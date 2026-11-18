"""Per-scope equipment-type catalog (``/scopes/{scope}/equipment-types...``).

Admin-authored, reusable definitions the cellbuilder places by slug. Equipment
types carry a bbox/mass/IFC-class/port list and an optional linked CAD asset
(under the hidden ``_equipment/`` prefix) from which the bbox and a preview GLB
are inferred by the ``equipment_bbox`` worker job.

Needs the app's storage and queue through :class:`~.deps.RestContext`. Extracted
from ``create_app``; see ``routes/__init__`` for the pattern. The fixed
``/cad``, ``/cad-from-scope`` and ``/infer-bbox`` leaves sit under
``{type_id}``, so no fixed path is shadowed by a parameterised sibling.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..scope import Scope
from .deps import RestContext, require_catalog_pool, rest_context, scope_from_path

router = APIRouter()

_CATALOG_CAD_EXTS = (".step", ".stp", ".ifc", ".glb", ".gltf", ".stl", ".obj", ".sat", ".xml")


async def _get_equipment_in_scope(pool, type_id: str, scope_obj: Scope) -> dict:
    try:
        row = await db_module.get_equipment_type(pool, type_id)
    except Exception:
        row = None
    if row is None or row["scope_kind"] != scope_obj.kind or (row["scope_id"] or None) != (scope_obj.id or None):
        raise HTTPException(status_code=404, detail="equipment type not found")
    return row


@router.get("/scopes/{scope}/equipment-types")
async def api_equipment_types_list(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    from ..catalog import equipment_preview_glb_key

    pool = require_catalog_pool(request)
    types = await db_module.list_equipment_types(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)
    for t in types:
        key = equipment_preview_glb_key(t["id"])
        t["preview_glb_key"] = key if await ctx.storage.exists(scope_obj, key) else None
    return JSONResponse({"equipment_types": types})


@router.post("/scopes/{scope}/equipment-types", status_code=201)
async def api_equipment_types_create(
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
    row = await db_module.create_equipment_type(
        pool,
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        slug=slug,
        name=name.strip(),
        description=desc if isinstance(desc, str) else None,
        created_by=user.sub,
    )
    if row is None:
        raise HTTPException(status_code=409, detail=f"an equipment type with slug {slug!r} already exists")
    return JSONResponse(row, status_code=201)


@router.get("/scopes/{scope}/equipment-types/{type_id}")
async def api_equipment_types_get(
    request: Request,
    type_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    from ..catalog import equipment_preview_glb_key

    pool = require_catalog_pool(request)
    row = await _get_equipment_in_scope(pool, type_id, scope_obj)
    key = equipment_preview_glb_key(type_id)
    out = {
        k: row[k]
        for k in ("id", "slug", "name", "description", "doc", "cad_key", "revision", "created_by", "updated_at")
    }
    out["preview_glb_key"] = key if await ctx.storage.exists(scope_obj, key) else None
    return JSONResponse(out)


@router.put("/scopes/{scope}/equipment-types/{type_id}")
async def api_equipment_types_update(
    request: Request,
    type_id: str,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    import asyncpg

    from ..catalog import slugify, validate_equipment_doc

    pool = require_catalog_pool(request)
    row = await _get_equipment_in_scope(pool, type_id, scope_obj)
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
        normalized = validate_equipment_doc(doc)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid equipment doc: {e}")
    try:
        new_rev = await db_module.update_equipment_type(
            pool,
            type_id,
            slug=slug,
            name=name.strip(),
            description=desc if isinstance(desc, str) else None,
            doc=normalized,
            base_revision=base_revision,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"an equipment type with slug {slug!r} already exists")
    if new_rev is None:
        current = await db_module.get_equipment_type(pool, type_id)
        raise HTTPException(
            status_code=409,
            detail={"message": "revision conflict", "current_revision": current["revision"] if current else None},
        )
    return JSONResponse({"id": row["id"], "revision": new_rev})


@router.delete("/scopes/{scope}/equipment-types/{type_id}")
async def api_equipment_types_delete(
    request: Request,
    type_id: str,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    pool = require_catalog_pool(request)
    await _get_equipment_in_scope(pool, type_id, scope_obj)
    ok = await db_module.archive_equipment_type(pool, type_id)
    if not ok:
        raise HTTPException(status_code=404, detail="equipment type not found")
    return JSONResponse({"status": "archived"})


@router.post("/scopes/{scope}/equipment-types/{type_id}/cad", status_code=201)
async def api_equipment_types_cad_upload(
    request: Request,
    type_id: str,
    filename: str = "",
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Attach a CAD/GLB asset to the type by direct body upload. The
    ``?filename=`` query param supplies the extension."""
    import os

    from ..catalog import equipment_cad_key

    pool = require_catalog_pool(request)
    await _get_equipment_in_scope(pool, type_id, scope_obj)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _CATALOG_CAD_EXTS:
        raise HTTPException(status_code=415, detail=f"unsupported CAD type {ext!r}; one of {_CATALOG_CAD_EXTS}")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")
    key = equipment_cad_key(type_id, ext)
    await ctx.storage.put_bytes(scope_obj, key, data)
    await db_module.set_equipment_type_cad(pool, type_id, key)
    return JSONResponse({"cad_key": key}, status_code=201)


