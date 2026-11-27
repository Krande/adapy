"""Admin execution policy (which engine runs each operation; see :mod:`..execution_policy`):

``GET/PUT /api/admin/execution-policy`` -- the global document,
``GET/PUT /api/admin/users/{user_sub}/execution-policy`` -- one user's override.

Extracted-module pattern; see ``routes/__init__``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import auth as auth_module
from .. import db as db_module
from .. import execution_policy as ep
from ..auth import User
from .deps import require_pool

router = APIRouter()


class ExecutionPolicyUpdate(BaseModel):
    #: The document; ``None`` (or omitted) resets the global policy to the defaults /
    #: clears a user's override. ``{}`` clears an override too.
    policy: dict[str, Any] | None = None


def _validated(doc: dict | None) -> dict:
    try:
        return ep.validate_policy(doc)
    except ep.ExecutionPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _vocabulary() -> dict:
    return {
        "operations": list(ep.OPERATIONS),
        "engines": list(ep.ENGINES),
        "defaults": ep.default_policy(),
    }


def _global_body(row: dict | None) -> dict:
    stored = ep.stored_policy(row["policy"]) if row else None
    return {
        "policy": ep.complete_policy(stored),
        "stored": row is not None,
        "updated_by": row["updated_by"] if row else None,
        "updated_at": row["updated_at"] if row else None,
        **_vocabulary(),
    }


@router.get("/execution-policy")
async def admin_get_execution_policy(request: Request) -> JSONResponse:
    pool = getattr(request.app.state, "db_pool", None)
    row = await db_module.get_execution_policy(pool) if pool is not None else None
    return JSONResponse(_global_body(row))


@router.put("/execution-policy")
async def admin_put_execution_policy(
    body: ExecutionPolicyUpdate,
    request: Request,
    admin: User = Depends(auth_module.require_admin),
) -> JSONResponse:
    pool = require_pool(request)
    if body.policy is None:
        await db_module.delete_execution_policy(pool)
        return JSONResponse(_global_body(None))
    doc = _validated(body.policy)
    row = await db_module.set_execution_policy(pool, doc, updated_by=admin.sub)
    return JSONResponse(_global_body(row))


async def _user_body(pool, user_sub: str, row: dict | None) -> dict:
    override = ep.stored_policy(row["policy"]) if row else {}
    global_row = await db_module.get_execution_policy(pool)
    return {
        "user_sub": user_sub,
        "override": override,
        "effective": ep.effective_policy(ep.stored_policy(global_row["policy"]) if global_row else None, override),
        "updated_by": row["updated_by"] if row else None,
        "updated_at": row["updated_at"] if row else None,
        **_vocabulary(),
    }


@router.get("/users/{user_sub}/execution-policy")
async def admin_get_user_execution_policy(user_sub: str, request: Request) -> JSONResponse:
    pool = require_pool(request)
    row = await db_module.get_user_execution_policy(pool, user_sub)
    return JSONResponse(await _user_body(pool, user_sub, row))


@router.put("/users/{user_sub}/execution-policy")
async def admin_put_user_execution_policy(
    user_sub: str,
    body: ExecutionPolicyUpdate,
    request: Request,
    admin: User = Depends(auth_module.require_admin),
) -> JSONResponse:
    pool = require_pool(request)
    doc = _validated(body.policy)
    if not doc:
        await db_module.delete_user_execution_policy(pool, user_sub)
        return JSONResponse(await _user_body(pool, user_sub, None))
    row = await db_module.set_user_execution_policy(pool, user_sub, doc, updated_by=admin.sub)
    return JSONResponse(await _user_body(pool, user_sub, row))
