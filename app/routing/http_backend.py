"""HTTP execution for api_key_http connections — Kimi (official or any
3rd-party/custom-endpoint mirror, OpenAI wire format), Claude on Azure AI
Foundry (Anthropic's native Messages API wire format, NOT OpenAI's shape —
Azure Foundry serves Claude through Anthropic's own wire format, verified
against Microsoft's own docs, not guessed), or any other OpenAI/Anthropic
-compatible endpoint the user points a connection at.

Unlike backends.py's subscription_cli path, this one holds a real API key —
fetched from the OS keychain per call, never logged, never stored in SQLite.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import keyring

from app.routing.backends import BackendError, BackendResult

_KEYRING_SERVICE = "manifold-deck"
_TIMEOUT_S = 180.0

# Real OpenAI-style function-calling tools for run_http_connection_with_tools
# — confirmed live that kimi-k3 (both via sfkey.cn and vectorengine.cn)
# returns genuine structured tool_calls for these, not just text describing
# what it would do. write_file is the only mutating one; it goes through
# the same human-approval flow Claude's own tool use does
# (permissions.request_decision) — read_file/list_files are auto-allowed
# (see permissions._AUTO_ALLOW_TOOLS).
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a text file within the project directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to the project root"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories within the project (or a subdirectory of it).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Subdirectory relative to the project root, or omit for the root",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrite an existing text file's full contents within the project directory. "
            "The file must already exist — this can't create new files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "content": {"type": "string", "description": "The new full file content"},
                },
                "required": ["path", "content"],
            },
        },
    },
]
_MAX_TOOL_ROUNDS = 10
_WRITE_PREVIEW_CHARS = 200


def store_api_key(ref: str, api_key: str) -> None:
    keyring.set_password(_KEYRING_SERVICE, ref, api_key)


def delete_api_key(ref: str) -> None:
    try:
        keyring.delete_password(_KEYRING_SERVICE, ref)
    except keyring.errors.PasswordDeleteError:
        pass  # already gone — fine, this runs on connection delete regardless


async def run_http_connection(prompt: str, connection: dict, timeout_s: float = _TIMEOUT_S) -> BackendResult:
    api_key = keyring.get_password(_KEYRING_SERVICE, connection["api_key_ref"])
    if not api_key:
        raise BackendError(f"no API key stored for connection {connection['name']!r}")

    base_url = connection["base_url"].rstrip("/")
    model = connection["default_model"]
    t0 = time.monotonic()

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        if connection["wire_api"] == "anthropic":
            resp = await client.post(
                f"{base_url}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if resp.status_code >= 400:
                raise BackendError(f"{connection['name']}: HTTP {resp.status_code} {resp.text[:300]}")
            data = resp.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            usage = data.get("usage") or {}
            input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        else:  # openai wire — Kimi official/3rd-party, or any OpenAI-compatible endpoint
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            )
            if resp.status_code >= 400:
                raise BackendError(f"{connection['name']}: HTTP {resp.status_code} {resp.text[:300]}")
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")

    return BackendResult(
        text=text,
        model=model,
        backend=connection["name"],
        latency_ms=int((time.monotonic() - t0) * 1000),
        cost_usd=None,  # unknown pricing for arbitrary custom endpoints — don't fabricate a number
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw=data,
    )


async def _execute_tool(name: str, arguments: dict, cwd: str, project_id: int, agent_name: str) -> str:
    from app.omnigent_compat.permissions import request_decision
    from app.project_files import read_file_text, write_file_text, walk_tree

    root = Path(cwd)
    if name == "read_file":
        text = read_file_text(root, arguments.get("path", ""))
        return text if text is not None else f"Error: could not read {arguments.get('path')!r} (not found, not a file, too large, or not valid UTF-8 text)."
    if name == "list_files":
        nodes = walk_tree(root, Path(arguments.get("path") or "."))
        return json.dumps([{"name": n["name"], "is_dir": n["is_dir"]} for n in nodes])
    if name == "write_file":
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        preview = content if len(content) <= _WRITE_PREVIEW_CHARS else content[:_WRITE_PREVIEW_CHARS] + f"... ({len(content)} chars total)"
        approved = await request_decision(project_id, "write_file", {"path": path, "content": preview}, cwd, agent_name=agent_name)
        if not approved:
            return "Error: the user denied permission to write this file."
        ok = write_file_text(root, path, content)
        return "OK: file written." if ok else f"Error: could not write {path!r} (not found or path escapes the project root)."
    return f"Error: unknown tool {name!r}."


async def run_http_connection_with_tools(
    prompt: str, connection: dict, cwd: str | None, project_id: int, timeout_s: float = _TIMEOUT_S
) -> BackendResult:
    """Real function-calling loop for OpenAI-wire connections (Kimi, or any
    other OpenAI-compatible endpoint) — confirmed live that kimi-k3 returns
    genuine structured tool_calls for read_file/list_files/write_file, not
    just text describing what it would do. Falls back to the plain
    single-shot run_http_connection (with build_file_context's pasted-in
    file content, at the router.py call site) for the anthropic wire or
    when there's no project directory to operate in — this loop needs a
    real cwd to read/write against."""
    if connection["wire_api"] != "openai" or not cwd:
        return await run_http_connection(prompt, connection, timeout_s=timeout_s)

    api_key = keyring.get_password(_KEYRING_SERVICE, connection["api_key_ref"])
    if not api_key:
        raise BackendError(f"no API key stored for connection {connection['name']!r}")

    base_url = connection["base_url"].rstrip("/")
    model = connection["default_model"]
    t0 = time.monotonic()
    messages: list[dict] = [{"role": "user", "content": prompt}]
    total_input_tokens = total_output_tokens = 0
    data: dict = {}

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for _ in range(_MAX_TOOL_ROUNDS):
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
                json={"model": model, "messages": messages, "tools": _TOOLS, "tool_choice": "auto"},
            )
            if resp.status_code >= 400:
                raise BackendError(f"{connection['name']}: HTTP {resp.status_code} {resp.text[:300]}")
            data = resp.json()
            usage = data.get("usage") or {}
            total_input_tokens += usage.get("prompt_tokens") or 0
            total_output_tokens += usage.get("completion_tokens") or 0
            message = data["choices"][0]["message"]
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                return BackendResult(
                    text=message.get("content") or "",
                    model=model,
                    backend=connection["name"],
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    cost_usd=None,
                    input_tokens=total_input_tokens or None,
                    output_tokens=total_output_tokens or None,
                    raw=data,
                )
            messages.append(message)
            for call in tool_calls:
                fn_name = call["function"]["name"]
                try:
                    fn_args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    fn_args = {}
                result_text = await _execute_tool(fn_name, fn_args, cwd, project_id, connection["name"])
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result_text})

    return BackendResult(
        text="[stopped: too many tool-call rounds without a final answer]",
        model=model,
        backend=connection["name"],
        latency_ms=int((time.monotonic() - t0) * 1000),
        cost_usd=None,
        input_tokens=total_input_tokens or None,
        output_tokens=total_output_tokens or None,
        raw=data,
    )