@router.post("/scopes/{scope}/equipment-types/{type_id}/cad-from-scope", status_code=201)
async def api_equipment_types_cad_from_scope(
    request: Request,
    type_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Attach a CAD asset by copying an existing scope file into the type."""
    import os

    from ..catalog import equipment_cad_key

    pool = require_catalog_pool(request)
    await _get_equipment_in_scope(pool, type_id, scope_obj)
    body = await request.json()
    source = (body.get("source_key") or "").strip().lstrip("/")
    if not source:
        raise HTTPException(status_code=400, detail="source_key (str) is required")
    ext = os.path.splitext(source)[1].lower()
    if ext not in _CATALOG_CAD_EXTS:
        raise HTTPException(status_code=415, detail=f"unsupported CAD type {ext!r}")
    try:
        data = await ctx.storage.get_bytes(scope_obj, source)
    except Exception:
        raise HTTPException(status_code=404, detail=f"source not found in scope: {source}")
    key = equipment_cad_key(type_id, ext)
    await ctx.storage.put_bytes(scope_obj, key, data)
    await db_module.set_equipment_type_cad(pool, type_id, key)
    return JSONResponse({"cad_key": key}, status_code=201)


@router.post("/scopes/{scope}/equipment-types/{type_id}/infer-bbox")
async def api_equipment_types_infer_bbox(
    request: Request,
    type_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Enqueue a worker job to read the linked CAD asset, infer the bbox
    into the doc and render a preview GLB for the sidecar viewer."""
    from ..catalog import equipment_preview_glb_key

    pool = require_catalog_pool(request)
    row = await _get_equipment_in_scope(pool, type_id, scope_obj)
    if not row.get("cad_key"):
        raise HTTPException(status_code=400, detail="equipment type has no linked CAD asset to infer from")
    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="bbox inference disabled (no NATS configured)")
    # The type's Z-up assumption (default True = verbatim) travels to the
    # worker so inference measures/previews in the frame the user selected.
    cad_z_up = bool((row.get("doc") or {}).get("cad_z_up", True))
    derived_key = equipment_preview_glb_key(type_id)
    job = await ctx.queue.enqueue(
        f"_synthetic/equipment/{type_id}/bbox",
        target_format="equipment_bbox",
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        conversion_options={"type_id": type_id, "cad_key": row["cad_key"], "cad_z_up": cad_z_up},
        derived_key=derived_key,
    )
    return JSONResponse({"job_id": job.job_id, "derived_key": derived_key})
