"""Plugin + catalog listing routes: ``GET /api/plugins`` and the seven
``/api/scopes/{scope}/procedural-models/<catalog>`` dropdown listings.

Every listing is the same union (``ada.comms.rest.catalog.merge_catalog_specs``):
static built-ins, what live workers advertise (``live_worker_specs``), and —
for equipment/system types — the per-scope DB catalog. The plugin admin gate
(``plugin_ids_gated_by_config``) lives here because ``/api/plugins`` reports it
and ``create_app``'s plugin-job POST enforces it.

The first route group extracted from ``create_app``; see ``routes/__init__``
for the pattern.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import db as db_module
from ..catalog import merge_catalog_specs, overlay_catalog_rows, sort_by_name
from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()


# Plugin jobs an admin must be to enqueue, named by plugin id. Admin-only to
# write like every other non-`public.` setting, and deliberately NOT under
# `public.` — who may run a job is not a read window for every user.
#
# This exists because the plugin's own advertisement cannot be the only
# source. A spec is advertised BY THE WORKER (`_live_worker_specs`), so a
# worker running an older build that predates the flag advertises no flag,
# and a gate that reads only the advertisement would silently disappear —
# the deployment would look protected and be open, which is the failure
# this whole mechanism exists to prevent. Worker registry rows are also not
# currently unforgeable (see the `$KV.ada-viewer-jobs.>` note in adapy's
# worker-trust docs), so an advertisement is a statement of intent, not an
# authorization decision.
#
# The two sources are therefore OR-ed, never AND-ed: adding a source can
# only ever TIGHTEN. A stale worker cannot open a gate the deployment set,
# and a deployment can gate a plugin that never declared anything.
PLUGIN_JOB_ADMIN_SETTING = "admin.plugin_jobs.require_admin"


async def plugin_ids_gated_by_config(pool) -> set[str] | None:
    """Plugin ids the DEPLOYMENT says are admin-only.

    Returns ``None`` for "the setting exists but could not be read", which
    callers must treat as *every* plugin job requiring admin. Failing closed
    on a malformed value is deliberate: the alternative is a typo quietly
    removing a gate, and an over-tight gate announces itself immediately
    while an absent one does not.
    """
    if pool is None:
        return set()
    try:
        raw = await db_module.get_setting(pool, PLUGIN_JOB_ADMIN_SETTING)
    except Exception:
        # Could not ask. Same answer as a value we could not parse: gate
        # everything. A gate must never become a 500 — that turns "the
        # database hiccuped" into "the endpoint is broken" — and it must
        # never resolve an error to "allowed", which would make an outage
        # into a silent removal of the restriction.
        logger.exception(
            "api: could not read %s; treating every plugin job as admin-only",
            PLUGIN_JOB_ADMIN_SETTING,
        )
        return None
    if raw is None or not raw.strip():
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        # Not JSON. Accept the shape a person types into a settings box
        # rather than rejecting it, since rejecting means failing closed.
        parts = [p.strip() for p in raw.replace(",", " ").split()]
        return {p for p in parts if p} or None
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list) or any(not isinstance(p, str) for p in parsed):
        return None
    return {p.strip() for p in parsed if p and p.strip()}


@router.get("/plugins")
async def api_plugins(request: Request, ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """Backend plugins advertised to the viewer plugin system: the union of
    the static built-ins (``builtin_plugin_specs`` — empty in core) and any a
    live (non-stale) worker advertises via ``plugin_specs`` (its
    ``ADA_WORKER_PRELOAD`` / ``ada.plugins`` entry point registered it with
    ``register_plugin_backend``), keyed by ``slug`` and tagged ``origin``
    ``code``/``db`` + ``online:true``. No hardcoded plugins: a backend plugin
    appears only while a pool that provides it is online — the same
    self-describing contract the procedural/detailing engines use. The
    frontend build-time registry can seed off this so a runtime-only backend
    plugin still surfaces."""
    from ..catalog import builtin_plugin_specs

    # With no queue, plugin jobs run in THIS process (see `local_jobs`), so
    # what it registered is online by definition — and the only source there
    # is. Only then: behind a queue a job goes to a worker, and a spec this
    # API happens to have imported says nothing about whether one is up. Both
    # halves of that are the transport's to answer, so this route does not
    # ask which one it is in.
    by_slug = merge_catalog_specs(
        builtin_plugin_specs(),
        await ctx.jobs.advertised_specs("plugin_specs"),
        project=lambda slug, spec, origin: {**spec, "slug": slug, "origin": origin, "online": True},
        live_origin="db",
        local_specs=ctx.jobs.local_specs(),
    )

    # `requires_admin` is reported as the EFFECTIVE gate, not merely what a
    # worker declared: a deployment can gate a plugin that declared nothing
    # (see PLUGIN_JOB_ADMIN_SETTING). A UI that hid its button on the
    # declaration alone would offer an action the API then refuses, which
    # reads as a broken button rather than as a permission.
    #
    # This is an affordance, never the gate. The gate is in the POST.
    pool = getattr(request.app.state, "db_pool", None)
    gated = await plugin_ids_gated_by_config(pool)
    for slug, spec in by_slug.items():
        spec["requires_admin"] = bool(spec.get("requires_admin")) or gated is None or slug in gated
    return JSONResponse({"plugins": list(by_slug.values())})


@router.get("/scopes/{scope}/procedural-models/equipment-types")
async def api_procedural_equipment_types(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Equipment types for the cellbuilder's add-equipment dropdown: the
    union of code-defined archetypes (advertised by live workers) and the
    per-scope DB catalog, each tagged with its ``origin`` (``code`` or
    ``catalog``) and its port list (name/direction/category plus local
    position/direction_vector/colour — used by the viewer's missing-input
    overlay and the port-glyph overlay). A slug present in both is shown as
    ``catalog`` — the editable copy shadows the built-in."""

    def _ports_of(doc: dict | None) -> list[dict]:
        out = []
        for p in (doc or {}).get("ports") or []:
            if isinstance(p, dict) and p.get("name"):
                out.append(
                    {
                        "name": p["name"],
                        "direction": p.get("direction", "INOUT"),
                        "category": p.get("category", "process"),
                        "position": p.get("position") or [0.0, 0.0, 0.0],
                        "direction_vector": p.get("direction_vector") or [0.0, 0.0, 1.0],
                        "color": p.get("color"),
                    }
                )
        return out

    by_slug = merge_catalog_specs(
        [],
        await ctx.jobs.advertised_specs("procedural_equipment_specs", "procedural_equipment_types"),
        project=lambda slug, spec, origin: {
            "slug": slug,
            "name": spec.get("name") or slug,
            "origin": origin,
            "ports": _ports_of(spec.get("doc")),
            "has_cad": False,
        },
    )
    pool = getattr(request.app.state, "db_pool", None)
    if pool is not None:
        # ``list_equipment_types`` returns summary rows only (no ``doc``), so
        # fetch the docs separately to project each catalog type's ports.
        docs_by_slug = await db_module.get_equipment_docs_by_scope(
            pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id
        )
        overlay_catalog_rows(
            by_slug,
            await db_module.list_equipment_types(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id),
            lambda slug, t: {
                "slug": slug,
                "name": t.get("name") or slug,
                "origin": "catalog",
                "id": t["id"],
                "ports": _ports_of(docs_by_slug.get(slug)),
                # Whether a CAD asset is linked — drives the selected-object
                # "Show as CAD" toggle (which loads this type's preview GLB).
                "has_cad": bool(t.get("cad_key")),
            },
        )
    return JSONResponse({"equipment_types": sort_by_name(by_slug.values())})


