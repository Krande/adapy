"""Convex-contact clash runs: ``/api/scopes/{scope}/clash-contacts`` and below.

A clash run measures the members of a viewer GLB (``ada.clash.contacts``) and is kept as a sidecar
(``ada.clash.sidecar``) beside the target it was run on. These routes:

* ``POST /clash-contacts`` -- run the check on a worker over the GLB at ``glb_key``.
* ``POST /clash-contacts/runs`` -- store a run a BROWSER measured (adacpp's wasm module on the same
  GLB bytes). Validated here, written by the same code a worker's run is, and optionally followed by
  an ``annotate`` job that resolves which specs could detail each clash -- the one thing a browser
  has no registry for.
* ``GET /clash-contacts/runs?target=...`` / ``GET /clash-contacts/run?key=...`` -- list a target's
  runs and read one back: a run outlives the page that made it.
* ``POST /clash-contacts/annotate`` -- the specs half alone, for an existing run.
* ``POST /clash-contacts/detail`` -- build named clashes of a run with a registered spec, from the
  stored contacts.

LIGHT, like ``clash_check``'s router: the slim API imports it, so only stdlib modules and
``ada.clash.sidecar`` are imported here; the work runs in a ``clash_contacts`` job.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.clash.sidecar import (
    CLASH_SIDECAR_PREFIX,
    SidecarError,
    index_key,
    parse_index,
    parse_run,
    run_key,
)

from .. import auth as auth_module
from ..auth import User
from ..clash_runs import write_run_and_index
from ..job_transport import JobRequest
from ..scope import Scope
from .clash_check import _spec_capability
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()

KIND = "clash_contacts"
_UP_AXES = ("z", "y")


def _target_from(raw: Any) -> dict:
    """A run's target, normalised: ``{"kind": "file", "source_key"}`` or
    ``{"kind": "node", "collection", "subject", "revision"?, "node"?}``."""
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="'target' must be an object")
    kind = raw.get("kind")
    if kind == "file":
        source_key = str(raw.get("source_key") or "").strip()
        if not source_key:
            raise HTTPException(status_code=400, detail="a file target needs 'source_key'")
        return {"kind": "file", "source_key": source_key}
    if kind == "node":
        collection, subject = str(raw.get("collection") or ""), str(raw.get("subject") or "")
        if not collection or not subject:
            raise HTTPException(status_code=400, detail="a node target needs 'collection' and 'subject'")
        out = {"kind": "node", "collection": collection, "subject": subject}
        for k in ("revision", "node"):
            if raw.get(k):
                out[k] = str(raw[k])
        return out
    raise HTTPException(status_code=400, detail=f"target kind must be 'file' or 'node' (got {kind!r})")


def _check_options(raw: Any) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="'options' must be an object")
    out = {}
    for k in ("security_margin", "min_contact_area", "line_contact_thickness"):
        if raw.get(k) is not None and raw.get(k) != "":
            try:
                out[k] = float(raw[k])
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"option {k!r} must be a number") from exc
            if out[k] < 0:
                raise HTTPException(status_code=400, detail=f"option {k!r} must not be negative")
    return out


def _require_run_key(key: str) -> str:
    key = key.strip()
    if not key.startswith(CLASH_SIDECAR_PREFIX) or not key.endswith(".json") or key.endswith("/index.json"):
        raise HTTPException(status_code=400, detail=f"{key!r} is not a clash run key")
    return key


async def _read_run(ctx: RestContext, scope_obj: Scope, key: str):
    try:
        raw = await ctx.storage.get_bytes(scope_obj, key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no clash run at {key!r}") from exc
    try:
        return parse_run(raw)
    except SidecarError as exc:
        raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc


async def _submit(ctx, request, user, scope_obj, *, feature, source_key, derived_key, options, capability=None):
    return await ctx.jobs.submit(
        JobRequest(
            source_key=source_key,
            target_format=KIND,
            scope=scope_obj,
            feature=feature,
            derived_key=derived_key,
            conversion_options=options,
            target_capability=capability,
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            f"clash_{options['mode']}",
            key=derived_key,
            target_format=KIND,
            status="queued",
            job_id=submitted.job_id,
        ),
    )


@router.post("/scopes/{scope}/clash-contacts")
async def api_clash_contacts(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Run the convex-contact check on a worker. Body ``{target, glb_key, up_axis?, options?}`` --
    ``glb_key`` the GLB the viewer loads for ``target``. Returns ``{job_id, run_key}``."""
    target = _target_from(body.get("target"))
    glb_key = str(body.get("glb_key") or "").strip()
    up_axis = str(body.get("up_axis") or "z")
    if not glb_key:
        raise HTTPException(status_code=400, detail="'glb_key' (the GLB the viewer loads) is required")
    if up_axis not in _UP_AXES:
        raise HTTPException(status_code=400, detail="'up_axis' must be 'z' or 'y'")
    options = _check_options(body.get("options"))
    ctx.jobs.require("clash_check")
    if not await ctx.storage.exists(scope_obj, glb_key):
        raise HTTPException(status_code=404, detail=f"no GLB at {glb_key!r} -- convert the model first")

    run_id = uuid.uuid4().hex
    key = run_key(target, run_id)
    submitted = await _submit(
        ctx,
        request,
        user,
        scope_obj,
        feature="clash_check",
        source_key=glb_key,
        derived_key=key,
        options={
            "mode": "check",
            "target": target,
            "glb_key": glb_key,
            "up_axis": up_axis,
            "options": options,
            "run_id": run_id,
        },
    )
    return JSONResponse({"job_id": submitted.job_id, "run_key": key})


