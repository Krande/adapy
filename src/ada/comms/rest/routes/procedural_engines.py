"""Registry of pluggable procedural-modelling engines
(``/scopes/{scope}/procedural-engines...``).

The built-in ``adapy-default`` engine (in-repo compile_procedural_doc, runnable in
the browser via the adapy wheel) is always present — unioned in with an
``origin`` tag — so a scope with no DB rows still offers one engine. Engines a
live worker advertises, and on a queue-less server the engines this process
registered, are listed after the scope's rows.

Needs the app's storage and queue through :class:`~.deps.RestContext`. Extracted
from ``create_app``; see ``routes/__init__`` for the pattern. The only fixed leaf,
``/resolve``, sits under ``{engine_id}``, so nothing is shadowed.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..queue import JobQueue
from ..scope import Scope
from .deps import (
    RestContext,
    live_worker_specs,
    require_catalog_pool,
    rest_context,
    scope_from_path,
)

router = APIRouter()

_BUILTIN_ENGINE = {
    "id": "builtin:adapy-default",
    "slug": "adapy-default",
    "name": "adapy default",
    "description": "Built-in adapy procedural engine (runs server-side and in-browser via WASM).",
    "revision": 0,
    "origin": "builtin",
    # Built-ins compile a single model-level blueprint; cell grouping is a
    # capability engine feature, advertised via the worker heartbeat.
    "supports_grouping": False,
    "doc": {"kind": "builtin", "entrypoint": "ada.topo_model.wasm_compile:compile_doc", "supports_excel": True},
}
# A second built-in: the diagnostic ``echo`` engine (renders the document's
# cells as raw boxes). It exercises the full engine-selection path — resolve a
# ``module:callable`` entrypoint and dispatch to it — on both the server and
# WASM compile, without needing an external wheel. Kept ada-free here (slim API).
_ECHO_ENGINE = {
    "id": "builtin:echo",
    "slug": "echo",
    "name": "echo (raw cells)",
    "description": "Diagnostic engine: renders the document's cells as raw boxes (no structure).",
    "revision": 0,
    "origin": "builtin",
    "supports_grouping": False,
    # No Excel format: the export/import routes read this off the manifest, as they
    # read a registered engine's ``supports_excel``.
    "doc": {"kind": "builtin", "entrypoint": "ada.topo_model.echo_engine:compile_doc", "supports_excel": False},
}
_BUILTIN_ENGINES = [_BUILTIN_ENGINE, _ECHO_ENGINE]
_BUILTIN_ENGINE_IDS = {e["id"] for e in _BUILTIN_ENGINES}
_BUILTIN_ENGINE_SLUGS = {e["slug"] for e in _BUILTIN_ENGINES}
_BUILTIN_ENGINE_BY_ID = {e["id"]: e for e in _BUILTIN_ENGINES}
_BUILTIN_ENGINE_BY_SLUG = {e["slug"]: e for e in _BUILTIN_ENGINES}


def builtin_engine_doc(slug: str | None) -> dict | None:
    """A built-in engine's manifest doc (a copy), or None for any other slug."""
    engine = _BUILTIN_ENGINE_BY_SLUG.get(slug or "")
    return dict(engine["doc"]) if engine is not None else None


@lru_cache(maxsize=1)
def _default_field_help() -> dict[str, dict[str, str]]:
    """adapy's own column help (``ada.topology.entities.entity_field_help``); ``{}`` on a slim
    API without ``ada.topology``. Built once: the entities do not change while the server runs."""
    try:
        from ada.topology.entities import entity_field_help
    except Exception:
        return {}
    return entity_field_help()


def field_help_for(spec: dict | None) -> dict[str, dict[str, str]]:
    """An engine's field help for the viewer: adapy's descriptions, under the engine's own
    (``spec["field_help"]``, however it got onto the spec -- the registration's keyword or a
    tool writing it there) winning per column. ``{list: {COLUMN: text}}``; a list or a text
    that is not of that shape is passed over."""
    out = {key: dict(rows) for key, rows in _default_field_help().items()}
    own = spec.get("field_help") if isinstance(spec, dict) else None
    if isinstance(own, dict):
        for key, rows in own.items():
            if not isinstance(key, str) or not isinstance(rows, dict):
                continue
            merged = out.setdefault(key, {})
            merged.update(
                {str(col): str(text) for col, text in rows.items() if isinstance(col, str) and text not in (None, "")}
            )
    return {key: rows for key, rows in out.items() if rows}


