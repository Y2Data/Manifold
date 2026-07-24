"""Deterministic string ID codecs between manifold-deck's integer primary
keys and the string-shaped IDs the vendored Omnigent frontend expects
(e.g. session/agent ids). One place for this so the mapping is never
done ad-hoc across route modules.
"""

from __future__ import annotations


def session_id(project_id: int) -> str:
    return f"proj_{project_id}"


def project_id_from_session(session_id_str: str) -> int | None:
    if not session_id_str.startswith("proj_"):
        return None
    try:
        return int(session_id_str[len("proj_") :])
    except ValueError:
        return None


def item_id(turn_id: int) -> str:
    return f"item_{turn_id}"


def agent_id(connection_id: int) -> str:
    return f"agent_{connection_id}"


def connection_id_from_agent(agent_id_str: str) -> int | None:
    if not agent_id_str.startswith("agent_"):
        return None
    try:
        return int(agent_id_str[len("agent_") :])
    except ValueError:
        return None


LOCAL_HOST_ID = "host_local"
LOCAL_RUNNER_ID = "runner_local"
