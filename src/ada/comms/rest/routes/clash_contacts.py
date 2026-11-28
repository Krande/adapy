"""Convex-contact clash runs: ``/api/scopes/{scope}/clash-contacts`` and below.

A clash run measures the members of a viewer GLB (``ada.clash.contacts``) and is kept as a sidecar
(``ada.clash.sidecar``) beside the target it was run on. These routes:

* ``POST /clash-contacts`` -- run the check on a worker over the GLB at ``glb_key``.
* ``POST /clash-contacts/runs`` -- store a run a BROWSER measured (adacpp's wasm module on the same
  GLB bytes). Validated here, written by the same code a worker's run is, and optionally followed by
  an ``annotate`` job that resolves which specs could detail each clash -- the one thing a browser
  has no registry for.
* ``GET /clash-contacts/runs?target=...`` / ``GET /clash-contacts/run?key=...`` -- list a target's
  runs and read one back: a run outlives the page that made it. ``GET /clash-contacts/recent`` lists
  the scope's newest runs over every target.
* ``POST /clash-contacts/annotate`` -- the specs half alone, for an existing run.
* ``POST /clash-contacts/detail`` -- build named clashes of a run with a registered spec, from the
  stored contacts. ``run_key: "latest"`` (with an optional ``target``) details the newest run.
* ``GET /clash-contacts/latest?target=...`` -- the newest run's index line, of a target or the scope.
* ``GET /clash-contacts/joints?key=...&ids=...`` -- a run's clashes as an agent reads them: members
  by name and role, centre, measured contact, applicable specs (``ada.comms.rest.clash_inspect``).

A saved GROUP's run (``{"kind": "group", "group_id"}``, see ``ada.clash.sidecar``) spans several GLBs:
it is measured in the browser only, then stored, listed, read, annotated and detailed like any other
run, each member rebuilt in its own model's part.

LIGHT, like ``clash_check``'s router: the slim API imports it, so only stdlib modules and
``ada.clash.sidecar`` are imported here; the work runs in a ``clash_contacts`` job.
"""

from __future__ import annotations

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
from .. import clash_details, clash_inspect
from ..auth import User
from ..browser_jobs import job_fields
from ..clash_runs import write_run_and_index
from ..job_transport import JobRequest
from ..scope import Scope
from .clash_check import _route_with_reader, _spec_capability
from .deps import RestContext, optional_pool, rest_context, scope_from_path

router = APIRouter()

KIND = "clash_contacts"
_UP_AXES = ("z", "y")
#: A run key that means "the newest run" (of the ``target`` given beside it, else of the scope).
LATEST = "latest"


def _target_from(raw: Any) -> dict:
    """A run's target, normalised: ``{"kind": "file", "source_key"}``,
    ``{"kind": "node", "collection", "subject", "revision"?, "node"?}`` or ``{"kind": "group",
    "group_id"}`` (a saved group's id alone: its name and members are the run's ``group`` snapshot)."""
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
    if kind == "group":
        group_id = str(raw.get("group_id") or "").strip()
        if not group_id:
            raise HTTPException(status_code=400, detail="a group target needs 'group_id'")
        return {"kind": "group", "group_id": group_id}
    raise HTTPException(status_code=400, detail=f"target kind must be 'file', 'node' or 'group' (got {kind!r})")


def _check_options(raw: Any) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="'options' must be an object")
    out = {}
    for k in (
        "security_margin",
        "min_contact_area",
        "min_contact_width_ratio",
        "keep_penetration_depth",
        "line_contact_thickness",
    ):
        if raw.get(k) is not None and raw.get(k) != "":
            try:
                out[k] = float(raw[k])
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"option {k!r} must be a number") from exc
            if out[k] < 0:
                raise HTTPException(status_code=400, detail=f"option {k!r} must not be negative")
    return out


def _geometry_provider(body: dict, target: dict) -> str | None:
    """The ``geometry_provider`` an annotate reads the members through, or None (each member's own).
    A file has no provider, so it is dropped for a file run -- the same rule the classic check keeps."""
    raw = body.get("geometry_provider")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=400, detail="'geometry_provider' must be a provider id")
    return None if target.get("kind") == "file" else raw.strip()