@router.get("/scopes/{scope}/procedural-models/system-types")
async def api_procedural_system_types(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """System types for the cellbuilder's systems inspector: the union of
    code-defined system kinds (piping/duct/cable/electrical — static, plus any
    extra kinds advertised by live workers) and the per-scope DB
    system-template catalog, each tagged with its ``origin`` and base kind."""
    from ..catalog import builtin_system_specs

    def _project(slug: str, spec: dict, origin: str) -> dict:
        doc = spec.get("doc") or {}
        return {
            "slug": slug,
            "name": spec.get("name") or slug,
            "origin": origin,
            "type": doc.get("type", slug),
            "medium": doc.get("medium"),
            "voltage": doc.get("voltage"),
        }

    by_slug = merge_catalog_specs(
        builtin_system_specs(),
        await ctx.jobs.advertised_specs("procedural_system_specs", "procedural_system_types"),
        project=_project,
    )
    pool = getattr(request.app.state, "db_pool", None)
    if pool is not None:

        def _project_row(slug: str, t: dict) -> dict:
            doc = t.get("doc") or {}
            return {
                "slug": slug,
                "name": t.get("name") or slug,
                "origin": "catalog",
                "id": t["id"],
                "type": doc.get("type", "piping"),
                "medium": doc.get("medium"),
                "voltage": doc.get("voltage"),
            }

        overlay_catalog_rows(
            by_slug,
            await db_module.list_system_templates(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id),
            _project_row,
        )
    return JSONResponse({"system_types": sort_by_name(by_slug.values())})


@router.get("/scopes/{scope}/procedural-models/design-rulesets")
async def api_procedural_design_rulesets(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Named design rulesets for the cellbuilder's ruleset dropdown: the
    built-in rulesets (static — ``standard``/``route_only``) plus any extra
    advertised by live workers, each tagged ``origin`` ``code``. Selecting
    one sets ``doc.design_rules``, which the compiler resolves to the routing/
    penetration callables (``ada.topo_model.resolve_design_rules``)."""
    from ..catalog import builtin_design_rulesets

    by_slug = merge_catalog_specs(
        builtin_design_rulesets(),
        await ctx.jobs.advertised_specs("procedural_design_rulesets"),
        project=lambda slug, spec, origin: {
            "slug": slug,
            "name": spec.get("name") or slug,
            "description": spec.get("description", ""),
            "origin": origin,
        },
    )
    return JSONResponse({"design_rulesets": sort_by_name(by_slug.values())})


@router.get("/scopes/{scope}/procedural-models/cell-types")
async def api_procedural_cell_types(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Space-cell types for the cellbuilder's ``+ Cell`` picker: the union of
    the static built-in blueprints (``adapy-default``) and any advertised by
    live workers (a capability engine registers its own via
    ``register_procedural_cell_type``), each tagged ``origin`` ``code``. Each
    carries a default box extent ``size`` ``(DX, DY, DZ)`` a freshly-placed
    cell is seeded with, plus optional entity ``metadata``. No DB rows are
    involved — like the design rulesets, so the dropdown is never empty for
    want of a worker."""
    from ..catalog import builtin_cell_specs

    by_slug = merge_catalog_specs(
        builtin_cell_specs(),
        await ctx.jobs.advertised_specs("procedural_cell_specs"),
        project=lambda slug, spec, origin: {
            "slug": slug,
            "name": spec.get("name") or slug,
            "origin": origin,
            "size": spec.get("size") or [5.0, 5.0, 3.0],
            "metadata": spec.get("metadata") or {},
        },
    )
    return JSONResponse({"cell_types": sort_by_name(by_slug.values())})


@router.get("/scopes/{scope}/procedural-models/opening-types")
async def api_procedural_opening_types(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Opening types for the cellbuilder's ``+ Opening`` picker: the union of
    the static built-in door/window/opening types (``adapy-default``) and any
    advertised by live workers (via ``register_procedural_opening_type``), each
    tagged ``origin`` ``code``. Each carries its ``subtype`` (``door``/
    ``window``/``opening`` — the reinforcement framing the compiler frames
    around the hole) and the
    default box extent ``size`` ``(DX, DY, DZ)``. No DB rows are involved."""
    from ..catalog import builtin_opening_specs

    by_slug = merge_catalog_specs(
        builtin_opening_specs(),
        await ctx.jobs.advertised_specs("procedural_opening_specs"),
        project=lambda slug, spec, origin: {
            "slug": slug,
            "name": spec.get("name") or slug,
            "origin": origin,
            "subtype": spec.get("subtype") if spec.get("subtype") in ("door", "window", "opening") else "door",
            "size": spec.get("size") or [1.0, 1.0, 2.0],
        },
    )
    return JSONResponse({"opening_types": sort_by_name(by_slug.values())})


@router.get("/scopes/{scope}/procedural-models/blueprints")
async def api_procedural_blueprints(
    request: Request,
    engine: str = "adapy-default",
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Structural blueprints for the cellbuilder's Blueprint dropdown, scoped
    to the compile ``engine`` query param (default ``adapy-default``): the
    union of that engine's static built-ins (``steel_stru``/``none`` for the
    default engine) and any advertised by live workers for the SAME engine (a
    capability engine registers its own via ``register_procedural_blueprint``),
    each tagged ``origin`` ``code``. Selecting one sets the document's
    ``blueprint_name``. The first entry is the engine's default; the list is
    never empty — an engine advertising none falls back to an ``engine
    default`` entry. No DB rows are involved."""
    from ..catalog import builtin_procedural_blueprint_specs

    # Preserve authored order (built-ins first, the FIRST being the default),
    # deduped by slug; live-worker extras append after. Engine-scoped: a live
    # spec carries the engine it belongs to; keep only this engine's (a spec
    # missing ``engine`` is treated as this one).
    by_slug = merge_catalog_specs(
        builtin_procedural_blueprint_specs(engine),
        await ctx.jobs.advertised_specs("procedural_blueprint_specs"),
        project=lambda slug, spec, origin: {
            "slug": slug,
            "name": spec.get("name") or slug,
            "description": spec.get("description", ""),
            "fields": spec.get("fields", []),
            "origin": origin,
        },
        live_filter=lambda spec: spec.get("engine") in (None, engine),
    )
    blueprints = list(by_slug.values())
    if not blueprints:
        # An engine that advertised nothing (offline capability worker) still
        # needs a non-empty dropdown so the compile can proceed.
        blueprints = [
            {
                "slug": "engine-default",
                "name": "Engine default",
                "description": "The engine's default blueprint.",
                "origin": "code",
            }
        ]
    return JSONResponse({"blueprints": blueprints})


@router.get("/scopes/{scope}/procedural-models/detailing-engines")
async def api_procedural_detailing_engines(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Detailing engines for the Compile-settings "Detailing" dropdown: the
    union of the static built-ins (``none`` + ``adapy-default``) and any an
    external (out-of-process) engine a live (non-stale) capability worker
    advertises via ``procedural_detailing_engine_specs``, each tagged ``origin``
    ``code``/``db``. Selecting one is a COMPILE-time choice (not part of the
    document); ``none`` (the default, first) passes no detailing -> structural-
    only GLB. No hardcoded external engines: an external engine appears only
    while its pool is online (modeled on the blueprints/design-rulesets
    dropdowns)."""
    from ..catalog import builtin_detailing_engine_specs

    # External engines are discovered ONLY from live capability workers (their
    # ADA_WORKER_PRELOAD registers the engine, so the heartbeat advertises it).
    # A live worker re-announcing a built-in keeps origin=code; a new
    # (external) engine is worker/db-provided.
    by_slug = merge_catalog_specs(
        builtin_detailing_engine_specs(),
        await ctx.jobs.advertised_specs("procedural_detailing_engine_specs"),
        project=lambda slug, spec, origin: {
            "slug": slug,
            "name": spec.get("name") or slug,
            "description": spec.get("description", ""),
            "inprocess": bool(spec.get("inprocess", False)),
            "worker_capability": spec.get("worker_capability"),
            "joint_types": spec.get("joint_types", []),
            "origin": origin,
            "online": True,
        },
        live_origin="db",
    )
    return JSONResponse({"detailing_engines": list(by_slug.values())})
