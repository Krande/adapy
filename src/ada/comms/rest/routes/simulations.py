"""Simulation providers and runs: ``/api/simulation/providers`` and
``/api/scopes/{scope}/simulations...``.

A simulation provider is something a worker registered that runs simulations behind one
interface (``ada.simulation``). These routes:

* ``GET /simulation/providers`` -- the kinds of simulation and the providers the worker registry
  knows, grouped by kind, each with whether a worker serving it is alive.
* ``POST /scopes/{scope}/simulations`` -- start a run: the parameters are checked against the
  provider's schema, a run record is written, and a ``simulation_run`` job is sent to the
  provider's pool.
* ``GET /scopes/{scope}/simulations`` / ``GET .../simulations/{run_id}`` -- the scope's runs,
  with status, progress and artefacts. A run outlives its job: it is read from its record.
* ``POST .../simulations/{run_id}/cancel`` -- ask a run to stop.

An artefact is listed with its storage ``key`` in the scope, and fetched through the blob route
like every other derived file.

LIGHT, like the clash routers: the slim API imports it, so only stdlib and ``ada.simulation``
are imported here; the provider runs in the job.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.simulation.interfaces import SIMULATION_DOMAINS
from ada.simulation.runs import (
    STATUS_CANCELLED,
    STATUS_QUEUED,
    TERMINAL_STATUSES,
    RunRecordError,
    cancel_key,
    new_run_id,
    new_run_record,
    run_record_key,
    utc_now,
)
from ada.simulation.schema import ParameterError, validate_parameters

from .. import auth as auth_module
from .. import pending_uploads
from ..auth import User
from ..job_transport import JobRequest
from ..scope import Scope
from ..simulation_runs import (
    domain_listing,
    list_providers,
    list_run_ids,
    provider_by_id,
    read_run,
    run_view,
    write_run,
)
from .deps import RestContext, pending_upload_detail, rest_context, scope_from_path

router = APIRouter()

KIND = "simulation_run"
_MAX_INPUTS = 64


async def _providers(ctx: RestContext) -> list[dict]:
    return await list_providers(ctx.jobs, stale_after_s=ctx.queue.WORKER_STALE_AFTER_S)


def _require_domain(domain: str | None) -> str | None:
    if domain is not None and domain not in SIMULATION_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown simulation domain {domain!r} (known: {', '.join(SIMULATION_DOMAINS)})",
        )
    return domain


@router.get("/simulation/providers")
async def api_simulation_providers(
    domain: str | None = None,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """``{"domains": [...], "providers": [...]}``. ``domains`` are the kinds of simulation
    (``fea``, ``robotics``, ``cfd``), each with its title, description and artefact roles;
    ``providers`` are ordered by domain and each carry ``id``, ``domain``, ``engine``, ``title``,
    ``version``, ``description``, ``kinds``, ``parameters_schema``, ``capability``, ``online`` and
    ``workers`` (how many live workers serve it). ``?domain=`` lists one kind's providers only.

    A provider stays listed, ``online: false``, after its last worker stops, until the worker
    registry prunes the row. Without a queue the providers are the ones registered in this
    process, which runs them itself."""
    _require_domain(domain)
    providers = await _providers(ctx)
    if domain is not None:
        providers = [p for p in providers if p["domain"] == domain]
    return JSONResponse({"domains": domain_listing(), "providers": providers})


def _inputs_from(raw) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(k, str) or not k.strip() for k in raw):
        raise HTTPException(status_code=400, detail="'inputs' must be a list of storage keys")
    keys = list(dict.fromkeys(k.strip().lstrip("/") for k in raw))
    if len(keys) > _MAX_INPUTS:
        raise HTTPException(status_code=400, detail=f"a run takes at most {_MAX_INPUTS} input files")
    return keys


@router.post("/scopes/{scope}/simulations")
async def api_simulation_start(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Start a run. Body ``{provider, parameters?, inputs?, kind?}``: the provider's id, its
    parameters, the storage keys of the run's input files in this scope, and which of the
    provider's kinds of run. Returns ``{"run": {...}}`` -- the run record, with its ``job_id``.

    Refused before anything is queued: an unknown provider (404), one no live worker serves
    (409), parameters that do not fit the provider's schema (400, naming each problem), an input
    that is missing (404) or still uploading (409)."""
    provider_id = body.get("provider")
    if not isinstance(provider_id, str) or not provider_id:
        raise HTTPException(status_code=400, detail="'provider' (a simulation provider id) is required")
    ctx.jobs.require("simulation")

    provider = provider_by_id(await _providers(ctx), provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"unknown simulation provider {provider_id!r}")
    if not provider["online"]:
        reason = provider.get("unavailable_reason")
        raise HTTPException(
            status_code=409,
            detail=(
                f"no live worker serves simulation provider {provider_id!r}, so a run would never start"
                + (f" ({reason})" if reason else "")
                + ". Start a worker that registers it."
            ),
        )

    kind = body.get("kind")
    kinds = [k.get("id") for k in provider["kinds"] if isinstance(k, dict)]
    if kind is not None and (not isinstance(kind, str) or kind not in kinds):
        raise HTTPException(
            status_code=400, detail=f"provider {provider_id!r} has no kind of run {kind!r} (it offers: {kinds})"
        )
    try:
        parameters = validate_parameters(provider["parameters_schema"], body.get("parameters"))
    except ParameterError as exc:
        raise HTTPException(status_code=400, detail=f"invalid parameters: {exc}") from exc

    inputs = _inputs_from(body.get("inputs"))
    for key in inputs:
        pending = pending_uploads.get(scope_obj, key)
        if pending is not None:
            raise HTTPException(status_code=409, detail=pending_upload_detail(key, pending))
        if not await ctx.storage.exists(scope_obj, key):
            raise HTTPException(status_code=404, detail=f"no input file at {key!r}")

    record = new_run_record(
        run_id=new_run_id(),
        provider=provider,
        parameters=parameters,
        inputs=inputs,
        started_by={"id": user.sub, "display": user.display_name},
        kind=kind,
    )
    key = run_record_key(record["run_id"])
    # Written before the job exists, so whatever picks the job up finds its record.
    await write_run(ctx.storage, scope_obj, record)

    async def before_dispatch(submitted) -> None:
        # Behind a queue: the job is durable and not yet visible to a worker, so the record can
        # still be rewritten here without racing the worker's own writes.
        record["job_id"] = submitted.job_id
        await write_run(ctx.storage, scope_obj, record)
        await ctx.audit(
            request,
            user,
            scope_obj,
            "simulation_run",
            key=key,
            target_format=KIND,
            status="queued",
            job_id=submitted.job_id,
        )

    try:
        submitted = await ctx.jobs.submit(
            JobRequest(
                source_key=key,
                target_format=KIND,
                scope=scope_obj,
                feature="simulation",
                derived_key=key,
                conversion_options={"provider_id": provider_id, "run_id": record["run_id"]},
                target_capability=provider["capability"],
            ),
            before_dispatch=before_dispatch,
        )
    except Exception:
        # Nothing will ever run it: do not leave a record that reads as queued.
        try:
            await ctx.storage.delete(scope_obj, key)
        except Exception:  # noqa: BLE001 - the submit error is the one to report
            pass
        raise
    # In-process the job has already started and stamps its own id on the record; the answer
    # carries it either way.
    return JSONResponse({"run": {**record, "job_id": submitted.job_id, "cancel_requested": False}})


