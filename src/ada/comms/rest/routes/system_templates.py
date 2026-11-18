"""Per-scope system-template catalog (``/scopes/{scope}/system-templates...``):
admin-authored, reusable system definitions the cellbuilder places by slug.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from ..auth import User
from ..scope import Scope
from .deps import RestContext, require_catalog_store, rest_context, scope_from_path

router = APIRouter()


def _catalogs(request: Request, ctx: RestContext):
    # Postgres, or the scope's storage files on a local viewer (catalog_store.py).
    return require_catalog_store(request, ctx.storage, ctx.queue)


async def _get_system_in_scope(store, template_id: str, scope_obj: Scope) -> dict:
    row = await store.get_system(scope_obj, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="system template not found")
    return row


@router.get("/scopes/{scope}/system-templates")
async def api_system_templates_list(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    templates = await _catalogs(request, ctx).list_systems(scope_obj)
    return JSONResponse({"system_templates": templates})


@router.post("/scopes/{scope}/system-templates", status_code=201)
async def api_system_templates_create(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    from ..catalog import slugify

    store = _catalogs(request, ctx)
    body = await request.json()
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="name (str) is required")
    slug = slugify(body.get("slug") or name)
    if not slug:
        raise HTTPException(status_code=400, detail="could not derive a slug from name/slug")
    desc = body.get("description")
    row = await store.create_system(
        scope_obj,
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
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    row = await _get_system_in_scope(_catalogs(request, ctx), template_id, scope_obj)
    return JSONResponse(
        {k: row[k] for k in ("id", "slug", "name", "description", "doc", "revision", "created_by", "updated_at")}
    )


@router.put("/scopes/{scope}/system-templates/{template_id}")
async def api_system_templates_update(
    request: Request,
    template_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    from ..catalog import slugify, validate_system_doc
    from ..catalog_store import CatalogSlugTaken

    store = _catalogs(request, ctx)
    row = await _get_system_in_scope(store, template_id, scope_obj)
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
        new_rev = await store.update_system(
            scope_obj,
            template_id,
            slug=slug,
            name=name.strip(),
            description=desc if isinstance(desc, str) else None,
            doc=normalized,
            base_revision=base_revision,
        )
    except CatalogSlugTaken:
        raise HTTPException(status_code=409, detail=f"a system template with slug {slug!r} already exists")
    if new_rev is None:
        current = await store.get_system(scope_obj, template_id)
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
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    store = _catalogs(request, ctx)
    await _get_system_in_scope(store, template_id, scope_obj)
    ok = await store.archive_system(scope_obj, template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="system template not found")
    return JSONResponse({"status": "archived"})
