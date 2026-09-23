"""Clashes routes: ``/api/scopes/{scope}/clash-check``, ``/clash-detail`` and the
``connection-specs`` listing (Decision 10, Phase 6).

THE ROUTE NEVER OPENS THE SOURCE. Composing a derived key costs a ``head()`` at most (a cheap
version discriminator, same rationale as the worker's own source-blob cache); the worker is what
downloads the file and computes a real content hash, because that is the one place the bytes are
already local (see ``formats/clash_check.py``).

Cache discipline mirrors ``POST /assets/build`` exactly: identical ``(source_key, options)`` ->
identical derived key, so a repeat is answered from the store without enqueueing anything.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

# The two LIGHT modules only: this router is imported at startup by the slim api, which has no
# numpy and no reader. `ada.clash.identify` (and the passes it imports) runs on a worker.
from ada.clash.builtin_names import BUILTIN_SPEC_NAMES
from ada.clash.options import ClashOptions
from ada.clash.result import ClashResultError, parse_clash_result

from .. import auth as auth_module
from ..auth import User
from ..catalog import merge_catalog_specs
from ..job_transport import JobRequest
from ..queue import capability_token
from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()


def _options_from_body(raw: object) -> ClashOptions:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="'options' must be an object")
    try:
        return ClashOptions(
            out_of_plane_tol=float(raw.get("out_of_plane_tol", 0.1)),
            point_tol=float(raw.get("point_tol", 1e-5)),
            root=raw.get("root") or None,
            include_plate_joints=bool(raw.get("include_plate_joints", True)),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"bad options: {exc}") from exc


async def _source_content_token(ctx: RestContext, scope: Scope, source_key: str) -> str:
    """A cheap token that changes when the source's bytes change, used ONLY to shape the derived
    key -- never stored as the result's ``source_sha256`` (the worker computes a real one from
    the downloaded bytes; see ``formats/clash_check.py``). ``head()``'s ``e_tag`` is a real
    content discriminator when the backend reports one (S3/Garage always do); falling back to the
    key itself means a same-content re-upload under a NEW key is a cache MISS, which is the safe
    direction -- never serving a stale check silently."""
    head = await ctx.storage.head(scope, source_key)
    token = (head or {}).get("e_tag") or source_key
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:16]


def _options_token(options: ClashOptions) -> str:
    return hashlib.sha256(json.dumps(options.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()[:16]


@router.post("/scopes/{scope}/clash-check")
async def api_clash_check(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Identify, classify and group the joints in ``source_key``'s model. Body:
    ``{source_key, options?}``. Returns ``{job_id, derived_key, cached}``.

    ``source_key`` is a SOURCE, never a GLB (see ``ada.clash.identify``'s module docstring): the
    same key the scene was loaded from, an IFC/Genie-XML/FEM file, or a compiled procedural
    model's neutral artifact. A source whose reader yields no members finishes ``done`` with
    ``counts.members == 0`` and a warning -- "this source cannot be checked" is an answer, not an
    error.
    """
    source_key = str(body.get("source_key") or "")
    if not source_key:
        raise HTTPException(status_code=400, detail="'source_key' is required")
    options = _options_from_body(body.get("options"))

    ctx.jobs.require("clash_check")

    key_token = await _source_content_token(ctx, scope_obj, source_key)
    options_token = _options_token(options)
    prefix = f"_derived/clash/{key_token}/{options_token}"
    derived_key = f"{prefix}/result.json"

    if not bool(body.get("force")) and await ctx.storage.exists(scope_obj, derived_key):
        return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True})

    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=source_key,
            target_format="clash_check",
            scope=scope_obj,
            feature="clash_check",
            derived_prefix=prefix,
            derived_key=derived_key,
            conversion_options={"source_key": source_key, "options": options.to_dict()},
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "clash_check",
            key=derived_key,
            target_format="clash_check",
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "cached": False})


async def _spec_capability(ctx: RestContext, spec_name: str) -> str | None:
    """The capability to route a ``clash_detail`` job to: ``None`` for a built-in (it runs on
    the default pool -- ``ada.clash.builtin_specs``), else whatever the live ``connection_specs``
    heartbeat currently advertises for this exact spec name. An unresolvable non-builtin name is
    left as ``None`` too: the default pool will then fail the job with a clear "spec not
    registered here" error (see ``formats/clash_detail.py``) rather than this route guessing at a
    pool that may not exist.
    """
    if spec_name in BUILTIN_SPEC_NAMES:
        return None
    specs = await ctx.jobs.advertised_specs("connection_specs")
    entry = specs.get(spec_name)
    cap = (entry or {}).get("capability")
    return capability_token(cap) if isinstance(cap, str) and cap.strip() else None