@router.get("/scopes/{scope}/simulations")
async def api_simulation_list(
    limit: int = 50,
    provider: str | None = None,
    domain: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The scope's runs, newest first: ``{"runs": [...]}``, each as ``GET .../{run_id}`` returns
    it. ``limit`` (default 50, at most 200) bounds how many records are read; ``provider`` and
    ``domain`` keep only that provider's or that kind's runs among them."""
    limit = max(1, min(int(limit), 200))
    _require_domain(domain)
    runs: list[dict] = []
    for run_id in (await list_run_ids(ctx.storage, scope_obj))[:limit]:
        try:
            record = await read_run(ctx.storage, scope_obj, run_id)
        except RunRecordError:
            continue  # a record that cannot be read is left out, not a failed listing
        if record is None:
            continue
        if provider is not None and record["provider"].get("id") != provider:
            continue
        if domain is not None and record["provider"].get("domain") != domain:
            continue
        runs.append(await run_view(ctx.storage, scope_obj, ctx.jobs, record))
    return JSONResponse({"runs": runs})


async def _require_run(ctx: RestContext, scope_obj: Scope, run_id: str) -> dict:
    try:
        record = await read_run(ctx.storage, scope_obj, run_id)
    except RunRecordError as exc:
        raise HTTPException(status_code=502, detail=f"unreadable simulation run {run_id}: {exc}") from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"no simulation run {run_id!r} in this scope")
    return record


@router.get("/scopes/{scope}/simulations/{run_id}")
async def api_simulation_read(
    run_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One run: ``{"run": {...}}``. The record (``run_id``, ``provider``, ``kind``,
    ``parameters``, ``inputs``, ``started_by``, ``created_at`` / ``started_at`` /
    ``finished_at``, ``status``, ``stage``, ``progress``, ``error``, ``job_id``, ``artefacts``,
    ``summary``) and ``cancel_requested``. While the run is going, its progress and artefact
    list are the job's, so an artefact with ``partial: true`` can be fetched before the end;
    its ``revision`` goes up each time it is stored again."""
    record = await _require_run(ctx, scope_obj, run_id)
    return JSONResponse({"run": await run_view(ctx.storage, scope_obj, ctx.jobs, record)})


@router.post("/scopes/{scope}/simulations/{run_id}/cancel")
async def api_simulation_cancel(
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Ask a run to stop: ``{"run": {...}, "cancelled": bool}``. Anyone who can open the scope
    may cancel a run in it, as with the scope's in-process jobs.

    Cancelling is cooperative. A run that has not started is ended here. A running one is told
    through a marker in its directory (and, in-process, its cancel event); it reads as
    ``cancel_requested`` until the provider stops at its next check and the run ends as
    ``cancelled``. ``cancelled`` is false for a run that had already ended."""
    record = await _require_run(ctx, scope_obj, run_id)
    view = await run_view(ctx.storage, scope_obj, ctx.jobs, record)
    if view["status"] in TERMINAL_STATUSES:
        return JSONResponse({"run": view, "cancelled": False})

    # The marker first: it is what a worker in another process reads.
    await ctx.storage.put_bytes(scope_obj, cancel_key(run_id), user.sub.encode("utf-8"))
    job_id = record.get("job_id") or view.get("job_id")
    if job_id:
        await ctx.jobs.cancel(str(job_id))
    if record.get("status") == STATUS_QUEUED and view["status"] == STATUS_QUEUED and job_id:
        # Nothing has picked it up: end it here. A worker that pulls the job later finds a
        # finished record and does not run it; one that started in this same moment rewrites
        # the record and then stops at the marker.
        record = {**record, "status": STATUS_CANCELLED, "stage": STATUS_CANCELLED, "finished_at": utc_now()}
        await write_run(ctx.storage, scope_obj, record)
        view = {**record, "cancel_requested": False}
    else:
        view = {**view, "cancel_requested": True}
    await ctx.audit(request, user, scope_obj, "simulation_cancel", key=run_record_key(run_id), target_format=KIND)
    return JSONResponse({"run": view, "cancelled": True})
