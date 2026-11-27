"""Admin user directory and worker-pool access management:
``GET /api/admin/users``, ``GET /api/admin/users/{user_sub}/worker-access``,
``PUT /api/admin/users/{user_sub}/worker-access``.

Extracted-module pattern; see ``routes/__init__``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from .deps import require_pool

router = APIRouter()


class WorkerAccessUpdate(BaseModel):
    enabled: bool = False
    allowed_capabilities: list[str] = Field(default_factory=list)


@router.get("/users")
async def admin_users_list(request: Request) -> JSONResponse:
    pool = require_pool(request)
    return JSONResponse({"users": await db_module.list_users(pool)})


@router.get("/users/{user_sub}/worker-access")
async def admin_get_user_worker_access(user_sub: str, request: Request) -> JSONResponse:
    pool = require_pool(request)
    policy = await db_module.get_user_worker_access(pool, user_sub)
    return JSONResponse({"user_sub": user_sub, **policy})


@router.put("/users/{user_sub}/worker-access")
async def admin_put_user_worker_access(
    user_sub: str,
    body: WorkerAccessUpdate,
    request: Request,
    admin: User = Depends(auth_module.require_admin),
) -> JSONResponse:
    pool = require_pool(request)
    policy = await db_module.set_user_worker_access(
        pool,
        user_sub,
        enabled=body.enabled,
        allowed_capabilities=body.allowed_capabilities,
        updated_by=admin.sub,
    )
    return JSONResponse({"user_sub": user_sub, **policy})
