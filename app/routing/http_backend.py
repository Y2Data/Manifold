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

import time

import httpx
import keyring

from app.routing.backends import BackendError, BackendResult

_KEYRING_SERVICE = "manifold-deck"
_TIMEOUT_S = 180.0


def store_api_key(ref: str, api_key: str) -> None:
    keyring.set_password(_KEYRING_SERVICE, ref, api_key)


def delete_api_key(ref: str) -> None:
    try:
        keyring.delete_password(_KEYRING_SERVICE, ref)
    except keyring.errors.PasswordDeleteError:
        pass  # already gone — fine, this runs on connection delete regardless


async def run_http_connection(prompt: str, connection: dict) -> BackendResult:
    api_key = keyring.get_password(_KEYRING_SERVICE, connection["api_key_ref"])
    if not api_key:
        raise BackendError(f"no API key stored for connection {connection['name']!r}")

    base_url = connection["base_url"].rstrip("/")
    model = connection["default_model"]
    t0 = time.monotonic()

    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
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