async def _get_engine_in_scope(pool, engine_id: str, scope_obj: Scope) -> dict:
    try:
        row = await db_module.get_procedural_engine(pool, engine_id)
    except Exception:
        row = None
    if row is None or row["scope_kind"] != scope_obj.kind or (row["scope_id"] or None) != (scope_obj.id or None):
        raise HTTPException(status_code=404, detail="procedural engine not found")
    return row


def local_engine_specs(queue: JobQueue) -> dict[str, dict]:
    """Engines registered in THIS process, on a server with no queue (a local viewer).

    A worker advertises the engines it can run on its heartbeat; a queue-less server
    has no workers, and compiles in-process (``local_jobs.start_procedural_build``),
    so the engines it can run are the ones its own process registered - a tool's
    package does that at import (``register_procedural_engine_capabilities`` with a
    name and an entrypoint, via ``ADA_WORKER_PRELOAD`` or an ``ada.plugins`` entry
    point). Only offerable specs, as for workers. ``{}`` with a queue, and on a slim
    API without ``ada.topo_model``.
    """
    if queue.enabled:
        return {}
    try:
        from ada.comms.engine_specs import is_offerable
        from ada.topo_model.engine_catalog import procedural_engine_specs
    except Exception:
        return {}
    return {s["slug"]: s for s in procedural_engine_specs() if s.get("slug") and is_offerable(s)}


_LOCAL_ENGINE_PREFIX = "local:"


def _local_engine(queue: JobQueue, engine_id: str) -> dict | None:
    """The descriptor of a ``local:<slug>`` engine id, or None."""
    if not engine_id.startswith(_LOCAL_ENGINE_PREFIX):
        return None
    spec = local_engine_specs(queue).get(engine_id[len(_LOCAL_ENGINE_PREFIX) :])
    if spec is None:
        return None
    return {
        "id": engine_id,
        "slug": spec["slug"],
        "name": spec["name"],
        "description": spec.get("description", ""),
        "revision": 0,
        "origin": "local",
        "supports_grouping": bool(spec.get("supports_grouping", False)),
        "field_help": field_help_for(spec),
        "doc": {"kind": "server", "entrypoint": spec["entrypoint"]},
    }


