"""Per-scope system-template catalog (``/scopes/{scope}/system-templates...``):
admin-authored, reusable system definitions the cellbuilder places by slug.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..scope import Scope
from .deps import require_catalog_pool, scope_from_path

router = APIRouter()


async def _get_system_in_scope(pool, template_id: str, scope_obj: Scope) -> dict:
    try:
        row = await db_module.get_system_template(pool, template_id)
    except Exception:
        row = None
    if row is None or row["scope_kind"] != scope_obj.kind or (row["scope_id"] or None) != (scope_obj.id or None):
        raise HTTPException(status_code=404, detail="system template not found")
    return row


@router.get("/scopes/{scope}/system-templates")
async def api_system_templates_list(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    pool = require_catalog_pool(request)
    templates = await db_module.list_system_templates(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)
    return JSONResponse({"system_templates": templates})


@router.post("/scopes/{scope}/system-templates", status_code=201)
async def api_system_templates_create(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    from ..catalog import slugify

    pool = require_catalog_pool(request)
    body = await request.json()
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="name (str) is required")
    slug = slugify(body.get("slug") or name)
    if not slug:
        raise HTTPException(status_code=400, detail="could not derive a slug from name/slug")
    desc = body.get("description")
    row = await db_module.create_system_template(
        pool,
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        slug=slug,
        name=name.strip(),
        description=desc if isinstance(desc, str) else None,
        created_by=user.sub,
    )
    if row is None:
        raise HTTPException(status_code=409, detail=f"a system template with slug {slug!r} already exists")
    return JSONResponse(row, status_code=201)


@router.get("/scopes/{scope}/system-templates/{template_id}")
async def api_system_templates_get(
    request: Request,
    template_id: str,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    pool = require_catalog_pool(request)
    row = await _get_system_in_scope(pool, template_id, scope_obj)
    return JSONResponse(
        {k: row[k] for k in ("id", "slug", "name", "description", "doc", "revision", "created_by", "updated_at")}
    )


@router.put("/scopes/{scope}/system-templates/{template_id}")
async def api_system_templates_update(
    request: Request,
    template_id: str,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    import asyncpg

    from ..catalog import slugify, validate_system_doc

    pool = require_catalog_pool(request)
    row = await _get_system_in_scope(pool, template_id, scope_obj)
    body = await request.json()
    name = body.get("name")
    doc = body.get("doc")
    base_revision = body.get("base_revision")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="name (str) is required")
    if not isinstance(doc, dict):
        raise HTTPException(status_code=400, detail="doc (object) is required")
    if not isinstance(base_revision, int):
        raise HTTPException(status_code=400, detail="base_revision (int) is required")
    slug = slugify(body.get("slug") or name)
    if not slug:
        raise HTTPException(status_code=400, detail="could not derive a slug")
    desc = body.get("description")
    try:
        normalized = validate_system_doc(doc)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid system doc: {e}")
    try:
        new_rev = await db_module.update_system_template(
            pool,
            template_id,
            slug=slug,
            name=name.strip(),
            description=desc if isinstance(desc, str) else None,
            doc=normalized,
            base_revision=base_revision,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"a system template with slug {slug!r} already exists")
    if new_rev is None:
        current = await db_module.get_system_template(pool, template_id)
        raise HTTPException(
            status_code=409,
            detail={"message": "revision conflict", "current_revision": current["revision"] if current else None},
        )
    return JSONResponse({"id": row["id"], "revision": new_rev})


@router.delete("/scopes/{scope}/system-templates/{template_id}")
async def api_system_templates_delete(
    request: Request,
    template_id: str,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    pool = require_catalog_pool(request)
    await _get_system_in_scope(pool, template_id, scope_obj)
    ok = await db_module.archive_system_template(pool, template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="system template not found")
    return JSONResponse({"status": "archived"})
