from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routing.http_backend import delete_api_key, store_api_key
from app.store import (
    add_connection,
    delete_connection,
    get_connection,
    list_connections,
    set_default_connection,
)

router = APIRouter(prefix="/api/connections")


class NewConnection(BaseModel):
    name: str
    provider: str
    kind: str  # "subscription_cli" | "api_key_http"
    cli: str | None = None  # subscription_cli only: "claude" | "codex"
    base_url: str | None = None  # api_key_http only
    wire_api: str | None = None  # api_key_http only: "openai" | "anthropic"
    api_key: str | None = None  # api_key_http only — raw key, stored via keyring, never in the DB
    default_model: str | None = None


@router.get("")
async def api_list_connections():
    # Never expose api_key_ref (a keyring lookup key that names a real secret's location).
    return [{k: v for k, v in c.items() if k != "api_key_ref"} for c in list_connections()]


@router.post("")
async def api_add_connection(body: NewConnection):
    if body.kind not in ("subscription_cli", "api_key_http"):
        raise HTTPException(400, f"invalid kind: {body.kind}")
    if body.kind == "subscription_cli" and body.cli not in ("claude", "codex"):
        raise HTTPException(400, "subscription_cli connections need cli: 'claude' or 'codex'")
    if body.kind == "api_key_http":
        if not body.base_url or body.wire_api not in ("openai", "anthropic"):
            raise HTTPException(400, "api_key_http connections need base_url and wire_api")
        if not body.api_key:
            raise HTTPException(400, "api_key_http connections need an api_key")
        if not body.default_model:
            raise HTTPException(400, "api_key_http connections need a default_model")

    api_key_ref = None
    if body.kind == "api_key_http":
        api_key_ref = f"conn-{uuid.uuid4().hex[:12]}"
        store_api_key(api_key_ref, body.api_key)

    connection = add_connection(
        {
            "name": body.name,
            "provider": body.provider,
            "kind": body.kind,
            "cli": body.cli,
            "base_url": body.base_url,
            "wire_api": body.wire_api,
            "api_key_ref": api_key_ref,
            "default_model": body.default_model,
            "enabled": 1,
        }
    )
    connection.pop("api_key_ref", None)
    return connection


@router.post("/{connection_id}/set-default")
async def api_set_default_connection(connection_id: int):
    if get_connection(connection_id) is None:
        raise HTTPException(404, "connection not found")
    connection = set_default_connection(connection_id)
    connection.pop("api_key_ref", None)
    return connection


@router.delete("/{connection_id}")
async def api_delete_connection(connection_id: int):
    connection = get_connection(connection_id)
    if connection is None:
        raise HTTPException(404, "connection not found")
    if connection["api_key_ref"]:
        delete_api_key(connection["api_key_ref"])
    delete_connection(connection_id)
    return {"deleted": connection_id}
