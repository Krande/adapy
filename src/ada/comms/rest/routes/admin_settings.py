"""Admin settings + CLI-token routes: ``GET``/``POST /api/admin/settings/{key}``,
``POST /api/admin/auth/cli-token`` and its revoke-all counterpart, and the
per-token list and revoke under ``/api/admin/auth/cli-tokens``.

Needs the DB pool (through ``require_pool``) and, for the capability-
requirements setting specifically, the job queue (to mirror the value into
NATS KV — see :func:`~.deps.publish_capability_requirements`).

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.assets.provider_labels import (
    PROVIDER_LABELS_SETTING,
    ProviderLabelError,
    normalise_provider_aliases,
)

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
    if key == PROVIDER_LABELS_SETTING:
        # Validated and normalised here, the one place it is written: a display name the read path
        # would silently drop is refused with the reason instead.
        try:
            value = normalise_provider_aliases(value)
        except ProviderLabelError as exc:
            raise HTTPException(status_code=400, detail=f"{key}: {exc}") from exc
    await db_module.set_setting(pool, key, value, updated_by=user.sub)
    if key == CAPABILITY_REQUIREMENTS_SETTING:
        await publish_capability_requirements(ctx.queue, value)
    return JSONResponse({"key": key, "value": value})


_LABEL_MAX = 120


def _label_from(body: object) -> str | None:
    """Optional free-text label from a JSON body: what the token is for."""
    if not isinstance(body, dict) or body.get("label") is None:
        return None
    label = str(body["label"]).strip()
    if len(label) > _LABEL_MAX:
        raise HTTPException(status_code=400, detail=f"label longer than {_LABEL_MAX} characters")
    return label or None


async def _json_body(request: Request) -> object:
    # The mint endpoint predates its body; an empty POST must keep working.
    if not await request.body():
        return None
    try:
        return await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc


@router.post("/auth/cli-token")
async def admin_mint_cli_token(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Mint a 30-day bearer token bound to the current OIDC identity.

    Body: ``{"label": "..."}``, optional. The token is returned once and never
    stored; its record (owner, label, hint, expiry) is what the token list
    shows. Use it as ``Authorization: Bearer <token>`` from CLI / pixi tasks."""
    label = _label_from(await _json_body(request))
    config = request.app.state.auth_config
    token, exp = auth_module.mint_cli_token(user, config)
    pool = getattr(request.app.state, "db_pool", None)
    record = await auth_module.record_cli_token(pool, token, label=label, issued_by=user.sub)
    return JSONResponse({"token": token, "expires_at": exp, **record})


@router.get("/auth/cli-tokens")
async def admin_list_cli_tokens(
    request: Request,
    include_inactive: bool = False,
    sub: str | None = None,
) -> JSONResponse:
    """Every issued CLI token on the deployment, newest first — people's and
    CI bots' alike. ``include_inactive`` adds revoked and expired ones.

    ``tracked_since`` is when per-token records started. Tokens issued before
    it are not listed; they lapse at most 30 days after it, and "revoke all"
    for their owner still kills them sooner."""
    pool = require_pool(request)
    tokens = await db_module.list_cli_tokens(pool, include_inactive=include_inactive, sub=sub or None)
    tracked_since = await db_module.cli_tokens_tracked_since(pool)
    return JSONResponse({"tokens": tokens, "tracked_since": tracked_since})


@router.post("/auth/cli-tokens/{jti}/revoke")
async def admin_revoke_cli_token(
    jti: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Revoke one token, whoever it belongs to. Its next use is refused; the
    owner's other tokens are untouched."""
    pool = require_pool(request)
    row = await db_module.revoke_cli_token(pool, jti, revoked_by=user.sub)
    if row is None:
        raise HTTPException(status_code=404, detail="no such token")
    return JSONResponse(row)


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
