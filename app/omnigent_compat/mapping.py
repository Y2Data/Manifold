"""Pure functions mapping manifold-deck's own data (dicts from app/store.py)
into the JSON shapes the vendored Omnigent frontend expects, per the schemas
pulled from the real Omnigent server's /openapi.json. No manifold-deck
tables/behavior are changed here — this only translates outward.
"""

from __future__ import annotations

from app.omnigent_compat import ids

_TIER_RATIONALE = {
    "SIMPLE": "Classified as simple — routed to the fast/cheap tier.",
    "MEDIUM": "Classified as medium complexity — routed to the mid tier.",
    "COMPLEX": "Classified as complex — fanned out to every default connection.",
}

# The exact key set the real server's GET /v1/hosts/{id} returns under
# configured_harnesses (verified via curl against the live instance) — the
# frontend's new-session picker uses this to decide which agents/harnesses
# are even offerable on a given host. manifold-deck has no such fixed
# catalog, so this list exists purely to match the real shape; every key
# defaults false except the small subset actually backed by a configured
# connection (see _HARNESS_ALIASES / build_configured_harnesses below).
_KNOWN_HARNESS_KEYS = [
    "native-antigravity", "pi", "native-cursor", "kimi-code", "native-kiro", "copilot",
    "kimi-native", "codex-native", "claude-sdk", "kimi", "native-hermes", "qwen-code",
    "claude_sdk", "hermes-native", "antigravity", "agents_sdk", "goose-native", "qwen-native",
    "codex", "native-codex", "open-responses", "native-kimi", "native-opencode", "cursor-native",
    "goose", "cursor", "antigravity-native", "openai-agents", "native-qwen", "openai-agents-sdk",
    "hermes", "pi-native", "github-copilot", "qwen", "native-claude", "opencode-native",
    "google-antigravity", "acp", "native-pi", "agy", "claude", "opencode", "native-goose",
    "kiro-native", "claude-native",
]

# manifold-deck cli name -> the subset of the real catalog's aliases that
# genuinely apply (native CLI usage only — manifold shells out to `claude
# -p`/`codex exec` directly, not via an SDK, so the *-sdk/agents_sdk
# variants stay false even when claude/codex are configured).
_HARNESS_ALIASES = {
    "claude": {"claude", "claude-native", "native-claude"},
    "codex": {"codex", "codex-native", "native-codex"},
    "kimi": {"kimi", "kimi-native", "kimi-code", "native-kimi"},
}


def build_configured_harnesses(connections: list[dict]) -> dict[str, bool]:
    configured_clis = {c.get("cli") for c in connections if c["kind"] == "subscription_cli"}
    enabled: set[str] = set()
    for cli in configured_clis:
        enabled |= _HARNESS_ALIASES.get(cli, set())
    return {key: key in enabled for key in _KNOWN_HARNESS_KEYS}


def connection_to_agent(connection: dict) -> dict:
    """-> AgentObject. `harness` is the frontend's signal for which kind of
    agent this is (it uses this to pick an icon/label) — pass through the
    CLI name for subscription_cli connections (e.g. "claude", "codex", a
    custom CLI name), or "http" for api_key_http connections."""
    harness = connection.get("cli") if connection["kind"] == "subscription_cli" else "http"
    return {
        "id": ids.agent_id(connection["id"]),
        "object": "agent",
        "name": connection["name"],
        "version": 1,
        "description": f"{connection['provider']} · {connection['kind']}",
        "created_at": int(connection["created_at"]),
        "updated_at": None,
        "harness": harness,
        "mcp_servers": [],
        "mcp_servers_editable": False,
        "policies": [],
        "skills": [],
        "terminals": [],
        "builtin": False,
    }


def _session_common(project: dict, default_connection: dict | None) -> dict:
    return {
        "id": ids.session_id(project["id"]),
        "agent_id": ids.agent_id(default_connection["id"]) if default_connection else "agent_none",
        "agent_name": default_connection["name"] if default_connection else None,
        "status": "idle",  # manifold has no mid-turn session concept once route() returns
        "created_at": int(project["created_at"]),
        "title": project["name"],
        "labels": {},
        "runner_id": ids.LOCAL_RUNNER_ID,
        "host_id": ids.LOCAL_HOST_ID,
        "runner_online": True,
        "host_online": True,
        "reasoning_effort": None,
        "permission_level": None,
        "owner": "local",
        "external_session_id": None,
        "pending_elicitations_count": 0,
        "workspace": project["cwd"],
        "git_branch": None,
        "archived": False,
        "comments_count": 0,
        "comments_updated_at": None,
        "viewer_last_seen": None,
        "viewer_unread": False,
        "search_snippet": None,
        "parent_session_id": None,
    }


def project_to_session_list_item(project: dict, default_connection: dict | None) -> dict:
    return {**_session_common(project, default_connection), "updated_at": int(project["last_used_at"])}


def project_to_session_response(project: dict, turns: list[dict], default_connection: dict | None) -> dict:
    items: list[dict] = []
    for turn in turns:
        items.extend(turn_to_conversation_items(turn))
    return {
        **_session_common(project, default_connection),
        "background_task_count": 0,
        "host_resumable": False,
        "items": items,
        "sub_agent_name": None,
        "parent_session_id": None,
        "root_conversation_id": None,
        "llm_model": default_connection.get("default_model") if default_connection else None,
    }


def turn_to_conversation_items(turn: dict) -> list[dict]:
    """turns row -> list of ConversationItem dicts.

    A user turn is one message item. An assistant turn becomes a
    routing_decision item (surfacing manifold's own tier/backend/cost — a
    near-exact fit for what this Omnigent item type exists for) followed
    by the message item with the actual response text.

    Shape verified against the real Omnigent server's actual
    `/v1/sessions/{id}/items` responses (`curl` against the live instance)
    rather than trusting the OpenAPI schema alone — the runtime wire format
    flattens each item's typed fields directly onto the item (no nested
    `data` wrapper, despite `components.schemas.ConversationItem` in the
    spec showing one). E.g. a real message item is exactly
    `{id, response_id, type:"message", status, role, content, model}`.
    """
    response_id = turn.get("fanout_group") or ids.item_id(turn["id"])
    if turn["role"] == "user":
        return [
            {
                "id": ids.item_id(turn["id"]),
                "response_id": response_id,
                "type": "message",
                "status": "completed",
                "role": "user",
                "content": [{"type": "input_text", "text": turn["content"]}],
            }
        ]

    tier = turn.get("tier")
    items = []
    if tier:
        items.append(
            {
                "id": f"route_{turn['id']}",
                "response_id": response_id,
                "type": "routing_decision",
                "status": "completed",
                "model": turn.get("model") or "unknown",
                "applied": True,
                "rationale": _TIER_RATIONALE.get(tier, f"Classified as {tier}."),
                "agent": turn.get("backend"),
            }
        )
    items.append(
        {
            "id": ids.item_id(turn["id"]),
            "response_id": response_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": turn["content"]}],
            "model": turn.get("model"),
        }
    )
    return items
