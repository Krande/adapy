"""User-facing project routes: ``GET /api/me`` (profile + scope picker,
including the caller's project memberships and, for admins, corpus
scopes) and ``GET /api/projects`` (the caller's own project list, the
bare-bones sibling ``/me`` also carries inline).

Admin project management (create / archive / member CRUD / CI-bot
provisioning) lives in :mod:`~.admin_projects` instead, mounted under
the admin router.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User

router = APIRouter()


@router.get("/me")
async def api_me(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    # Lazy upsert on first authenticated hit so the `users` table
    # tracks who has actually signed in. No-op when DB is off.
    pool = getattr(request.app.state, "db_pool", None)
    projects: list[dict] = []
    if pool is not None:
        await db_module.upsert_user(pool, user.sub, user.email, user.display_name)
        for p in await db_module.list_user_projects(pool, user.sub):
            projects.append({"id": p.id, "slug": p.slug, "name": p.name, "role": p.role})

    # Scopes the caller can pick from in the SPA's project picker.
    # Order matters — first entry is the default landing scope.
    scopes: list[dict] = [
        {"kind": "user", "id": "me", "name": "Personal"},
        {"kind": "shared", "id": None, "name": "Shared"},
    ]
    for p in projects:
        scopes.append({"kind": "project", "id": p["id"], "name": p["name"]})

    # Corpus scopes are admin-only (scope_can_access gates them). Advertise
    # them here so an admin can browse + visualise corpus files straight from
    # the main storage panel — the same list/convert flow every other scope
    # uses. Non-admins never see them; the backend rejects the scope anyway.
    if user.is_admin and pool is not None:
        try:
            for c in await db_module.list_corpora(pool):
                scopes.append({"kind": "corpus", "id": c["slug"], "name": c["name"]})
        except Exception:
            logger.exception("api_me: listing corpora failed")

    return JSONResponse(
        {
            "sub": user.sub,
            "email": user.email,
            "displayName": user.display_name,
            "isAdmin": user.is_admin,
            "scopes": scopes,
            "projects": projects,
        }
    )


@router.get("/projects")
async def api_projects(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return JSONResponse({"projects": []})
    rows = await db_module.list_user_projects(pool, user.sub)
    return JSONResponse({"projects": [{"id": p.id, "slug": p.slug, "name": p.name, "role": p.role} for p in rows]})