@router.post("/scopes/{scope}/clash-contacts/runs")
async def api_clash_contacts_store(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Store a run a browser measured. Body ``{run, annotate?}``: ``run`` is the sidecar document
    (``ada.clash/contacts@1``). Returns ``{run_key, summary, job_id}`` -- ``job_id`` of the annotate
    job resolving its specs, when asked for (``annotate``, default true) and a pool can run it."""
    try:
        run = parse_run(body.get("run"))
    except SidecarError as exc:
        raise HTTPException(status_code=400, detail=f"bad clash run: {exc}") from exc
    target = _target_from(dict(run.target))
    if dict(run.target) != target:
        raise HTTPException(status_code=400, detail="the run's target is not in its normal form")
    key = run_key(target, run.run_id)
    if await ctx.storage.exists(scope_obj, key):
        raise HTTPException(status_code=409, detail=f"a clash run {run.run_id!r} already exists for this target")
    await write_run_and_index(ctx.storage, scope_obj, key, run.to_json().encode("utf-8"))
    await ctx.audit(request, user, scope_obj, "clash_run_store", key=key, target_format=KIND, status="done")

    job_id = None
    if body.get("annotate", True) and ctx.jobs.supports("clash_check"):
        submitted = await _submit(
            ctx,
            request,
            user,
            scope_obj,
            feature="clash_check",
            source_key=key,
            derived_key=key,
            options={"mode": "annotate", "run_key": key},
        )
        job_id = submitted.job_id
    return JSONResponse({"run_key": key, "summary": run.summary(key), "job_id": job_id})


@router.get("/scopes/{scope}/clash-contacts/runs")
async def api_clash_contacts_list(
    target: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """A target's runs, newest first. ``target`` is the target object as JSON."""
    try:
        target_obj = _target_from(json.loads(target))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="'target' must be a JSON object") from exc
    try:
        raw = await ctx.storage.get_bytes(scope_obj, index_key(target_obj))
    except (FileNotFoundError, KeyError):
        raw = None
    try:
        runs = parse_index(raw)
    except (SidecarError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"unreadable clash run index: {exc}") from exc
    return JSONResponse({"target": target_obj, "runs": runs})


@router.get("/scopes/{scope}/clash-contacts/run")
async def api_clash_contacts_read(
    key: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One run, validated."""
    run = await _read_run(ctx, scope_obj, _require_run_key(key))
    return JSONResponse(run.to_dict())


@router.post("/scopes/{scope}/clash-contacts/annotate")
async def api_clash_contacts_annotate(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Resolve which specs could detail each clash of a run. Body ``{run_key}``. Returns ``{job_id}``."""
    key = _require_run_key(str(body.get("run_key") or ""))
    await _read_run(ctx, scope_obj, key)
    ctx.jobs.require("clash_check")
    submitted = await _submit(
        ctx,
        request,
        user,
        scope_obj,
        feature="clash_check",
        source_key=key,
        derived_key=key,
        options={"mode": "annotate", "run_key": key},
    )
    return JSONResponse({"job_id": submitted.job_id})


@router.post("/scopes/{scope}/clash-contacts/detail")
async def api_clash_contacts_detail(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Detail clashes of a run with a registered spec, from the stored contacts. Body
    ``{run_key, contact_ids, spec, options?}``. Returns ``{job_id, derived_key, glb_key}``.

    Routed to the spec's pool (a built-in runs anywhere). The ids are checked against the run before
    anything is enqueued, so a stale selection is a 404 naming them rather than a failed job."""
    key = _require_run_key(str(body.get("run_key") or ""))
    ids = sorted({str(i) for i in (body.get("contact_ids") or []) if str(i).strip()})
    spec_name = str(body.get("spec") or "")
    gen_options = body.get("options") if body.get("options") is not None else {}
    if not ids or not spec_name:
        raise HTTPException(status_code=400, detail="'contact_ids' (non-empty) and 'spec' are required")
    if not isinstance(gen_options, dict):
        raise HTTPException(status_code=400, detail="'options' must be an object")
    ctx.jobs.require("clash_detail")

    run = await _read_run(ctx, scope_obj, key)
    known = {c.id for c in run.contacts}
    absent = [i for i in ids if i not in known]
    if absent:
        raise HTTPException(status_code=404, detail=f"clash id(s) not in this run: {', '.join(absent)}")
    if run.target.get("kind") not in ("file", "node"):
        raise HTTPException(status_code=409, detail="this run's target cannot be rebuilt into members")

    capability = await _spec_capability(ctx, spec_name)
    ids_token = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:16]
    opts_token = hashlib.sha256(json.dumps(gen_options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    run_token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    prefix = f"_derived/clash/contacts-detail/{spec_name}/{run_token}/{ids_token}/{opts_token}"
    derived_key, glb_key = f"{prefix}/result.stats.json", f"{prefix}/model.glb"
    submitted = await _submit(
        ctx,
        request,
        user,
        scope_obj,
        feature="clash_detail",
        source_key=key,
        derived_key=derived_key,
        options={
            "mode": "detail",
            "run_key": key,
            "contact_ids": ids,
            "spec": spec_name,
            "options": gen_options,
            "glb_key": glb_key,
        },
        capability=capability,
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "glb_key": glb_key})