async def _annotate_capability(ctx: RestContext, provider: str | None) -> str | None:
    """Where an annotate job runs: anywhere core does, unless it reads through a geometry provider --
    then only a pool with that provider's reader (409 naming it when none is live)."""
    return await _route_with_reader(ctx, None, provider, needs="annotating a clash run")


def _require_run_key(key: str) -> str:
    key = key.strip()
    if (
        not key.startswith(CLASH_SIDECAR_PREFIX)
        or key.startswith(clash_details.DETAILS_PREFIX)
        or not key.endswith(".json")
        or key.endswith("/index.json")
    ):
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


async def _all_index_lines(ctx: RestContext, scope_obj: Scope) -> list[dict]:
    """Every run index line in the scope, over every target, newest first."""
    runs: list[dict] = []
    for entry in await ctx.storage.list_prefix(scope_obj, CLASH_SIDECAR_PREFIX):
        if not entry.key.endswith("/index.json"):
            continue
        try:
            runs.extend(parse_index(await ctx.storage.get_bytes(scope_obj, entry.key)))
        except (FileNotFoundError, KeyError, SidecarError, json.JSONDecodeError):
            continue
    runs.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    return runs


def _target_query(target: str | None) -> dict | None:
    if target is None or not target.strip():
        return None
    try:
        return _target_from(json.loads(target))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="'target' must be a JSON object") from exc


async def _latest_line(ctx: RestContext, scope_obj: Scope, target: dict | None) -> dict:
    """The newest run's index line: of ``target`` when one is named, else of the whole scope. 404
    when there is none."""
    if target is not None:
        try:
            lines = parse_index(await ctx.storage.get_bytes(scope_obj, index_key(target)))
        except (FileNotFoundError, KeyError):
            lines = []
        except (SidecarError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=f"unreadable clash run index: {exc}") from exc
    else:
        lines = await _all_index_lines(ctx, scope_obj)
    line = clash_inspect.newest(lines)
    if line is None:
        what = "this target" if target is not None else "this scope"
        raise HTTPException(status_code=404, detail=f"no clash run for {what}")
    return line


async def _resolve_run_key(ctx: RestContext, scope_obj: Scope, key: str, target: dict | None) -> str:
    """A run key as given, or ``"latest"`` resolved (:func:`_latest_line`)."""
    if key.strip() == LATEST:
        return _require_run_key(str((await _latest_line(ctx, scope_obj, target)).get("key") or ""))
    return _require_run_key(key)


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
    """Run the convex-contact check on a worker. Body ``{target, glb_key, up_axis?, options?,
    annotate?}`` -- ``glb_key`` the GLB the viewer loads for ``target``; ``annotate: false`` stores the
    run without resolving its specs (the caller annotates it next, e.g. through a geometry provider).
    Returns ``{job_id, run_key}``."""
    target = _target_from(body.get("target"))
    if target["kind"] == "group":
        raise HTTPException(
            status_code=400,
            detail="a saved group spans several GLBs; its clash check runs in the browser and is posted to /runs",
        )
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
            "annotate": bool(body.get("annotate", True)),
        },
    )
    return JSONResponse({**job_fields(submitted), "run_key": key})


