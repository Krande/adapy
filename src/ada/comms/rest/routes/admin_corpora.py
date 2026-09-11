"""Admin corpus-management routes: ``GET``/``POST /api/admin/corpora``,
``PATCH``/``DELETE /api/admin/corpora/{slug}``.

Per-corpus file management reuses the existing ``/api/scopes/{scope}/files``
family — corpus is just another ``ScopeKind``, so listing / uploading /
downloading bytes flows through the same code paths as user / project
scopes (gated by ``is_admin`` via ``scope_can_access``); nothing here beyond
the DB pool.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from .deps import require_pool

router = APIRouter()

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@router.get("/corpora")
async def admin_corpora_list(request: Request) -> JSONResponse:
    pool = require_pool(request)
    rows = await db_module.list_corpora(pool)
    return JSONResponse({"corpora": rows})


@router.post("/corpora")
async def admin_corpora_create(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Create a new corpus.

    Body: ``{"slug": "cad-baseline", "name": "...",
             "description": "..." }``.

    ``slug`` is lowercase ASCII with hyphen separators — used in
    URLs (``corpus:cad-baseline``) and storage prefixes
    (``corpus/cad-baseline/``). Duplicate-against-live returns 409
    via the partial unique index on ``corpora.slug``.
    """
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    slug = (body.get("slug") or "").strip().lower()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip() or None
    if not slug or not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail=("slug must be lowercase ASCII with hyphen separators " "(e.g. 'cad-baseline')"),
        )
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        row = await db_module.create_corpus(
            pool,
            slug=slug,
            name=name,
            description=description,
            created_by=user.sub,
        )
    except Exception as exc:
        # asyncpg surfaces unique-violation via ``UniqueViolationError``;
        # treat that specifically as 409 instead of a generic 500.
        if exc.__class__.__name__ == "UniqueViolationError":
            raise HTTPException(
                status_code=409,
                detail=f"corpus slug {slug!r} already in use",
            ) from exc
        raise
    return JSONResponse(row, status_code=201)


@router.patch("/corpora/{slug}")
async def admin_corpora_update(slug: str, request: Request) -> JSONResponse:
    """Update a corpus's display name / description.

    Body: ``{"name": "...", "description": "..."}`` — name required
    non-empty, empty description clears it. The slug itself is
    immutable: it's baked into the storage prefix
    (``corpus/<slug>/``) and scope URLs, so renaming it would
    orphan the bucket bytes.
    """
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    row = await db_module.update_corpus(pool, slug, name=name, description=description)
    if row is None:
        raise HTTPException(status_code=404, detail=f"corpus {slug!r} not found")
    return JSONResponse(row)


@router.delete("/corpora/{slug}")
async def admin_corpora_archive(slug: str, request: Request) -> JSONResponse:
    """Soft-delete a corpus by slug. Storage bytes are NOT wiped —
    the operator handles that out-of-band if disk pressure
    matters. The slug becomes available for reuse immediately
    because the uniqueness index is partial-on-live."""
    pool = require_pool(request)
    ok = await db_module.archive_corpus(pool, slug)
    if not ok:
        raise HTTPException(status_code=404, detail=f"corpus {slug!r} not found")
    return JSONResponse({"slug": slug, "archived": True})
