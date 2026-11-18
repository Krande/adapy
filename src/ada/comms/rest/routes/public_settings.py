"""``GET /settings/{key}``: the ``public.*`` settings any signed-in user may read.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern. Writes stay
on the admin router (routes/admin_settings.py).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import db as db_module

router = APIRouter()


# Settings whose key begins with this prefix are readable by ANY
# authenticated user; every other key in app_settings stays admin-only.
# Writes are always admin-only (POST /api/admin/settings/{key}) — this is a
# read window, not a public key/value store.
#
# It exists because a plugin often has to publish one small piece of
# deployment configuration that its UI needs for EVERY user, not just
# admins: which feature is enabled where, which external catalog a project
# is bound to. Without this each such plugin would need its own core
# endpoint, and core would end up naming plugins. The prefix is the whole
# contract: an admin opting a key into `public.` is opting into it being
# world-readable within the deployment.
PUBLIC_SETTING_PREFIX = "public."


@router.get("/settings/{key}")
async def api_get_public_setting(key: str, request: Request) -> JSONResponse:
    """Read a setting from the publicly-readable namespace. Returns
    ``{"key": k, "value": v}`` with v=null when unset, exactly like the admin
    getter. 403 for any key outside the namespace, so this can never be used
    to read an admin-only setting."""
    if not key.startswith(PUBLIC_SETTING_PREFIX):
        raise HTTPException(
            status_code=403,
            detail=f"only {PUBLIC_SETTING_PREFIX}* settings are readable here",
        )
    # A server with no database has no settings: every key reads as unset (200, value
    # null), so the viewer's per-load reads stay quiet.
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return JSONResponse({"key": key, "value": None})
    value = await db_module.get_setting(pool, key)
    return JSONResponse({"key": key, "value": value})
