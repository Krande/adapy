"""Procedural cell-model routes: ``/api/scopes/{scope}/procedural-models*``
and ``procedural-templates`` — the viewer cellbuilder's document CRUD, the
compile / preview / export / import job enqueues, the compile-log and take-off
readers, and the catalog ``sync``/``resync`` writers that live under the same
path prefix.

One postgres row per model; the doc (spaces/equipments/openings as
ada.topology pydantic dumps) is the single source of truth. Commit and
compile are separate: PUT bumps the revision under optimistic
concurrency, POST /compile enqueues a procedural_build worker job whose
GLB lands at _procedural/{id}/r{rev}.glb (hidden from file listings).

Included by ``create_app`` right after the catalog-listing router
(``routes/plugins.py``): the fixed-segment ``procedural-models/<catalog>``
listings must register before ``procedural-models/{model_id}``. Extracted
from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..job_transport import JobTransport
from ..procedural import (
    procedural_build_job_key,
    procedural_detail_job_key,
    procedural_export_model_job_key,
    procedural_export_xlsx_job_key,
    procedural_import_job_key,
    procedural_preview_job_key,
    procedural_relocations_job_key,
)
from ..queue import JobQueue
from ..scope import Scope
from ..storage import Storage
from .deps import (
    DIRECT_UPLOAD_THRESHOLD_BYTES,
    RestContext,
    advertised_engine_capability,
    require_catalog_pool,
    rest_context,
    scope_from_path,
)

router = APIRouter()


def require_procedural_pool(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="procedural models disabled (no database configured)")
    return pool


async def get_procedural_in_scope(pool, model_id: str, scope_obj: Scope) -> dict:
    try:
        row = await db_module.get_procedural_model(pool, model_id)
    except Exception:
        # malformed UUID etc. — treat as not found, not a 500
        row = None
    if row is None or row["scope_kind"] != scope_obj.kind or (row["scope_id"] or None) != (scope_obj.id or None):
        raise HTTPException(status_code=404, detail="procedural model not found")
    return row


async def catalog_fingerprint_for(pool, scope_obj: Scope, doc: dict) -> str | None:
    """Fingerprint of the scope's equipment + system catalogs, but ONLY when the
    model actually references catalog items — else ``None``, i.e. the model has no
    catalog dependency and its cache stays purely revision/doc-hash keyed. The
    equipment/system catalogs are live compile inputs the model's revision doesn't
    capture, so folding this (via the ``.catfp`` sidecar) makes a catalog edit force
    a fresh compile."""
    if not (doc.get("equipments") or doc.get("systems")):
        return None
    return await db_module.get_catalog_fingerprint(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)


async def catalog_cache_stale(storage: Storage, scope_obj: Scope, derived_key: str, catalog_fp: str | None) -> bool:
    """True when a cached artifact must be rebuilt because the catalog changed
    since it was built. ``catalog_fp is None`` (no catalog dependency) is never
    stale. Otherwise compare the live fingerprint against the ``.catfp`` sidecar
    written beside the artifact: a mismatch — or a missing sidecar (an artifact
    built before this feature, or one whose sidecar write was lost) — is stale, so
    the cache heals itself on the next compile."""
    if catalog_fp is None:
        return False
    from ..procedural import procedural_catalog_fp_key

    try:
        stored = (await storage.get_bytes(scope_obj, procedural_catalog_fp_key(derived_key))).decode("utf-8").strip()
    except Exception:
        stored = None
    return stored != catalog_fp


@router.get("/scopes/{scope}/procedural-models")
async def api_procedural_list(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    from ..procedural import procedural_glb_key

    pool = require_procedural_pool(request)
    models = await db_module.list_procedural_models(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)
    out = []
    for m in models:
        glb_key = procedural_glb_key(m["id"], m["revision"])
        m = dict(m)
        m["latest_glb_key"] = glb_key if await ctx.storage.exists(scope_obj, glb_key) else None
        out.append(m)
    return JSONResponse({"models": out})


@router.get("/scopes/{scope}/procedural-templates")
async def api_procedural_templates(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Start-from templates for the ``New model from template`` menu.

    The list is the union of the demo templates advertised by every
    currently-live worker: the base worker announces the ``adapy-default``
    templates, and a capability worker announces its own —
    so a template appears exactly while a worker that can build it is up, and
    vanishes when that pool goes offline. Each carries the ``doc`` committed
    verbatim on instantiate (for a non-default engine, a thin routing
    document the engine expands at compile time). No DB rows are involved."""
    # Union across live workers, keyed by slug (last writer wins). Scope is
    # only an access gate here — the templates themselves are worker-global.
    specs = await ctx.jobs.advertised_specs("procedural_template_specs")
    templates = [
        {
            "id": slug,
            "name": s.get("name") or slug,
            "engine": s.get("engine") or "adapy-default",
            "doc": s.get("doc") if isinstance(s.get("doc"), dict) else {},
        }
        for slug, s in specs.items()
    ]
    return JSONResponse({"templates": templates})


