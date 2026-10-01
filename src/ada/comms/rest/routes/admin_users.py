"""Admin user directory: ``GET /api/admin/users``.

Read-only. Membership is still managed per project (see
:mod:`~.admin_projects`); this is the other axis — start from a person and see
where they belong, then jump to their audit trail (the admin panel links each
row to the audit log filtered on ``user_sub``, which ``GET /api/admin/audit``
already supports).

Extracted-module pattern; see ``routes/__init__``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import db as db_module
from .deps import require_pool

router = APIRouter()


@router.get("/users")
async def admin_users_list(request: Request) -> JSONResponse:
    pool = require_pool(request)
    return JSONResponse({"users": await db_module.list_users(pool)})
