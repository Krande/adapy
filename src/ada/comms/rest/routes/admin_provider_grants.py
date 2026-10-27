"""Admin -> Providers -> Access: which scope may read which provider dataset (migration 042).

``GET    /api/admin/provider-grants?target_scope=&provider=&collection=``
                                  the grants, each filter optional; ``collection`` also lists the
                                  provider-wide grants that cover it
``PUT    /api/admin/provider-grants``  ``{target_scope, provider, collection?, attributes?, geometry?,
                                  request?, note?}`` -- create the grant or replace its permissions
                                  (defaults: attributes and geometry on, request off)
``DELETE /api/admin/provider-grants``  ``{target_scope, provider, collection?}`` -- revoke one grant

``target_scope`` is a storage prefix (``shared``, ``projects/<uuid>``, ``users/<sub>``), as the
database reports scopes; a project must exist. ``collection`` absent or null is every collection of
the provider, the ones published later included. A grant copies nothing: it says who may read the
dataset where it lies, in the provider store (see ``db/provider_grants.py``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..db.provider_grants import PERMISSIONS
from ..scope import Scope
from .deps import require_pool

router = APIRouter()

#: The kinds of scope a dataset may be granted to. Not a corpus (nothing reads provider data from
#: one) and not the provider store itself.
GRANT_TARGET_KINDS = ("shared", "project", "user")


async def _body(request: Request) -> dict:
    body = await request.json() if await request.body() else {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    return body


def parse_grant_ref(body: dict) -> dict:
    """``{target_scope, provider, collection}`` from a body, shape-checked (400s). ``collection``
    absent, null or blank is None: every collection of the provider."""
    ref: dict = {}
    for name in ("target_scope", "provider"):
        v = body.get(name)
        if not isinstance(v, str) or not v.strip():
            raise HTTPException(status_code=400, detail=f"{name!r} is required (a non-empty string)")
        ref[name] = v.strip()
    raw = body.get("collection")
    if raw is not None and not isinstance(raw, str):
        raise HTTPException(status_code=400, detail="'collection' must be a string or null")
    ref["collection"] = raw.strip().lower() if isinstance(raw, str) and raw.strip() else None
    try:
        target = Scope.from_prefix(ref["target_scope"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target.kind not in GRANT_TARGET_KINDS:
        raise HTTPException(status_code=400, detail=f"a provider dataset cannot be granted to a {target.kind} scope")
    if target.kind == "project":
        try:
            uuid.UUID(target.id or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="a project scope is projects/<uuid>") from exc
    return ref


def parse_permissions(body: dict) -> dict:
    """The permission flags of a grant body: each optional, a real boolean when given."""
    defaults = {"attributes": True, "geometry": True, "request": False}
    out = {}
    for name in PERMISSIONS:
        v = body.get(name, defaults[name])
        if not isinstance(v, bool):
            raise HTTPException(status_code=400, detail=f"{name!r} must be true or false")
        out[name] = v
    if not any(out.values()):
        raise HTTPException(status_code=400, detail="a grant needs at least one permission; revoke it instead")
    return out


@router.get("/provider-grants")
async def admin_provider_grants(
    request: Request,
    target_scope: str | None = None,
    provider: str | None = None,
    collection: str | None = None,
) -> JSONResponse:
    pool = require_pool(request)
    grants = await db_module.list_provider_grants(
        pool, target_scope=target_scope or None, provider=provider or None, collection=collection or None
    )
    return JSONResponse({"grants": grants})


@router.put("/provider-grants")
async def admin_provider_grant_put(request: Request, user: User = Depends(auth_module.current_user)) -> JSONResponse:
    pool = require_pool(request)
    body = await _body(request)
    ref = parse_grant_ref(body)
    perms = parse_permissions(body)
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=400, detail="'note' must be a string")
    target = Scope.from_prefix(ref["target_scope"])
    if target.kind == "project" and not await db_module.project_exists(pool, target.id):
        raise HTTPException(status_code=404, detail=f"no project {target.id!r}")
    grant = await db_module.upsert_provider_grant(pool, **ref, **perms, granted_by=user.sub, note=note)
    logger.info(
        "provider grant by %s: %s/%s -> %s %s",
        user.sub,
        ref["provider"],
        ref["collection"] or "*",
        ref["target_scope"],
        ",".join(p for p in PERMISSIONS if perms[p]),
    )
    return JSONResponse({"grant": grant})


@router.delete("/provider-grants")
async def admin_provider_grant_delete(request: Request, user: User = Depends(auth_module.current_user)) -> JSONResponse:
    pool = require_pool(request)
    ref = parse_grant_ref(await _body(request))
    removed = await db_module.revoke_provider_grant(pool, **ref)
    if removed:
        logger.info(
            "provider grant revoked by %s: %s/%s -> %s",
            user.sub,
            ref["provider"],
            ref["collection"] or "*",
            ref["target_scope"],
        )
    return JSONResponse({"removed": removed})