@router.post("/scopes/{scope}/procedural-models", status_code=201)
async def api_procedural_create(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    pool = require_procedural_pool(request)
    from ..procedural import normalize_model_name

    body = await request.json()
    raw_name = body.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise HTTPException(status_code=400, detail="name (str) is required")
    # The name may carry a folder path — models are filed alongside real
    # files in the storage browser and a UUID is what actually addresses
    # them, so "decks/level-3/module-a" is a name, not a route.
    try:
        name = normalize_model_name(raw_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await db_module.create_procedural_model(
        pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id, name=name, created_by=user.sub
    )
    if row is None:
        raise HTTPException(status_code=409, detail=f"a procedural model named {name!r} already exists")
    return JSONResponse(row, status_code=201)


@router.patch("/scopes/{scope}/procedural-models/{model_id}/name")
async def api_procedural_rename(
    model_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Rename a model — which is also how it is MOVED between folders.

    One operation, because the name IS the path: a separate move would be a
    second way to say where a model lives, and two of those eventually
    disagree.
    """
    from ..procedural import normalize_model_name

    pool = require_procedural_pool(request)
    body = await request.json()
    raw_name = body.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise HTTPException(status_code=400, detail="name (str) is required")
    try:
        name = normalize_model_name(raw_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = await db_module.rename_procedural_model(pool, model_id, name)
    if row is False:
        raise HTTPException(status_code=409, detail=f"a procedural model named {name!r} already exists")
    if row is None:
        raise HTTPException(status_code=404, detail="procedural model not found")
    return JSONResponse(row)


@router.post("/scopes/{scope}/procedural-models/equipment-types/sync", status_code=201)
async def api_procedural_equipment_sync(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Persist a code-defined equipment archetype into the per-scope DB
    catalog so it becomes an editable catalog entry. Body: ``{slug}``. Its
    full spec comes from the live workers' advertisement, so the slim API
    never imports ``ada``."""
    from ..catalog import validate_equipment_doc

    pool = require_catalog_pool(request)
    body = await request.json()
    slug = body.get("slug")
    if not isinstance(slug, str) or not slug:
        raise HTTPException(status_code=400, detail="slug (str) is required")
    spec = (await ctx.jobs.advertised_specs("procedural_equipment_specs")).get(slug)
    if spec is None or not isinstance(spec.get("doc"), dict):
        raise HTTPException(status_code=404, detail=f"no code equipment archetype {slug!r} advertised by a live worker")
    try:
        doc = validate_equipment_doc(spec["doc"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid archetype doc: {e}")
    name = spec.get("name") or slug
    desc = "Synced from built-in archetype"
    created = await db_module.create_equipment_type(
        pool,
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        slug=slug,
        name=name,
        description=desc,
        created_by=user.sub,
    )
    if created is None:
        raise HTTPException(status_code=409, detail=f"equipment type {slug!r} already exists in this scope")
    new_rev = await db_module.update_equipment_type(
        pool,
        created["id"],
        slug=slug,
        name=name,
        description=desc,
        doc=doc,
        base_revision=created["revision"],
    )
    return JSONResponse(
        {"id": created["id"], "slug": slug, "revision": new_rev if new_rev is not None else created["revision"]},
        status_code=201,
    )


@router.post("/scopes/{scope}/procedural-models/equipment-types/resync")
async def api_procedural_equipment_resync(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Upsert EVERY code-defined equipment archetype into the scope catalog,
    UPDATING an existing entry whose slug matches (unlike ``/sync``, which only
    creates and 409s on an existing one). This is the "Resync equipments"
    action: code changes — a new port like ``feeder2``, a corrected nozzle
    height — flow into the catalog docs that placed equipment resolve against,
    so a recompile picks them up. Idempotent: a slug whose catalog doc already
    equals the code doc is left untouched. Returns per-slug outcomes."""
    from ..catalog import (
        resync_target_doc,
        summarize_equipment_doc_changes,
        validate_equipment_doc,
    )

    pool = require_catalog_pool(request)
    specs = await ctx.jobs.advertised_specs("procedural_equipment_specs")
    if not specs:
        raise HTTPException(status_code=503, detail="no live worker advertising equipment archetypes")
    existing = {
        t["slug"]: t
        for t in await db_module.list_equipment_types(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)
    }
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    skipped: list[str] = []
    # Per-slug human-readable "what changed" so the client can show a summary
    # (which equipment changed and how), not just counts. Created entries list
    # a single "new equipment" line.
    changes: dict[str, list[str]] = {}
    for slug, spec in specs.items():
        if not isinstance(spec.get("doc"), dict):
            skipped.append(slug)
            continue
        try:
            doc = validate_equipment_doc(spec["doc"])
        except ValueError:
            skipped.append(slug)
            continue
        name = spec.get("name") or slug
        cur = existing.get(slug)
        if cur is not None:
            full = await db_module.get_equipment_type(pool, cur["id"])
            stored_doc = (full or {}).get("doc") or {}
            # A CAD-backed type's inferred geometry + aligned ports must survive
            # the resync (which runs on every model open); see resync_target_doc.
            target_doc = resync_target_doc(doc, stored_doc, bool(cur.get("cad_key")))
            if full is not None and stored_doc == target_doc and full.get("name") == name:
                unchanged.append(slug)
                continue
            changes[slug] = summarize_equipment_doc_changes(
                stored_doc, (full or {}).get("name") or slug, target_doc, name
            )
            await db_module.update_equipment_type(
                pool,
                cur["id"],
                slug=slug,
                name=name,
                description=(full or {}).get("description") or "Synced from built-in archetype",
                doc=target_doc,
                base_revision=cur["revision"],
            )
            updated.append(slug)
        else:
            row = await db_module.create_equipment_type(
                pool,
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
                slug=slug,
                name=name,
                description="Synced from built-in archetype",
                created_by=user.sub,
            )
            if row is None:
                skipped.append(slug)
                continue
            await db_module.update_equipment_type(
                pool,
                row["id"],
                slug=slug,
                name=name,
                description="Synced from built-in archetype",
                doc=doc,
                base_revision=row["revision"],
            )
            created.append(slug)
            changes[slug] = ["new equipment"]
    return JSONResponse(
        {"created": created, "updated": updated, "unchanged": unchanged, "skipped": skipped, "changes": changes}
    )


@router.post("/scopes/{scope}/procedural-models/system-types/sync", status_code=201)
async def api_procedural_system_sync(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Persist a code-defined system kind into the per-scope DB
    system-template catalog. Body: ``{slug}``. The built-in kinds are static,
    so this works without a live worker."""
    from ..catalog import builtin_system_specs, validate_system_doc

    pool = require_catalog_pool(request)
    body = await request.json()
    slug = body.get("slug")
    if not isinstance(slug, str) or not slug:
        raise HTTPException(status_code=400, detail="slug (str) is required")
    specs = {s["slug"]: s for s in builtin_system_specs()}
    specs.update(await ctx.jobs.advertised_specs("procedural_system_specs"))
    spec = specs.get(slug)
    if spec is None or not isinstance(spec.get("doc"), dict):
        raise HTTPException(status_code=404, detail=f"no code system kind {slug!r}")
    try:
        doc = validate_system_doc(spec["doc"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid system-kind doc: {e}")
    name = spec.get("name") or slug
    desc = "Synced from built-in system kind"
    created = await db_module.create_system_template(
        pool,
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        slug=slug,
        name=name,
        description=desc,
        created_by=user.sub,
    )
    if created is None:
        raise HTTPException(status_code=409, detail=f"system template {slug!r} already exists in this scope")
    new_rev = await db_module.update_system_template(
        pool,
        created["id"],
        slug=slug,
        name=name,
        description=desc,
        doc=doc,
        base_revision=created["revision"],
    )
    return JSONResponse(
        {"id": created["id"], "slug": slug, "revision": new_rev if new_rev is not None else created["revision"]},
        status_code=201,
    )


@router.get("/scopes/{scope}/procedural-models/{model_id}")
async def api_procedural_get(
    request: Request,
    model_id: str,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    pool = require_procedural_pool(request)
    row = await get_procedural_in_scope(pool, model_id, scope_obj)
    return JSONResponse(
        {k: row[k] for k in ("id", "name", "doc", "revision", "created_by", "created_at", "updated_at")}
    )


@router.put("/scopes/{scope}/procedural-models/{model_id}")
async def api_procedural_commit(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    from ..procedural import validate_doc

    pool = require_procedural_pool(request)
    row = await get_procedural_in_scope(pool, model_id, scope_obj)
    body = await request.json()
    doc = body.get("doc")
    base_revision = body.get("base_revision")
    if not isinstance(doc, dict):
        raise HTTPException(status_code=400, detail="doc (object) is required")
    if not isinstance(base_revision, int):
        raise HTTPException(status_code=400, detail="base_revision (int) is required")
    try:
        normalized = validate_doc(doc)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid procedural doc: {e}")
    new_revision = await db_module.update_procedural_model_doc(pool, model_id, normalized, base_revision)
    if new_revision is None:
        current = await db_module.get_procedural_model(pool, model_id)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "revision conflict",
                "current_revision": current["revision"] if current else None,
            },
        )
    # Promote-on-commit: if this exact doc was already previewed (same hash),
    # copy that preview blob to the committed revision key instead of forcing
    # a recompile — so what the user saw is byte-identical to the committed
    # revision, and a subsequent compile short-circuits to the cache. Best
    # effort: a miss just means the normal (auto-)compile builds it.
    try:
        from ..procedural import (
            doc_content_hash,
            procedural_catalog_fp_key,
            procedural_glb_key,
            procedural_preview_glb_key,
        )

        engine = (row.get("engine") or "").strip() or None
        if engine == "adapy-default":
            engine = None
        preview_key = procedural_preview_glb_key(model_id, doc_content_hash(normalized), engine, "sim")
        revision_key = procedural_glb_key(model_id, new_revision, engine)
        if await ctx.storage.exists(scope_obj, preview_key) and not await ctx.storage.exists(scope_obj, revision_key):
            data = await ctx.storage.get_bytes(scope_obj, preview_key)
            await ctx.storage.put_bytes(scope_obj, revision_key, data, content_encoding="gzip")
            # Carry the catalog-fingerprint sidecar across too, so the first
            # post-commit compile short-circuits to cache instead of rebuilding
            # once to (re)establish it (catalog-bearing models only).
            try:
                fp = await ctx.storage.get_bytes(scope_obj, procedural_catalog_fp_key(preview_key))
                await ctx.storage.put_bytes(scope_obj, procedural_catalog_fp_key(revision_key), fp)
            except Exception:
                pass
    except Exception:
        logger.warning("procedural: promote-on-commit failed for %s (non-fatal)", model_id, exc_info=True)
    return JSONResponse({"id": row["id"], "revision": new_revision})


@router.delete("/scopes/{scope}/procedural-models/{model_id}")
async def api_procedural_delete(
    request: Request,
    model_id: str,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    pool = require_procedural_pool(request)
    await get_procedural_in_scope(pool, model_id, scope_obj)
    ok = await db_module.archive_procedural_model(pool, model_id)
    if not ok:
        raise HTTPException(status_code=404, detail="procedural model not found")
    return JSONResponse({"status": "archived"})


async def resolve_detailing_engine(jobs: JobTransport, detailing: str | None) -> dict | None:
    """Resolve a selected detailing slug to the spec a live capability worker
    advertises (or ``None`` for ``none``/absent). External engines are
    discovered only from live heartbeats, so an external engine is routable
    while its pool is online. An in-process built-in (``adapy-default``) has no
    external spec and returns ``None`` — its detailing runs as stage 2 of the
    structural build (Phase 1)."""
    if not detailing or detailing == "none":
        return None
    spec = (await jobs.advertised_specs("procedural_detailing_engine_specs")).get(detailing)
    # Only EXTERNAL (out-of-process) engines are routed as a chained job; an
    # in-process one falls through to the unchanged Phase-1 in-process path.
    if spec is None or spec.get("inprocess", False):
        return None
    return spec


async def audit_compile_run(
    ctx: RestContext,
    request: Request,
    user: User,
    scope: Scope,
    *,
    job_id: str | None,
    derived_key: str,
    target_format: str = "procedural_build",
) -> None:
    """Open the audit row for one compile RUN, at enqueue time.

    Deliberately the same idiom as a conversion: ``job_id`` is the run id, so
    the worker's existing terminal-status patch (``_audit_done`` →
    ``update_audit_by_job``) fills in duration, error, traceback and the run's
    ``log_key`` on the very same row. That makes a compile visible in the admin
    audit log — with its log readable through the row's existing "Log" tab —
    without a second audit surface for procedural runs."""
    if not job_id:
        return
    await ctx.audit(
        request,
        user,
        scope,
        "compile",
        key=derived_key,
        target_format=target_format,
        status="queued",
        job_id=job_id,
    )


def parse_detailing_options(raw: str | None) -> dict:
    """Parse the ``?detailing_options=<json>`` query param — the per-joint-type
    option map ``{slug: {enabled, <field>: value}}`` the Detailing tab produced.
    Malformed / non-object JSON is treated as no options (empty dict), so a bad
    value degrades to the default detailing rather than failing the compile."""
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@router.post("/scopes/{scope}/procedural-models/{model_id}/compile")
async def api_procedural_compile(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    from ..procedural import (
        procedural_detailing_glb_key,
        procedural_structural_ifc_key,
        procedural_structural_sections_key,
    )

    # ``?force=true`` recompiles even when the revision's GLB is already
    # cached. The cache is keyed by the model REVISION, not the compiler
    # version — so when the routing/topology engine changes but the document
    # doesn't, a plain recompile would hand back the stale pre-change blob.
    # Force skips the endpoint short-circuit AND sets ``force_rebuild`` so the
    # worker's own redelivery short-circuit is bypassed too; the worker then
    # overwrites the blob in place (same revision key), so no doc edit /
    # revision bump is needed to pick up an engine fix.
    force = (request.query_params.get("force") or "").strip().lower() in ("1", "true", "yes")
    # ``?lod=detail`` compiles the richer detail model into a separate,
    # revision-stamped derived key so it caches independently of the simulation
    # GLB. Any other value is the default simulation model.
    lod = "detail" if (request.query_params.get("lod") or "").strip().lower() == "detail" else "sim"
    # ``?engine=<slug>`` selects the procedural engine (default = adapy-default).
    # A non-default engine's output caches under its own key so it never serves
    # the default's bytes; the slug travels to the worker in conversion_options.
    engine = (request.query_params.get("engine") or "").strip() or None
    # ``?detailing=<slug>`` selects the detailing engine (fabrication-detail
    # stage). A COMPILE-time choice, not on the document. None/"none" adds NO
    # key suffix -> byte-identical to the plain structural key (backward-compat).
    detailing = (request.query_params.get("detailing") or "").strip() or None
    # ``?detailing_options=<json>`` carries the per-joint-type option map the
    # Detailing tab produced. Folded into the derived key (a knob change is a
    # distinct cache entry) and passed to the worker's in-process detail().
    # Ignored (no key effect) when no detailing engine is selected.
    detailing_options = parse_detailing_options(request.query_params.get("detailing_options")) if detailing else {}

    pool = require_procedural_pool(request)
    row = await get_procedural_in_scope(pool, model_id, scope_obj)
    # Absent an explicit query override, honour the engine the model declares
    # (its stored routing header, imported from the workbook's Model sheet), so
    # a file authored for a specific engine auto-routes there. "adapy-default"
    # collapses to None — the built-in path with the bare (backward-compat) key.
    if engine is None:
        declared = (row.get("engine") or "").strip()
        engine = declared or None
        if engine == "adapy-default":
            engine = None
    derived_key = procedural_detailing_glb_key(row["id"], row["revision"], engine, detailing, lod, detailing_options)

    # The equipment/system catalogs are LIVE compile inputs the model revision
    # doesn't capture — a catalog edit must yield a fresh compile even though the
    # derived key is unchanged. Fingerprint them (only when the model places
    # catalog items) and, on a hit, rebuild if the cached artifact predates the
    # current catalog state (sidecar mismatch).
    catalog_fp = await catalog_fingerprint_for(pool, scope_obj, row.get("doc") or {})

    # Resolve the selected detailing engine. An EXTERNAL (Tier-B) engine
    # (inprocess=False) runs as a chained ``procedural_detail``
    # job on its own capability pool consuming a neutral structural artifact;
    # an in-process one (none/adapy-default) is unchanged from Phase 1.
    det_spec = await resolve_detailing_engine(ctx.jobs, detailing)
    is_external_detailing = det_spec is not None

    if not force and await ctx.storage.exists(scope_obj, derived_key):
        if not await catalog_cache_stale(ctx.storage, scope_obj, derived_key, catalog_fp):
            return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True})
        # Cached artifact is stale w.r.t. the catalog — rebuild it in place
        # (force past the worker's own redelivery short-circuit).
        force = True

    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="procedural build disabled (no NATS configured)")

    # A registered (DB) engine may name a worker_capability — the tag of the
    # worker pool that has that engine + its deps pre-installed. Route the
    # compile there. Built-in engines (adapy-default/echo, in the base image)
    # have no DB row and run on the default pool.
    target_capability = None
    if engine and engine != "adapy-default":
        eng = await db_module.get_procedural_engine_by_slug(
            pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id, slug=engine
        )
        if eng is not None:
            target_capability = (eng.get("doc") or {}).get("worker_capability")
        else:
            target_capability = await advertised_engine_capability(ctx.queue, engine)

    if is_external_detailing:
        # Two-stage pipeline. Stage 1: the structural build on the default (or
        # the procedural engine's) pool writes the PLAIN structural GLB and,
        # because ``detailing_external`` is set, ALSO the neutral structural IFC
        # artifact + section sidecar the external engine reads. Stage 2: the
        # chained ``procedural_detail`` job on the detailing engine's capability
        # pool consumes those and writes the detailing-layer GLB to ``derived_key``.
        structural_key = procedural_detailing_glb_key(row["id"], row["revision"], engine, None, lod)
        structural_ifc_key = procedural_structural_ifc_key(row["id"], row["revision"], engine)
        sections_key = procedural_structural_sections_key(row["id"], row["revision"], engine)

        structural_job = await ctx.queue.enqueue(
            procedural_build_job_key(row["id"], row["revision"], lod),
            target_format="procedural_build",
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            conversion_options={
                "model_id": row["id"],
                "revision": row["revision"],
                "lod": lod,
                "engine": engine,
                # The structural stage runs NO in-process detailing (the external
                # pool does it); this flag just makes it emit the neutral artifact.
                "detailing": None,
                "detailing_external": True,
                "structural_ifc_key": structural_ifc_key,
                "structural_sections_key": sections_key,
                "catalog_fingerprint": catalog_fp,
            },
            derived_key=structural_key,
            force_rebuild=force,
            target_capability=target_capability,
        )
        detail_job = await ctx.queue.enqueue(
            procedural_detail_job_key(row["id"], row["revision"], lod, detailing),
            target_format="procedural_detail",
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            conversion_options={
                "model_id": row["id"],
                "revision": row["revision"],
                "lod": lod,
                "engine": engine,
                "detailing": detailing,
                "detailing_entrypoint": det_spec.get("entrypoint"),
                "detailing_options": detailing_options,
                "structural_ifc_key": structural_ifc_key,
                "structural_sections_key": sections_key,
                "catalog_fingerprint": catalog_fp,
            },
            derived_key=derived_key,
            force_rebuild=force,
            target_capability=det_spec.get("worker_capability"),
        )
        # Both stages are compile runs of their own (each writes its own log
        # under its own job id), so both get an audit row.
        await audit_compile_run(ctx, request, user, scope_obj, job_id=structural_job.job_id, derived_key=structural_key)
        await audit_compile_run(
            ctx,
            request,
            user,
            scope_obj,
            job_id=detail_job.job_id,
            derived_key=derived_key,
            target_format="procedural_detail",
        )
        return JSONResponse(
            {
                "job_id": detail_job.job_id,
                "derived_key": derived_key,
                "cached": False,
                "structural_job_id": structural_job.job_id,
                "structural_key": structural_key,
            }
        )

    job = await ctx.queue.enqueue(
        procedural_build_job_key(row["id"], row["revision"], lod),
        target_format="procedural_build",
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        conversion_options={
            "model_id": row["id"],
            "revision": row["revision"],
            "lod": lod,
            "engine": engine,
            "detailing": detailing,
            "detailing_options": detailing_options,
            "catalog_fingerprint": catalog_fp,
        },
        derived_key=derived_key,
        force_rebuild=force,
        target_capability=target_capability,
    )
    await audit_compile_run(ctx, request, user, scope_obj, job_id=job.job_id, derived_key=derived_key)
    return JSONResponse({"job_id": job.job_id, "derived_key": derived_key, "cached": False})


@router.post("/scopes/{scope}/procedural-models/{model_id}/compile-preview")
async def api_procedural_compile_preview(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Compile the CURRENT (uncommitted) document — a *preview* — without
    minting a revision. The body carries ``{doc, engine?, lod?}``; the doc is
    validated + normalized, hashed, and built to
    ``_procedural/{id}/preview/{hash}.glb`` (content-keyed, so re-previewing an
    unchanged doc is free). No DB write, no revision bump — the user commits
    only when happy, and the commit promotes this exact blob (same hash) to the
    revision key, so committed == previewed with no recompile.

    Mirrors :func:`api_procedural_compile` for engine routing / cache / enqueue,
    but the worker gets the doc inline (``conversion_options.preview_doc``)."""
    from ..procedural import doc_content_hash, procedural_preview_glb_key, validate_doc

    body = await request.json()
    doc = body.get("doc")
    if not isinstance(doc, dict):
        raise HTTPException(status_code=400, detail="doc (object) is required")
    try:
        normalized = validate_doc(doc)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid procedural doc: {e}")

    force = (request.query_params.get("force") or "").strip().lower() in ("1", "true", "yes")
    lod = "detail" if (request.query_params.get("lod") or "").strip().lower() == "detail" else "sim"
    engine = (request.query_params.get("engine") or "").strip() or None
    detailing = (request.query_params.get("detailing") or "").strip() or None
    detailing_options = parse_detailing_options(request.query_params.get("detailing_options")) if detailing else {}

    pool = require_procedural_pool(request)
    row = await get_procedural_in_scope(pool, model_id, scope_obj)
    # Same engine resolution as /compile, but a preview may also carry the
    # engine on the doc itself (the doc is the source of truth here). Query
    # override > doc header > model's declared engine; "adapy-default" == None.
    if engine is None:
        declared = (normalized.get("engine") or row.get("engine") or "").strip()
        engine = declared or None
        if engine == "adapy-default":
            engine = None

    doc_hash = doc_content_hash(normalized)
    derived_key = procedural_preview_glb_key(row["id"], doc_hash, engine, lod, detailing, detailing_options)

    # The preview key is content-keyed on the DOC, but catalog equipment/systems
    # are resolved live at compile — so a catalog edit must invalidate a cached
    # preview too (same reasoning as /compile). Fingerprint the referenced catalogs.
    catalog_fp = await catalog_fingerprint_for(pool, scope_obj, normalized)

    if not force and await ctx.storage.exists(scope_obj, derived_key):
        if not await catalog_cache_stale(ctx.storage, scope_obj, derived_key, catalog_fp):
            return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True, "doc_hash": doc_hash})
        force = True

    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="procedural build disabled (no NATS configured)")

    target_capability = None
    if engine and engine != "adapy-default":
        eng = await db_module.get_procedural_engine_by_slug(
            pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id, slug=engine
        )
        if eng is not None:
            target_capability = (eng.get("doc") or {}).get("worker_capability")
        else:
            target_capability = await advertised_engine_capability(ctx.queue, engine)

    job = await ctx.queue.enqueue(
        procedural_preview_job_key(row["id"], doc_hash, lod),
        target_format="procedural_build",
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        conversion_options={
            "model_id": row["id"],
            "revision": row["revision"],
            "lod": lod,
            "engine": engine,
            "detailing": detailing,
            "detailing_options": detailing_options,
            "preview_doc": normalized,
            "catalog_fingerprint": catalog_fp,
        },
        derived_key=derived_key,
        force_rebuild=force,
        target_capability=target_capability,
    )
    await audit_compile_run(ctx, request, user, scope_obj, job_id=job.job_id, derived_key=derived_key)
    return JSONResponse({"job_id": job.job_id, "derived_key": derived_key, "cached": False, "doc_hash": doc_hash})


@router.get("/scopes/{scope}/procedural-models/{model_id}/compile-log")
async def api_procedural_compile_log(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> PlainTextResponse:
    """Return the engine log captured during one compile RUN as ``text/plain``.

    A log belongs to a RUN, not to a document. Pass ``?run=<job_id>`` — the id
    the compile/preview response returned — and you get exactly that run's log
    (:func:`procedural_run_log_key`) or an empty body; a second compile of the
    very same document can therefore never hand back the first one's output.

    ``?key=<derived GLB key>`` remains for the two lookups that have no run id
    of their own: a result the endpoint served straight from cache (no run
    happened just now), and an artifact built before runs existed. It resolves
    through the key's ``.run`` pointer sidecar to the run that most recently
    targeted that artifact, falling back to the legacy ``.log`` sibling.

    The response carries ``X-Compile-Run`` naming the run actually served (empty
    when it came from the legacy sibling), so a caller can tell whether the log
    it is showing belongs to the run it just triggered. An empty body means that
    run produced no log. 503 when the procedural DB isn't configured, matching
    the sibling procedural endpoints."""
    from ..procedural import (
        PROCEDURAL_PREFIX,
        is_valid_run_id,
        procedural_log_key,
        procedural_run_log_key,
        procedural_run_pointer_key,
    )

    pool = require_procedural_pool(request)
    # Scope + existence check (raises 404 when the model isn't in this scope).
    await get_procedural_in_scope(pool, model_id, scope_obj)
    run = (request.query_params.get("run") or "").strip()
    key = (request.query_params.get("key") or "").strip()
    if not run and not key:
        raise HTTPException(status_code=400, detail="run (compile run id) or key (derived GLB key) is required")

    async def _read(blob_key: str) -> str | None:
        try:
            data = await ctx.storage.get_bytes(scope_obj, blob_key)
        except Exception:
            return None
        return data.decode("utf-8", errors="replace")

    if run:
        # The run id lands inside a blob key, so it is validated (not merely
        # escaped) before use — the same confinement `key` gets below.
        if not is_valid_run_id(run):
            raise HTTPException(status_code=400, detail="run is not a valid compile run id")
        text = await _read(procedural_run_log_key(model_id, run))
        return PlainTextResponse(text or "", headers={"X-Compile-Run": run})

    # Confine reads to this model's hidden prefix so the endpoint can't be used
    # to fetch arbitrary blobs by handing it any key.
    if not key.startswith(f"{PROCEDURAL_PREFIX}{model_id}/"):
        raise HTTPException(status_code=400, detail="key is not a derived artifact of this model")
    pointed = (await _read(procedural_run_pointer_key(key)) or "").strip()
    if pointed and is_valid_run_id(pointed):
        text = await _read(procedural_run_log_key(model_id, pointed))
        return PlainTextResponse(text or "", headers={"X-Compile-Run": pointed})
    # Legacy: an artifact whose compile predates run-keyed logs.
    return PlainTextResponse(await _read(procedural_log_key(key)) or "", headers={"X-Compile-Run": ""})


@router.get("/scopes/{scope}/procedural-models/{model_id}/stats")
async def api_procedural_stats(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Return the quantity take-off computed alongside a compiled GLB.

    The frontend passes the GLB ``derived_key`` (from the compile/preview
    response) as ``?key=``; the stats are that key's ``.stats.json`` sibling
    (:func:`procedural_stats_key`). A model with no such sibling (a capability engine /
    STEP-IFC imports) returns ``{"available": false}`` (HTTP 200) so the panel
    can degrade gracefully to a muted "take-off not available" state rather
    than erroring."""
    import json as _json

    from ..procedural import PROCEDURAL_PREFIX, procedural_stats_key

    pool = require_procedural_pool(request)
    await get_procedural_in_scope(pool, model_id, scope_obj)
    key = (request.query_params.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key (derived GLB key) query param is required")
    if not key.startswith(f"{PROCEDURAL_PREFIX}{model_id}/"):
        raise HTTPException(status_code=400, detail="key is not a derived artifact of this model")
    try:
        data = await ctx.storage.get_bytes(scope_obj, procedural_stats_key(key))
    except Exception:
        return JSONResponse({"available": False})
    try:
        stats = _json.loads(data.decode("utf-8"))
    except Exception:
        return JSONResponse({"available": False})
    return JSONResponse({"available": True, "stats": stats})


@router.get("/scopes/{scope}/procedural-models/{model_id}/stats/export")
async def api_procedural_stats_export(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> Response:
    """Export the take-off as a whole-model Excel workbook (``fmt=xlsx``, one
    sheet per discipline + COGs + Overview) or the active tab as ``fmt=csv``.

    Built on the fly from the stored ``.stats.json`` sibling (cheap; no worker
    job) via :func:`ada.topo_model.takeoff.takeoff_to_xlsx_bytes` /
    ``takeoff_to_csv``. 404 when no stats sidecar exists for the given GLB
    ``?key=``."""
    import json as _json

    from ada.topo_model.takeoff import takeoff_to_csv, takeoff_to_xlsx_bytes

    from ..procedural import PROCEDURAL_PREFIX, procedural_stats_key

    pool = require_procedural_pool(request)
    row = await get_procedural_in_scope(pool, model_id, scope_obj)
    key = (request.query_params.get("key") or "").strip()
    fmt = (request.query_params.get("fmt") or "xlsx").strip().lower()
    tab = (request.query_params.get("tab") or "overview").strip().lower()
    if not key:
        raise HTTPException(status_code=400, detail="key (derived GLB key) query param is required")
    if not key.startswith(f"{PROCEDURAL_PREFIX}{model_id}/"):
        raise HTTPException(status_code=400, detail="key is not a derived artifact of this model")
    if fmt not in ("xlsx", "csv"):
        raise HTTPException(status_code=400, detail="fmt must be 'xlsx' or 'csv'")
    try:
        data = await ctx.storage.get_bytes(scope_obj, procedural_stats_key(key))
        stats = _json.loads(data.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=404, detail="no take-off stats available for this model")
    base = str(row.get("name") or model_id)
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in base) or "model"
    if fmt == "csv":
        body = takeoff_to_csv(stats, tab)
        filename = f"{safe}_stats_{tab}.csv"
        return Response(
            content=body,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    xlsx = takeoff_to_xlsx_bytes(stats)
    filename = f"{safe}_stats.xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/scopes/{scope}/procedural-models/{model_id}/propose-relocations")
async def api_procedural_propose_relocations(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Enqueue a search for the minimum set of equipment relocations that make
    the model's runs route cleanly. Mirrors :func:`api_procedural_compile`, but
    the worker produces a JSON proposal document (not a GLB) at ``derived_key``.

    No cache short-circuit: the search always re-runs (it's cheap-ish and the
    layout may have changed since the last run), overwriting the previous
    proposals in place. The frontend polls ``convertStatus(job_id)`` then GETs
    the relocations blob via ``GET /api/scopes/{scope}/blobs/{derived_key}`` —
    a JSON ``{proposals, unresolved, baseline_problems}`` document. Relocations
    are proposals only; applying them is a separate, explicit user action."""
    from ..procedural import procedural_relocations_key

    pool = require_procedural_pool(request)
    row = await get_procedural_in_scope(pool, model_id, scope_obj)
    derived_key = procedural_relocations_key(row["id"])

    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="procedural relocations disabled (no NATS configured)")

    job = await ctx.queue.enqueue(
        procedural_relocations_job_key(row["id"], row["revision"]),
        target_format="procedural_relocations",
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        conversion_options={"model_id": row["id"], "revision": row["revision"]},
        derived_key=derived_key,
        force_rebuild=True,
    )
    return JSONResponse({"job_id": job.job_id, "derived_key": derived_key})


# ── Excel export / import ─────────────────────────────────────────
#
# Round-trip a procedural model through the OWNING engine's Excel workbook,
# delegated to the worker (the engine's capability pool does the read/write).
# Export mirrors /compile (revision-keyed cache + capability routing); import
# is a two-step: upload+detect (dependency-free _ADA_META peek), then enqueue.

# Built-in engine slugs with NO Excel format — echo is diagnostic only. Kept
# ada-free here (slim API): the default engine + registered engines support it.
NO_EXCEL_ENGINE_SLUGS = {"echo"}


async def procedural_engine_capability(queue: JobQueue, pool, scope_obj: Scope, engine: str | None) -> str | None:
    """The worker capability that owns a non-default engine's Excel format
    (its ``worker_capability``), so export/import routes to that pool. None for
    the default / built-in engines (base pool)."""
    if not engine or engine == "adapy-default" or engine in ("echo",):
        return None
    eng = await db_module.get_procedural_engine_by_slug(
        pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id, slug=engine
    )
    if eng is not None:
        return (eng.get("doc") or {}).get("worker_capability")
    return await advertised_engine_capability(queue, engine)


@router.post("/scopes/{scope}/procedural-models/{model_id}/export-xlsx")
async def api_procedural_export_xlsx(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Enqueue an export of the model's committed revision to its engine's
    Excel workbook. Mirrors :func:`api_procedural_compile`: revision-keyed
    cache short-circuit, ``?force=true`` to rebuild, engine → capability
    routing. The worker writes the ``.xlsx`` to ``derived_key``; the frontend
    polls ``convertStatus`` then downloads the blob as an attachment."""
    from ..procedural import procedural_xlsx_export_key

    force = (request.query_params.get("force") or "").strip().lower() in ("1", "true", "yes")
    engine = (request.query_params.get("engine") or "").strip() or None

    pool = require_procedural_pool(request)
    row = await get_procedural_in_scope(pool, model_id, scope_obj)
    # Honour the model's declared engine unless overridden (as /compile does);
    # "adapy-default" collapses to the bare (built-in) key.
    if engine is None:
        declared = (row.get("engine") or "").strip()
        engine = declared or None
        if engine == "adapy-default":
            engine = None
    if engine in NO_EXCEL_ENGINE_SLUGS:
        raise HTTPException(status_code=400, detail=f"engine {engine!r} has no Excel format")

    derived_key = procedural_xlsx_export_key(row["id"], row["revision"], engine)
    if not force and await ctx.storage.exists(scope_obj, derived_key):
        return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True})
    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="procedural export disabled (no NATS configured)")

    target_capability = await procedural_engine_capability(ctx.queue, pool, scope_obj, engine)
    job = await ctx.queue.enqueue(
        procedural_export_xlsx_job_key(row["id"], row["revision"]),
        target_format="procedural_export_xlsx",
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        conversion_options={"model_id": row["id"], "revision": row["revision"], "engine": engine},
        derived_key=derived_key,
        force_rebuild=force,
        target_capability=target_capability,
    )
    return JSONResponse({"job_id": job.job_id, "derived_key": derived_key, "cached": False})


@router.post("/scopes/{scope}/procedural-models/{model_id}/export-model")
async def api_procedural_export_model(
    request: Request,
    model_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Enqueue an export of the model's committed revision to a downloadable CAD
    / analysis file. ``?format=ifc`` serializes the DETAIL model (beams, plates,
    joints — the clash cuts ride along as IfcRelVoidsElement voids, equipment as
    IfcPump/IfcTank/…); ``?format=gxml`` serializes the SIMULATION model as a
    Genie concept XML, and ``?format=gnx`` as a Genie workspace (the same XML,
    zipped the way Genie saves one). Mirrors :func:`api_procedural_export_xlsx` (revision-keyed
    cache, ``?force=true`` to rebuild). Built-in engine only — a non-default
    engine emits GLB, not an in-process ada assembly the writers need. The
    worker writes ``derived_key``; the frontend polls then downloads the blob."""
    from ..procedural import procedural_model_export_key

    fmt = (request.query_params.get("format") or "").strip().lower()
    if fmt not in ("ifc", "gxml", "gnx"):
        raise HTTPException(status_code=400, detail="format must be 'ifc', 'gxml' or 'gnx'")
    force = (request.query_params.get("force") or "").strip().lower() in ("1", "true", "yes")
    # IFC only: splice real catalog CAD geometry for equipment (default on). A
    # falsy value renders equipment as placeholder boxes; gxml ignores it (it
    # always exports equipment as its Genie concept type).
    cad_raw = (request.query_params.get("cad") or "").strip().lower()
    cad_equipment = fmt == "ifc" and cad_raw not in ("0", "false", "no")

    pool = require_procedural_pool(request)
    row = await get_procedural_in_scope(pool, model_id, scope_obj)
    engine = (row.get("engine") or "").strip()
    if engine and engine != "adapy-default":
        raise HTTPException(
            status_code=400,
            detail=f"IFC/Genie export is only available for the built-in engine, not {engine!r}",
        )

    # IFC = the detail model (with its fabrication detailing); Genie (XML or
    # workspace) = the sim model.
    lod = "detail" if fmt == "ifc" else "sim"
    detailing = (row.get("doc") or {}).get("detailing") if fmt == "ifc" else None

    derived_key = procedural_model_export_key(row["id"], row["revision"], fmt, cad_equipment=cad_equipment)
    # Equipment (and, for gxml, systems) are resolved live from the catalog at
    # export — so a catalog edit must invalidate a cached export too.
    catalog_fp = await catalog_fingerprint_for(pool, scope_obj, row.get("doc") or {})
    if not force and await ctx.storage.exists(scope_obj, derived_key):
        if not await catalog_cache_stale(ctx.storage, scope_obj, derived_key, catalog_fp):
            return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True})
        force = True
    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="procedural export disabled (no NATS configured)")

    job = await ctx.queue.enqueue(
        procedural_export_model_job_key(row["id"], row["revision"], fmt),
        target_format="procedural_export_model",
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        conversion_options={
            "model_id": row["id"],
            "revision": row["revision"],
            "export_format": fmt,
            "lod": lod,
            "detailing": detailing,
            "cad_equipment": cad_equipment,
            "catalog_fingerprint": catalog_fp,
        },
        derived_key=derived_key,
        force_rebuild=force,
        target_capability=None,
    )
    return JSONResponse({"job_id": job.job_id, "derived_key": derived_key, "cached": False})


@router.post("/scopes/{scope}/procedural-models/import-xlsx/upload")
async def api_procedural_import_xlsx_upload(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Stage an uploaded ``.xlsx`` for import and auto-detect its owning engine.

    The workbook (octet-stream body) is stored under a hidden per-upload token;
    its ``_ADA_META`` sheet is read dependency-free (stdlib zip/xml) to detect
    which engine authored it. Returns ``{source_key, engine, package,
    package_version, schema_version}``; ``engine`` is ``null`` for a hand-made /
    legacy workbook (no ``_ADA_META``), which is the frontend's cue to PROMPT
    the user to choose an engine before calling ``/import-xlsx``."""
    import uuid

    from ..procedural import (
        ADA_META_KEY_ENGINE,
        ADA_META_KEY_PACKAGE,
        ADA_META_KEY_PACKAGE_VERSION,
        ADA_META_KEY_SCHEMA_VERSION,
        procedural_import_source_key,
        read_ada_meta_from_xlsx_bytes,
    )

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")
    if len(data) > DIRECT_UPLOAD_THRESHOLD_BYTES:
        raise HTTPException(status_code=413, detail="workbook too large to import")

    source_key = procedural_import_source_key(uuid.uuid4().hex)
    await ctx.storage.put_bytes(scope_obj, source_key, data)

    meta = read_ada_meta_from_xlsx_bytes(data) or {}
    engine = (meta.get(ADA_META_KEY_ENGINE) or "").strip() or None
    return JSONResponse(
        {
            "source_key": source_key,
            "engine": engine,
            "package": meta.get(ADA_META_KEY_PACKAGE),
            "package_version": meta.get(ADA_META_KEY_PACKAGE_VERSION),
            "schema_version": meta.get(ADA_META_KEY_SCHEMA_VERSION),
        }
    )


@router.post("/scopes/{scope}/procedural-models/import-xlsx", status_code=201)
async def api_procedural_import_xlsx(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Enqueue an import of a previously-uploaded workbook into a NEW model.

    Body: ``{source_key, engine, name}`` — ``source_key`` from
    ``/import-xlsx/upload``; ``engine`` the detected or user-chosen slug;
    ``name`` the new model's name. The engine's capability pool parses the
    workbook into a procedural doc and creates the model; the frontend polls
    ``convertStatus`` then GETs the JSON result blob to learn the new
    ``model_id``."""
    from ..procedural import PROCEDURAL_PREFIX, procedural_import_result_key

    pool = require_procedural_pool(request)
    body = await request.json()
    source_key = (body.get("source_key") or "").strip()
    engine = (body.get("engine") or "").strip() or None
    name = (body.get("name") or "").strip()
    if not source_key or not source_key.startswith(f"{PROCEDURAL_PREFIX}_import/"):
        raise HTTPException(status_code=400, detail="source_key (from /import-xlsx/upload) is required")
    if not name:
        raise HTTPException(status_code=400, detail="name (str) is required")
    if engine in NO_EXCEL_ENGINE_SLUGS:
        raise HTTPException(status_code=400, detail=f"engine {engine!r} has no Excel format")
    if engine == "adapy-default":
        engine = None
    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="procedural import disabled (no NATS configured)")

    target_capability = await procedural_engine_capability(ctx.queue, pool, scope_obj, engine)
    derived_key = procedural_import_result_key(source_key)
    job = await ctx.queue.enqueue(
        procedural_import_job_key(source_key),
        target_format="procedural_import_xlsx",
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        conversion_options={
            "source_key": source_key,
            "engine": engine,
            "name": name,
            "created_by": user.sub,
        },
        derived_key=derived_key,
        force_rebuild=True,
        target_capability=target_capability,
    )
    return JSONResponse({"job_id": job.job_id, "derived_key": derived_key}, status_code=201)