@router.get("/scopes/{scope}/procedural-engines")
async def api_procedural_engines_list(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    # No database (a local viewer): no registered rows, but the built-ins and the
    # engines this process registered are still the honest answer.
    pool = getattr(request.app.state, "db_pool", None)
    engines = (
        await db_module.list_procedural_engines(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)
        if pool is not None
        else []
    )
    for e in engines:
        e["origin"] = "db"
    # Built-ins first, then the scope's registered engines.
    summaries = [
        {k: e[k] for k in ("id", "slug", "name", "description", "revision", "origin", "supports_grouping")}
        for e in _BUILTIN_ENGINES
    ]
    # Fold each engine's advertised capability flags (from live, non-stale
    # workers) onto its summary by slug — this is how a DB-registered capability
    # engine reports ``supports_grouping=True`` while its worker is
    # up. A slug with no live spec defaults to non-grouping. Built-ins carry
    # their static flag above and are overridden only if a worker re-announces.
    engine_caps = await live_worker_specs(ctx.queue, "procedural_engine_specs")
    for summary in (*summaries, *engines):
        spec = engine_caps.get(summary.get("slug"))
        summary["supports_grouping"] = bool(
            spec.get("supports_grouping") if spec is not None else summary.get("supports_grouping", False)
        )
        # What the doc's columns mean (the builder's field help): adapy's, under a live worker's
        # text for this engine. On every engine, so the viewer reads one place.
        summary["field_help"] = field_help_for(spec)

    # Engines a live worker advertises in full. A worker can only honestly
    # announce an engine whose code it has, so this is the authoritative
    # answer to "what can actually run right now" -- and it removes the
    # manual step of creating a row per scope for an engine that is already
    # installed and already reachable.
    #
    # Only specs carrying a name AND an entrypoint qualify (see
    # ada.comms.engine_specs.is_offerable): older workers advertise capability flags
    # alone, and surfacing one of those would offer an engine the viewer
    # cannot dispatch to.
    #
    # Built-ins and DB rows win on slug collision. A row is an explicit
    # admin decision -- possibly pinning a different entrypoint or revision --
    # and must not be silently overridden by whatever a pod happens to run.
    from ada.comms.engine_specs import is_offerable

    known = {*_BUILTIN_ENGINE_SLUGS, *(e.get("slug") for e in engines)}
    advertised = [
        {
            "id": f"worker:{spec['slug']}",
            "slug": spec["slug"],
            "name": spec["name"],
            "description": spec.get("description", ""),
            "revision": 0,
            "origin": "worker",
            "supports_grouping": bool(spec.get("supports_grouping", False)),
            "field_help": field_help_for(spec),
        }
        for slug, spec in sorted(engine_caps.items())
        if slug not in known and is_offerable(spec)
    ]
    known |= {e["slug"] for e in advertised}
    local = [
        {k: v for k, v in d.items() if k != "doc"}
        for slug in sorted(local_engine_specs(ctx.queue))
        if slug not in known and (d := _local_engine(ctx.queue, f"{_LOCAL_ENGINE_PREFIX}{slug}")) is not None
    ]
    return JSONResponse({"procedural_engines": [*summaries, *engines, *advertised, *local]})


@router.post("/scopes/{scope}/procedural-engines", status_code=201)
async def api_procedural_engines_create(
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
    if slug in _BUILTIN_ENGINE_SLUGS:
        raise HTTPException(status_code=409, detail=f"{slug!r} is a reserved built-in engine slug")
    desc = body.get("description")
    row = await db_module.create_procedural_engine(
        pool,
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        slug=slug,
        name=name.strip(),
        description=desc if isinstance(desc, str) else None,
        created_by=user.sub,
    )
    if row is None:
        raise HTTPException(status_code=409, detail=f"a procedural engine with slug {slug!r} already exists")
    return JSONResponse(row, status_code=201)


@router.get("/scopes/{scope}/procedural-engines/{engine_id}")
async def api_procedural_engines_get(
    request: Request,
    engine_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    if engine_id in _BUILTIN_ENGINE_IDS:
        return JSONResponse(_BUILTIN_ENGINE_BY_ID[engine_id])
    local = _local_engine(ctx.queue, engine_id)
    if local is not None:
        return JSONResponse(local)
    pool = require_catalog_pool(request)
    row = await _get_engine_in_scope(pool, engine_id, scope_obj)
    return JSONResponse(
        {k: row[k] for k in ("id", "slug", "name", "description", "doc", "revision", "created_by", "updated_at")}
    )


@router.get("/scopes/{scope}/procedural-engines/{engine_id}/resolve")
async def api_procedural_engines_resolve(
    request: Request,
    engine_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Resolve an engine to a BROWSER-runnable descriptor for the in-browser
    (Pyodide) compile: a built-in engine returns its slug + entrypoint; a
    ``kind:wheel`` engine returns its ``module:callable`` entrypoint, the
    ``pyodide_deps`` to micropip-install, and a presigned ``wheel_url`` (when
    the wheel has been built — ``ready``). A ``kind:server`` engine is not
    browser-runnable (``ready: false``)."""

    def _builtin(b: dict) -> JSONResponse:
        return JSONResponse({"kind": "builtin", "slug": b["slug"], "entrypoint": b["doc"]["entrypoint"], "ready": True})

    if engine_id in _BUILTIN_ENGINE_IDS:
        return _builtin(_BUILTIN_ENGINE_BY_ID[engine_id])
    local = _local_engine(ctx.queue, engine_id)
    if local is not None:
        # Compiled by this server, never in the browser.
        return JSONResponse({"kind": "server", "entrypoint": local["doc"]["entrypoint"], "ready": False})
    pool = require_catalog_pool(request)
    row = await _get_engine_in_scope(pool, engine_id, scope_obj)
    doc = row.get("doc") or {}
    kind = doc.get("kind", "builtin")
    if kind == "builtin":
        return JSONResponse(
            {"kind": "builtin", "slug": row["slug"], "entrypoint": doc.get("entrypoint"), "ready": True}
        )
    if kind == "wheel":
        wheel_key = doc.get("wheel_key")
        wheel_url = None
        ready = False
        if wheel_key and ctx.storage.supports_presigned_uploads:
            meta = await ctx.storage.head(scope_obj, wheel_key)
            if meta is not None:
                wheel_url = await ctx.storage.presigned_get_url(scope_obj, wheel_key, expires_in_seconds=15 * 60)
                ready = True
        return JSONResponse(
            {
                "kind": "wheel",
                "entrypoint": doc.get("entrypoint"),
                "pyodide_deps": doc.get("pyodide_deps") or [],
                "wheel_url": wheel_url,
                "ready": ready,
            }
        )
    # server (or unknown): native-only, not runnable in the browser.
    return JSONResponse({"kind": kind, "entrypoint": doc.get("entrypoint"), "ready": False})


@router.put("/scopes/{scope}/procedural-engines/{engine_id}")
async def api_procedural_engines_update(
    request: Request,
    engine_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    import asyncpg

    from ..catalog import slugify, validate_engine_doc

    if engine_id in _BUILTIN_ENGINE_IDS:
        raise HTTPException(status_code=403, detail="the built-in engine is read-only")
    pool = require_catalog_pool(request)
    row = await _get_engine_in_scope(pool, engine_id, scope_obj)
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
    if slug in _BUILTIN_ENGINE_SLUGS:
        raise HTTPException(status_code=409, detail=f"{slug!r} is a reserved built-in engine slug")
    desc = body.get("description")
    try:
        normalized = validate_engine_doc(doc)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid engine doc: {e}")
    try:
        new_rev = await db_module.update_procedural_engine(
            pool,
            engine_id,
            slug=slug,
            name=name.strip(),
            description=desc if isinstance(desc, str) else None,
            doc=normalized,
            base_revision=base_revision,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"a procedural engine with slug {slug!r} already exists")
    if new_rev is None:
        current = await db_module.get_procedural_engine(pool, engine_id)
        raise HTTPException(
            status_code=409,
            detail={"message": "revision conflict", "current_revision": current["revision"] if current else None},
        )
    # A kind:wheel engine needs its wheel (re)built from the repo whenever the
    # manifest changes; enqueue the build worker (it clones + builds + stores
    # the wheel under _engines/ and records wheel_key). force so a manifest
    # edit at an unchanged spot still rebuilds.
    if normalized.get("kind") == "wheel" and ctx.queue.enabled:
        from ..procedural import engine_wheel_dir

        await ctx.queue.enqueue(
            f"_synthetic/engine-build/{engine_id}/r{new_rev}",
            target_format="procedural_engine_build",
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            conversion_options={"engine_id": engine_id},
            derived_key=engine_wheel_dir(engine_id),
            force_rebuild=True,
        )
    return JSONResponse({"id": row["id"], "revision": new_rev})


@router.delete("/scopes/{scope}/procedural-engines/{engine_id}")
async def api_procedural_engines_delete(
    request: Request,
    engine_id: str,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    if engine_id in _BUILTIN_ENGINE_IDS:
        raise HTTPException(status_code=403, detail="the built-in engine cannot be deleted")
    pool = require_catalog_pool(request)
    await _get_engine_in_scope(pool, engine_id, scope_obj)
    ok = await db_module.archive_procedural_engine(pool, engine_id)
    if not ok:
        raise HTTPException(status_code=404, detail="procedural engine not found")
    return JSONResponse({"status": "archived"})
