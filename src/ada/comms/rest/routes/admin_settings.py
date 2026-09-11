"""Admin settings + CLI-token routes: ``GET``/``POST /api/admin/settings/{key}``,
``POST /api/admin/auth/cli-token`` and its revoke counterpart.

Needs the DB pool (through ``require_pool``) and, for the capability-
requirements setting specifically, the job queue (to mirror the value into
NATS KV — see :func:`~.deps.publish_capability_requirements`).

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from .deps import (
    CAPABILITY_REQUIREMENTS_SETTING,
    RestContext,
    publish_capability_requirements,
    require_pool,
    rest_context,
)

router = APIRouter()


@router.get("/settings/{key}")
async def admin_get_setting(
    key: str,
    request: Request,
) -> JSONResponse:
    """Generic key/value get from app_settings. Returns
    ``{"key": k, "value": v}`` with v=null when unset. Admin-only; keys in
    the ``public.`` namespace are additionally readable by any authenticated
    user via ``GET /api/settings/{key}``."""
    pool = require_pool(request)
    value = await db_module.get_setting(pool, key)
    return JSONResponse({"key": key, "value": value})


@router.post("/settings/{key}")
async def admin_set_setting(
    key: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Upsert a setting. Body: ``{"value": "..."}``. The audit trail
    for who-flipped-what lives on the row's ``updated_by`` column."""
    pool = require_pool(request)
    body = await request.json()
    if "value" not in body:
        raise HTTPException(status_code=400, detail="value required")
    value = "" if body["value"] is None else str(body["value"])
    await db_module.set_setting(pool, key, value, updated_by=user.sub)
    if key == CAPABILITY_REQUIREMENTS_SETTING:
        await publish_capability_requirements(ctx.queue, value)
    return JSONResponse({"key": key, "value": value})


@router.post("/auth/cli-token")
async def admin_mint_cli_token(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Mint a 30-day bearer token bound to the current OIDC
    identity. Returned once, never stored server-side. Use it as
    ``Authorization: Bearer <token>`` from CLI / pixi tasks."""
    config = request.app.state.auth_config
    token, exp = auth_module.mint_cli_token(user, config)
    return JSONResponse({"token": token, "expires_at": exp})


@router.post("/auth/cli-token/revoke")
async def admin_revoke_cli_tokens(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Revoke every CLI token previously minted for the current
    user by bumping the per-user cutoff. The OIDC bearer used for
    this request stays valid — only self-issued CLI tokens are
    affected."""
    pool = require_pool(request)
    revoked_at = await auth_module.revoke_cli_tokens(pool, user)
    return JSONResponse({"revoked_at": revoked_at})
