"""GET /v1/info and /v1/me — the vendored frontend reads these at boot to
decide which chrome to even register (accounts, managed sandboxes, sharing,
smart routing). Keeping every advanced flag off means the SPA never calls
the endpoints backing those features, which is what makes vendoring this
frontend tractable without reimplementing all 64 of Omnigent's own routes.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/v1/info")
async def info():
    return {
        "accounts_enabled": False,
        "single_user": True,
        "login_url": None,
        "needs_setup": False,
        "databricks_features": False,
        "managed_sandboxes_enabled": False,
        "sandbox_provider": None,
        "sharing_mode": "off",
        "public_sharing_enabled": False,
        "server_version": "manifold-deck-compat-0.1",
        "smart_routing_enabled": False,
    }


@router.get("/v1/me")
async def me():
    return {"user_id": "local", "is_admin": True}