@router.post("/scopes/{scope}/clash-detail")
async def api_clash_detail(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Detail one group of joints with a registered spec. Body:
    ``{result_key, joint_ids, spec, options?}``. Returns ``{job_id, derived_key, glb_key}``.

    Routed to the spec's CAPABILITY when it has one (an out-of-tree generator, advertised live);
    to the default pool when it does not (a built-in). ``joint_ids`` are validated against the
    CACHED result before anything is enqueued, so a stale selection fails fast with a 404 naming
    the missing ids rather than a job that errors two polls later.
    """
    result_key = str(body.get("result_key") or "")
    joint_ids = sorted({str(j) for j in (body.get("joint_ids") or []) if str(j).strip()})
    spec_name = str(body.get("spec") or "")
    gen_options = body.get("options")
    if gen_options is None:
        gen_options = {}
    if not result_key or not joint_ids or not spec_name:
        raise HTTPException(status_code=400, detail="'result_key', 'joint_ids' (non-empty) and 'spec' are required")
    if not isinstance(gen_options, dict):
        raise HTTPException(status_code=400, detail="'options' must be an object")

    ctx.jobs.require("clash_detail")

    try:
        raw = await ctx.storage.get_bytes(scope_obj, result_key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no clash result at {result_key!r}") from exc
    try:
        result = parse_clash_result(raw)
    except ClashResultError as exc:
        raise HTTPException(status_code=502, detail=f"{result_key}: {exc}") from exc
    if not result.source_key:
        raise HTTPException(status_code=502, detail=f"{result_key}: clash result carries no source_key")

    known_ids = {j.id for j in result.joints}
    absent = [jid for jid in joint_ids if jid not in known_ids]
    if absent:
        raise HTTPException(status_code=404, detail=f"joint id(s) not in this result: {', '.join(absent)}")

    capability = await _spec_capability(ctx, spec_name)

    ids_token = hashlib.sha256("|".join(joint_ids).encode("utf-8")).hexdigest()[:16]
    opts_token = hashlib.sha256(json.dumps(gen_options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    result_token = hashlib.sha256(result_key.encode("utf-8")).hexdigest()[:16]
    prefix = f"_derived/clash/detail/{spec_name}/{result_token}/{ids_token}/{opts_token}"
    derived_key = f"{prefix}/result.stats.json"
    glb_key = f"{prefix}/model.glb"

    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=result.source_key,
            target_format="clash_detail",
            scope=scope_obj,
            feature="clash_detail",
            derived_prefix=prefix,
            derived_key=derived_key,
            conversion_options={
                "result_key": result_key,
                "joint_ids": joint_ids,
                "spec": spec_name,
                "options": gen_options,
                "glb_key": glb_key,
            },
            target_capability=capability,
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "clash_detail",
            key=derived_key,
            target_format="clash_detail",
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "glb_key": glb_key})


def _builtin_connection_specs() -> list[dict]:
    """The built-ins core always offers, in the catalog shape ``merge_catalog_specs`` wants --
    the ``code`` half of the union, exactly as ``builtin_plugin_specs()`` /
    ``builtin_detailing_engine_specs()`` are for their own listings."""
    try:
        from ada.api.connections.spec import all_registered, spec_to_form_schema
        from ada.clash.builtin_specs import register_builtin_specs
    except ImportError:
        # The SLIM api has no modelling stack, so it cannot DESCRIBE a spec's roles -- and does
        # not need to: every pool that can run one advertises it on its heartbeat, and the union
        # below is what the panel reads. Returning nothing here is the honest answer from a
        # process that cannot answer, not a claim that core registers none.
        return []

    register_builtin_specs()
    return [
        {
            "slug": reg.spec.name,
            "name": reg.spec.name,
            "tags": sorted(reg.spec.tags),
            "priority": reg.spec.priority,
            "roles": spec_to_form_schema(reg.spec),
            "capability": None,
        }
        for reg in all_registered()
        if reg.spec.name in BUILTIN_SPEC_NAMES
    ]


@router.get("/scopes/{scope}/clash-check/connection-specs")
async def api_connection_specs(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Registered connection specs: the built-ins core always offers, unioned with whatever a
    live (non-stale) pool currently advertises via its heartbeat's ``connection_specs`` -- the
    same "appears only while its pool is online" contract the detailing-engine and blueprint
    dropdowns already have (``routes/plugins.py``). The panel reads this to know which specs
    COULD be offered before it has ever run a check; a joint's own ``applicable[]`` (from
    ``POST /clash-check``) is the authority on which ones actually bind a given joint.
    """
    by_slug = merge_catalog_specs(
        _builtin_connection_specs(),
        await ctx.jobs.advertised_specs("connection_specs"),
        project=lambda slug, spec, origin: {**spec, "slug": slug, "origin": origin},
    )
    return JSONResponse({"connection_specs": list(by_slug.values())})
