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

    A user turn is one MessageData item. An assistant turn becomes a
    RoutingDecisionData item (surfacing manifold's own tier/backend/cost —
    a near-exact fit for what this Omnigent item type exists for) followed
    by the MessageData item with the actual response text.
    """
    response_id = turn.get("fanout_group") or ids.item_id(turn["id"])
    if turn["role"] == "user":
        return [
            {
                "id": ids.item_id(turn["id"]),
                "type": "message",
                "status": "completed",
                "response_id": response_id,
                "created_at": int(turn["ts"]),
                "created_by": None,
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": turn["content"]}],
                    "model": None,
                },
            }
        ]

    tier = turn.get("tier")
    items = []
    if tier:
        items.append(
            {
                "id": f"route_{turn['id']}",
                "type": "routing_decision",
                "status": "completed",
                "response_id": response_id,
                "created_at": int(turn["ts"]),
                "created_by": None,
                "data": {
                    "model": turn.get("model") or "unknown",
                    "applied": True,
                    "rationale": _TIER_RATIONALE.get(tier, f"Classified as {tier}."),
                    "agent": turn.get("backend"),
                },
            }
        )
    items.append(
        {
            "id": ids.item_id(turn["id"]),
            "type": "message",
            "status": "completed",
            "response_id": response_id,
            "created_at": int(turn["ts"]),
            "created_by": None,
            "data": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": turn["content"]}],
                "model": turn.get("model"),
            },
        }
    )
    return items
