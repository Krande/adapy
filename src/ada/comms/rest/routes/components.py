"""Connection-component panel (``/components/*``):

* ``GET /components/profiles?category=...``   — section dropdown data
* ``GET /components/specs?scope=...&branch=`` — preview gallery from the latest
  ada-build manifest on a branch
* ``POST /components/build``                  — on-demand build job for
  user-tweaked inputs

The build-status poll reuses ``/convert/{job_id}`` (routes/convert.py) since
component jobs flow through the same job transport.

Needs the app's storage and job transport through :class:`~.deps.RestContext`.
Extracted from ``create_app``; see ``routes/__init__`` for the pattern. All three
paths are fixed, so registration order does not matter.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..components_manifest import (
    _scope_url_segment,
    expose_manifest,
    resolve_latest_manifest,
)
from ..job_transport import JobRequest
from ..scope import Scope
from ..scope import can_access as scope_can_access
from .deps import RestContext, parse_scope, resolve_project_scope, rest_context

router = APIRouter()


# ada.sections.profile_lookup is imported lazily — the viewer image
# ships a minimal /app/src/ada/ layout (only ada.config + ada.comms)
# and a top-level import here would crash uvicorn on startup. Keep
# the import inside the handler so the rest of the API still serves
# if profile_lookup or its dep chain isn't present.
@router.get("/components/profiles")
async def api_components_profiles(category: str | None = None) -> JSONResponse:
    try:
        from ada.sections.profile_lookup import (
            list_categories as _list_section_categories,
        )
        from ada.sections.profile_lookup import (
            load_profiles_by_category as _load_profiles_by_category,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"section catalogue unavailable: {exc}",
        )
    if category is None:
        return JSONResponse({"categories": _list_section_categories()})
    profiles = _load_profiles_by_category(category)
    return JSONResponse({"category": category, "profiles": profiles})


@router.get("/components/specs")
async def api_components_specs(
    request: Request,
    branch: str | None = None,
    scope: str | None = None,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """List published component specs.

    Auto-discovery: with no ``scope`` query param, scans every
    scope the caller can access (personal, shared, all
    memberships) and aggregates whichever happen to have a
    manifest published. Each returned spec entry carries the
    ``scope`` it was found in so the frontend can resolve preview
    URLs and route builds correctly.

    Likewise without a ``branch`` query param, ``versions/`` is
    scanned wholesale and the newest manifest anywhere in each
    scope wins — the bake project may not have ever published to
    ``main`` and the viewer shouldn't have to guess the right
    branch name. Pass ``?branch=...`` when you need to pin to a
    specific bake branch (tests, direct API consumers).

    Explicit override: ``?scope=...`` restricts the lookup to one
    scope — useful for tests and direct API consumers that don't
    want the full sweep.

    Name collisions across scopes: first-found wins. Order is
    personal → shared → projects (matches /api/me ordering).
    """
    pool = getattr(request.app.state, "db_pool", None)
    if scope is not None:
        scope_obj = parse_scope(scope, user)
        scope_obj = await resolve_project_scope(pool, scope_obj)
        if not await scope_can_access(user, scope_obj, pool):
            raise HTTPException(status_code=403, detail="forbidden")
        candidate_scopes: list[Scope] = [scope_obj]
    else:
        candidate_scopes = [Scope.user(user.sub), Scope.shared()]
        if pool is not None:
            for p in await db_module.list_user_projects(pool, user.sub):
                candidate_scopes.append(Scope.project(p.id))

    sources: list[dict] = []
    all_specs: dict[str, dict] = {}
    for cand in candidate_scopes:
        try:
            resolved = await resolve_latest_manifest(ctx.storage, cand, branch)
        except Exception as exc:
            logger.debug("components/specs: skipping scope %s (%s)", cand, exc)
            continue
        if resolved is None:
            continue
        scope_str = _scope_url_segment(cand)
        sources.append({"scope": scope_str, "branch": resolved.branch, "commit": resolved.commit})
        exposed = expose_manifest(resolved, cand)
        for name, entry in exposed["specs"].items():
            if name in all_specs:
                continue  # first scope to publish this name wins
            spec_entry = dict(entry)
            spec_entry["scope"] = scope_str
            spec_entry["branch"] = resolved.branch
            all_specs[name] = spec_entry

    return JSONResponse({"branch": branch, "sources": sources, "specs": all_specs})


@router.post("/components/build")
async def api_components_build(
    request: Request,
    scope: str = "shared",
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Enqueue an on-demand component build for user-tweaked inputs.

    Body: ``{"spec_name": str, "inputs": dict, "name": str | None,
    "extra_handler_kwargs": dict | None}``. Returns ``{"job_id":
    str}``; poll status via the existing ``GET /api/convert/{job_id}``.
    Result GLB lands at the job's derived_key and is fetchable via
    ``GET /api/scopes/{scope}/blobs/{derived_key}``.
    """
    ctx.jobs.require("component_build")
    body = await request.json()
    spec_name = body.get("spec_name")
    if not isinstance(spec_name, str) or not spec_name:
        raise HTTPException(status_code=400, detail="spec_name (str) is required")
    inputs = body.get("inputs")
    if not isinstance(inputs, dict):
        raise HTTPException(status_code=400, detail="inputs (dict) is required")
    component_name = body.get("name")
    extra_kwargs = body.get("extra_handler_kwargs") or {}
    if not isinstance(extra_kwargs, dict):
        raise HTTPException(status_code=400, detail="extra_handler_kwargs must be a dict")

    scope_obj = parse_scope(scope, user)
    scope_obj = await resolve_project_scope(getattr(request.app.state, "db_pool", None), scope_obj)
    if not await scope_can_access(user, scope_obj, getattr(request.app.state, "db_pool", None)):
        raise HTTPException(status_code=403, detail="forbidden")

    # No source file for component_build — use a synthetic source_key
    # that captures spec_name + inputs hash so cache hits work for
    # identical configurations (frontend submitting the same form
    # twice should not double-build). derived_key is the produced
    # GLB blob.
    import hashlib as _hashlib

    inputs_hash = _hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    source_key = f"_synthetic/component_build/{spec_name}/{inputs_hash}"
    derived_key = f"_derived/component_builds/{spec_name}/{inputs_hash}.glb"

    # Capability resolution: caller-supplied wins (frontend forwards
    # the manifest's ``capability`` from the spec entry). Otherwise
    # re-resolve the manifest in this scope and use its top-level
    # capability so the right worker pool picks the job up. Falls
    # back to the default pool when the manifest doesn't declare one
    # (the spec must then be registered on the base worker — built-in
    # adapy specs).
    target_capability = body.get("capability")
    if not isinstance(target_capability, str) or not target_capability.strip():
        target_capability = None
        resolved = await resolve_latest_manifest(ctx.storage, scope_obj, branch=None)
        if resolved is not None:
            manifest_cap = resolved.body.get("capability")
            if isinstance(manifest_cap, str) and manifest_cap.strip():
                target_capability = manifest_cap.strip().lower()

    job = await ctx.jobs.submit(
        JobRequest(
            source_key=source_key,
            target_format="component_build",
            scope=scope_obj,
            feature="component_build",
            conversion_options={
                "spec_name": spec_name,
                "inputs": inputs,
                "name": component_name,
                "extra_handler_kwargs": extra_kwargs,
            },
            derived_key=derived_key,
            target_capability=target_capability,
        )
    )
    return JSONResponse({"job_id": job.job_id, "derived_key": derived_key})