@router.post("/scopes/{scope}/clash-contacts/runs")
async def api_clash_contacts_store(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Store a run a browser measured. Body ``{run, annotate?, geometry_provider?}``: ``run`` is the
    sidecar document (``ada.clash/contacts@1``). Returns ``{run_key, summary, job_id}`` -- ``job_id``
    of the annotate job resolving its specs, when asked for (``annotate``, default true) and a pool
    can run it; through ``geometry_provider`` when one is named (see ``/annotate``)."""
    try:
        run = parse_run(body.get("run"))
    except SidecarError as exc:
        raise HTTPException(status_code=400, detail=f"bad clash run: {exc}") from exc
    target = _target_from(dict(run.target))
    if dict(run.target) != target:
        raise HTTPException(status_code=400, detail="the run's target is not in its normal form")
    provider = _geometry_provider(body, target)
    annotate = bool(body.get("annotate", True)) and ctx.jobs.supports("clash_check")
    # Refused before anything is written: a run stored with an annotate that cannot be routed would
    # be a run whose specs silently never come.
    capability = await _annotate_capability(ctx, provider) if annotate else None
    key = run_key(target, run.run_id)
    if await ctx.storage.exists(scope_obj, key):
        raise HTTPException(status_code=409, detail=f"a clash run {run.run_id!r} already exists for this target")
    await write_run_and_index(ctx.storage, scope_obj, key, run.to_json().encode("utf-8"), pool=optional_pool(request))
    await ctx.audit(request, user, scope_obj, "clash_run_store", key=key, target_format=KIND, status="done")

    job: dict = {"job_id": None}
    if annotate:
        submitted = await _submit(
            ctx,
            request,
            user,
            scope_obj,
            feature="clash_check",
            source_key=key,
            derived_key=key,
            options={"mode": "annotate", "run_key": key, **({"geometry_provider": provider} if provider else {})},
            capability=capability,
        )
        job = job_fields(submitted)
    return JSONResponse({"run_key": key, "summary": run.summary(key), **job})


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


@router.get("/scopes/{scope}/clash-contacts/recent")
async def api_clash_contacts_recent(
    limit: int = 20,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The scope's runs over every target, newest first, each with its ``target`` and ``label`` -- how
    a run is reopened when the panel is on another target. A line indexed before it carried those is
    completed from the run itself; one whose run cannot be read is left out."""
    limit = max(1, min(int(limit), 100))
    runs = await _all_index_lines(ctx, scope_obj)
    out: list[dict] = []
    for line in runs[:limit]:
        if "target" not in line or "label" not in line:
            try:
                run = parse_run(await ctx.storage.get_bytes(scope_obj, str(line.get("key"))))
            except (FileNotFoundError, KeyError, SidecarError, json.JSONDecodeError):
                continue
            line = {**line, "target": dict(run.target), "label": run.label()}
        out.append(line)
    return JSONResponse({"runs": out})


@router.get("/scopes/{scope}/clash-contacts/run")
async def api_clash_contacts_read(
    key: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One run, validated."""
    run = await _read_run(ctx, scope_obj, _require_run_key(key))
    return JSONResponse(run.to_dict())


@router.get("/scopes/{scope}/clash-contacts/latest")
async def api_clash_contacts_latest(
    target: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The newest run: of ``target`` (the target object as JSON) when given, else of the whole
    scope. ``{"run": <index line: run_id, key, target, label, created_at, contacts, ...>}``; 404 when
    there is none."""
    return JSONResponse({"run": await _latest_line(ctx, scope_obj, _target_query(target))})


#: How many clashes ``/clash-contacts/joints`` answers with at most, when no ids are named.
JOINTS_LIMIT = 500


@router.get("/scopes/{scope}/clash-contacts/joints")
async def api_clash_contacts_joints(
    key: str = LATEST,
    target: str | None = None,
    ids: str | None = None,
    member: str | None = None,
    limit: int = 100,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """A run's clashes as an agent reads them (``clash_inspect.contact_view``). ``key`` is a run key or
    ``latest`` (of ``target``, else of the scope). ``ids`` (comma-separated) picks clashes -- an id
    not in the run is listed in ``missing`` rather than refused; ``member`` keeps the clashes with a
    member of that name. ``{"run": {key, run_id, label, target, created_at, contacts}, "joints",
    "missing", "truncated"}``."""
    run_key_ = await _resolve_run_key(ctx, scope_obj, key, _target_query(target))
    run = await _read_run(ctx, scope_obj, run_key_)
    limit = max(1, min(int(limit), JOINTS_LIMIT))
    missing: list[str] = []
    if ids:
        wanted = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))
        contacts = [c for c in (run.contact(i) for i in wanted) if c is not None]
        missing = [i for i in wanted if run.contact(i) is None]
    else:
        contacts = list(run.contacts)
    if member:
        contacts = [c for c in contacts if member in run.names(c)]
    truncated = not ids and len(contacts) > limit
    joints = [clash_inspect.contact_view(run, c) for c in (contacts if ids else contacts[:limit])]
    return JSONResponse(
        {
            "run": {
                "key": run_key_,
                "run_id": run.run_id,
                "label": run.label(),
                "target": dict(run.target),
                "created_at": run.created_at,
                "contacts": len(run.contacts),
            },
            "joints": joints,
            "missing": missing,
            "truncated": truncated,
        }
    )


@router.post("/scopes/{scope}/clash-contacts/annotate")
async def api_clash_contacts_annotate(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Resolve which specs could detail each clash of a run. Body ``{run_key, geometry_provider?}``.
    Returns ``{job_id, geometry_provider}``.

    ``geometry_provider`` reads the run's members from that provider's publish of the same elements
    (matched by name -- ``ada.clash.geometry_source``, the classic check's "Geometry from") rather
    than each member's own provider: how a run over a mesh-only provider's members still gets
    member-bound specs and roles. Routed to a pool that reads it (409 when none does); ignored for a
    file run, which has no provider. ``geometry_provider`` in the answer is the one in force."""
    key = _require_run_key(str(body.get("run_key") or ""))
    run = await _read_run(ctx, scope_obj, key)
    provider = _geometry_provider(body, dict(run.target))
    ctx.jobs.require("clash_check")
    capability = await _annotate_capability(ctx, provider)
    submitted = await _submit(
        ctx,
        request,
        user,
        scope_obj,
        feature="clash_check",
        source_key=key,
        derived_key=key,
        options={"mode": "annotate", "run_key": key, **({"geometry_provider": provider} if provider else {})},
        capability=capability,
    )
    return JSONResponse({**job_fields(submitted), "geometry_provider": provider})


@router.post("/scopes/{scope}/clash-contacts/detail")
async def api_clash_contacts_detail(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Detail clashes of a run with a registered spec, from the stored contacts. Body
    ``{run_key, contact_ids, spec, options?, geometry_provider?, make_subjects?}`` (``make_subjects``: also
    write the simulation subject of every connection built, ``_clash/subjects/<ck>.weld-assembly.json``). Returns ``{job_id, derived_key,
    glb_key, detail_id}``: the run is a detail model of its own, listed by ``GET /clash-detail/models``.

    The members are rebuilt as the run's last annotate bound them: through the ``geometry_provider``
    it recorded, unless the body names another. Routed to the spec's pool (a built-in runs anywhere)
    that also reads that provider. The ids are checked against the run before anything is enqueued,
    so a stale selection is a 404 naming them rather than a failed job.

    ``run_key: "latest"`` details the newest run of ``target`` (a target object, as for a check)
    when the body names one, else the scope's newest run; the answer's ``run_key`` says which."""
    raw_target = body.get("target")
    key = await _resolve_run_key(
        ctx, scope_obj, str(body.get("run_key") or ""), _target_from(raw_target) if raw_target is not None else None
    )
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
    if run.target.get("kind") not in ("file", "node", "group"):
        raise HTTPException(status_code=409, detail="this run's target cannot be rebuilt into members")
    if body.get("geometry_provider") is not None:
        provider = _geometry_provider(body, dict(run.target))
    else:
        recorded = run.provenance.get("geometry_provider")
        provider = str(recorded) if recorded and run.target.get("kind") != "file" else None

    capability = await _route_with_reader(
        ctx, await _spec_capability(ctx, spec_name), provider, needs=f"spec {spec_name!r}"
    )
    # A model of its own per detail run, kept and listed (`clash_details`) -- see `/clash-detail`.
    paths = clash_details.detail_paths(clash_details.new_detail_id())
    derived_key, glb_key = paths.stats, paths.glb
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
            "detail_id": paths.detail_id,
            # The simulation subject of every connection built, made of the built objects (ada.clash.subjects).
            **({"make_subjects": True} if body.get("make_subjects") else {}),
            **({"geometry_provider": provider} if provider else {}),
        },
        capability=capability,
    )
    await clash_details.record_new(
        ctx.storage,
        scope_obj,
        clash_details.make_record(
            paths,
            spec=spec_name,
            capability=capability,
            source={
                "kind": "run",
                "run_key": key,
                "target": dict(run.target),
                "label": run.label(),
                **({"geometry_provider": provider} if provider else {}),
            },
            joint_ids=ids,
            options=gen_options,
            job_id=submitted.job_id,
        ),
        pool=optional_pool(request),
    )
    return JSONResponse(
        {
            **job_fields(submitted),
            "derived_key": derived_key,
            "glb_key": glb_key,
            "detail_id": paths.detail_id,
            "run_key": key,
        }
    )
