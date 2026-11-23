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
* ``POST`` / ``DELETE .../simulations/batches/{batch_id}/runs`` -- move runs into a batch (made
  when it is new), or take them out of it, after they were started.
* ``DELETE .../simulations/{run_id}``, ``POST .../simulations/delete`` -- delete one run, or many;
  ``DELETE .../simulations/batches/{batch_id}`` deletes a batch, with ``?runs=true`` its runs too
  (``simulation_admin`` says what a run is and who may).

An artefact is listed with its storage ``key`` in the scope, and fetched through the blob route
like every other derived file.

LIGHT, like the clash routers: the slim API imports it, so only stdlib and ``ada.simulation``
are imported here; the provider runs in the job.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.simulation.batches import BatchError, batch_prefix, check_batch
from ada.simulation.interfaces import SIMULATION_DOMAINS
from ada.simulation.runs import (
    ACTIVE_STATUSES,
    STATUS_CANCELLED,
    STATUS_QUEUED,
    TERMINAL_STATUSES,
    RunRecordError,
    cancel_key,
    deleted_key,
    finite_json,
    new_run_id,
    new_run_record,
    run_record_key,
    utc_now,
)
from ada.simulation.schema import ParameterError, validate_parameters
from ada.simulation.subjects import SUBJECT_KINDS

from .. import auth as auth_module
from .. import pending_uploads
from ..assoc_links import drop_sim_run_links, sim_run_link
from ..auth import User
from ..job_transport import JobRequest
from ..scope import Scope
from ..simulation_admin import (
    RunOps,
    delete_batch,
    delete_runs,
    move_runs,
    purge_deleted,
    remove_runs,
    run_ids_from,
)
from ..simulation_relay import SimulationRelay, merge_providers, relay_of
from ..simulation_runs import (
    batch_index,
    batch_run_ids,
    batch_summaries,
    delete_batch_member,
    domain_listing,
    list_providers,
    list_run_ids,
    provider_by_id,
    read_run,
    read_runs,
    run_view,
    write_batch_member,
    write_run,
)
from .deps import (
    RestContext,
    optional_pool,
    pending_upload_detail,
    rest_context,
    scope_from_path,
)

router = APIRouter()

KIND = "simulation_run"
_MAX_INPUTS = 64
#: The most records one filtered listing reads; ``next`` says where to go on from.
_MAX_SCAN = 2000
_RUN_STATUSES = frozenset(ACTIVE_STATUSES | TERMINAL_STATUSES)


class FiniteJSONResponse(JSONResponse):
    """A JSONResponse that answers a non-finite float (``inf``, ``nan``) as null instead of failing.

    These routes echo what providers put in run records -- summaries, parameters, a remote studio's
    views -- and one ``inf`` there must not turn a whole scope's listing into a 500. The strict
    encoding is tried first, so a body without one costs nothing more; only one that has one is
    walked (``ada.simulation.runs.finite_json``)."""

    def render(self, content) -> bytes:
        try:
            return super().render(content)
        except ValueError:
            return super().render(finite_json(content)[0])


def _batch_from(raw, parameters: dict) -> dict | None:
    """The batch the run is started in: the body's ``batch``, else a ``batch`` among the parameters
    when the provider's schema has one (a client that put it there before core had the field)."""
    try:
        if raw is not None:
            return check_batch(raw)
    except BatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return check_batch(parameters.get("batch"))
    except BatchError:
        return None  # the provider's own parameter, not one core can read as a batch


async def _providers(ctx: RestContext, request: Request) -> list[dict]:
    """The providers a run can be started on: this deployment's, and a remote Ada Studio's when one is
    configured (``simulation_relay``; marked ``remote: true``; a local provider wins an id clash)."""
    local = await list_providers(ctx.jobs, stale_after_s=ctx.queue.WORKER_STALE_AFTER_S)
    relay = relay_of(request)
    if relay is None:
        return local
    remote, _ = await relay.providers()
    return merge_providers(local, remote)


def _require_domain(domain: str | None) -> str | None:
    if domain is not None and domain not in SIMULATION_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown simulation domain {domain!r} (known: {', '.join(SIMULATION_DOMAINS)})",
        )
    return domain


@router.get("/simulation/providers")
async def api_simulation_providers(
    request: Request,
    domain: str | None = None,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """``{"domains": [...], "providers": [...]}``. ``domains`` are the kinds of simulation
    (``fea``, ``robotics``, ``cfd``), each with its title, description and artefact roles;
    ``providers`` are ordered by domain and each carry ``id``, ``domain``, ``engine``, ``title``,
    ``version``, ``description``, ``kinds``, ``parameters_schema``, ``accepts`` (the subject kinds a
    run can be started on), ``capability``, ``online`` and
    ``workers`` (how many live workers serve it). ``?domain=`` lists one kind's providers only.

    A provider stays listed, ``online: false``, after its last worker stops, until the worker
    registry prunes the row. Without a queue the providers are the ones registered in this
    process, which runs them itself.

    With a remote Ada Studio configured (``ADA_SIMULATION_REMOTE_URL``) its providers are listed too,
    each marked ``remote: true``, and the answer carries ``remote: {url, error}`` (``error``: why the
    remote could not be asked, else null). A run on one is relayed to it."""
    _require_domain(domain)
    providers = await _providers(ctx, request)
    if domain is not None:
        providers = [p for p in providers if p["domain"] == domain]
    body: dict = {"domains": domain_listing(), "providers": providers}
    relay = relay_of(request)
    if relay is not None:
        _, error = await relay.providers()
        body["remote"] = {"url": relay.url, "error": error}
    return FiniteJSONResponse(body)


def _inputs_from(raw) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(k, str) or not k.strip() for k in raw):
        raise HTTPException(status_code=400, detail="'inputs' must be a list of storage keys")
    keys = list(dict.fromkeys(k.strip().lstrip("/") for k in raw))
    if len(keys) > _MAX_INPUTS:
        raise HTTPException(status_code=400, detail=f"a run takes at most {_MAX_INPUTS} input files")
    return keys


def _subject_from(raw, provider: dict) -> dict | None:
    """``{"kind", "key"}`` of the subject a run is started on, checked: a kind core knows
    (``ada.simulation.subjects``), which the provider accepts, and a storage key."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("kind"), str) or not isinstance(raw.get("key"), str):
        raise HTTPException(status_code=400, detail='\'subject\' must be {"kind": ..., "key": ...}')
    kind, key = raw["kind"], raw["key"].strip().lstrip("/")
    if kind not in SUBJECT_KINDS:
        raise HTTPException(
            status_code=400, detail=f"unknown subject kind {kind!r} (known: {', '.join(SUBJECT_KINDS)})"
        )
    if kind not in provider.get("accepts", []):
        accepted = provider.get("accepts") or []
        raise HTTPException(
            status_code=400,
            detail=f"provider {provider['id']!r} does not take a {kind} (it accepts: {accepted or 'no subject'})",
        )
    if not key:
        raise HTTPException(status_code=400, detail="'subject.key' (a storage key in this scope) is required")
    return {"kind": kind, "key": key}


@router.post("/scopes/{scope}/simulations")
async def api_simulation_start(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Start a run. Body ``{provider, parameters?, inputs?, kind?, subject?, batch?}``: the provider's
    id, its parameters, the storage keys of the run's input files in this scope, which of the
    provider's kinds of run, what the run is ON (``subject: {kind, key}``, see
    ``ada.simulation.subjects``; its key is put first among the inputs and the record keeps it as
    ``subject``), and the batch it is one of (``batch: {id, label?, source?: {kind, key}}``, see
    ``ada.simulation.batches``; the record keeps it as ``batch``, and the batch's runs are listed
    together). Returns ``{"run": {...}}`` -- the run record, with its ``job_id``.

    Refused before anything is queued: an unknown provider (404), one no live worker serves
    (409), parameters that do not fit the provider's schema (400, naming each problem), a
    subject of an unknown kind or one the provider does not accept (400), an input or subject
    that is missing (404) or still uploading (409)."""
    return FiniteJSONResponse({"run": await start_run(ctx, request, user, scope_obj, body)})


async def start_run(ctx: RestContext, request: Request, user: User, scope_obj: Scope, body: dict) -> dict:
    """Start one run as ``user`` (``POST .../simulations``'s whole work) and return its record, with
    its ``job_id``. HTTPException for anything the route refuses. A run on a connection's subject is
    filed under the connection's elements as queued (``assoc_links.sim_run_link``)."""
    provider_id = body.get("provider")
    if not isinstance(provider_id, str) or not provider_id:
        raise HTTPException(status_code=400, detail="'provider' (a simulation provider id) is required")
    ctx.jobs.require("simulation")

    provider = provider_by_id(await _providers(ctx, request), provider_id)
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

    subject = _subject_from(body.get("subject"), provider)
    batch = _batch_from(body.get("batch"), parameters)
    inputs = _inputs_from(body.get("inputs"))
    if subject is not None:
        # The subject is one of the run's input files: the provider reads it through the context.
        inputs = [subject["key"], *(k for k in inputs if k != subject["key"])]
    for key in inputs:
        pending = pending_uploads.get(scope_obj, key)
        if pending is not None:
            raise HTTPException(status_code=409, detail=pending_upload_detail(key, pending))
        if not await ctx.storage.exists(scope_obj, key):
            raise HTTPException(status_code=404, detail=f"no input file at {key!r}")

    if provider.get("remote"):
        # Handed off: the inputs go to the remote studio, the run starts there, and the record kept
        # here points at it (simulation_relay). The run id is the remote's, so artefact keys agree.
        relay = relay_of(request)
        assert relay is not None  # a remote provider is only listed by a relay
        record = await relay.start(
            ctx.storage,
            scope_obj,
            provider_id=provider_id,
            parameters=parameters,
            kind=kind,
            subject=subject,
            inputs=inputs,
            batch=batch,
        )
        if batch is not None:
            await write_batch_member(ctx.storage, scope_obj, batch["id"], record["run_id"])
        await ctx.audit(
            request,
            user,
            scope_obj,
            "simulation_run",
            key=run_record_key(record["run_id"]),
            target_format=KIND,
            status="queued",
        )
        await sim_run_link(ctx.storage, scope_obj, record, pool=optional_pool(request))
        return record

    record = new_run_record(
        run_id=new_run_id(),
        provider=provider,
        parameters=parameters,
        inputs=inputs,
        started_by={"id": user.sub, "display": user.display_name},
        kind=kind,
        subject=subject,
        batch=batch,
    )
    key = run_record_key(record["run_id"])
    # Written before the job exists, so whatever picks the job up finds its record.
    await write_run(ctx.storage, scope_obj, record)
    if batch is not None:
        await write_batch_member(ctx.storage, scope_obj, batch["id"], record["run_id"])

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
        if batch is not None:
            await delete_batch_member(ctx.storage, scope_obj, batch["id"], record["run_id"])
        raise
    # In-process the job has already started and stamps its own id on the record; the answer
    # carries it either way.
    started = {**record, "job_id": submitted.job_id, "cancel_requested": False}
    await sim_run_link(ctx.storage, scope_obj, started, pool=optional_pool(request))
    return started


def _statuses_from(raw: str | None) -> frozenset[str] | None:
    if raw is None or not raw.strip():
        return None
    wanted = frozenset(s.strip() for s in raw.split(",") if s.strip())
    unknown = sorted(wanted - _RUN_STATUSES)
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown run status {unknown[0]!r} (known: {', '.join(sorted(_RUN_STATUSES))})"
        )
    return wanted


def _batch_id_from(raw: str) -> str:
    try:
        batch_prefix(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"'batch' must be a batch id or 'none' ({exc})") from exc
    return raw


@router.get("/scopes/{scope}/simulations")
async def api_simulation_list(
    request: Request,
    limit: int = 50,
    provider: str | None = None,
    domain: str | None = None,
    batch: str | None = None,
    status: str | None = None,
    before: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The scope's runs, newest first: ``{"runs": [...], "next": run_id | null}``, each run as
    ``GET .../{run_id}`` returns it.

    * ``limit`` (default 50, at most 200): how many runs are returned;
    * ``provider`` / ``domain``: only that provider's, or that kind's, runs;
    * ``batch``: only the runs of that batch (``ada.simulation.batches``), or with ``batch=none``
      only the runs that belong to no batch;
    * ``status``: only runs in these statuses (comma-separated, e.g. ``queued,running,done,error``);
    * ``before``: only runs older than this run id -- the ``next`` of the page before, to page on.

    Filtering reads records until ``limit`` runs match, at most ``_MAX_SCAN`` per request; ``next``
    is where the next page starts, null when there is nothing after this one."""
    limit = max(1, min(int(limit), 200))
    _require_domain(domain)
    statuses = _statuses_from(status)
    await purge_deleted(ctx.storage, scope_obj, ctx.jobs)
    if batch is None:
        ids = await list_run_ids(ctx.storage, scope_obj)
    elif batch == "none":
        index = await batch_index(ctx.storage, scope_obj)
        batched = {i for members in index.values() for i in members}
        ids = [i for i in await list_run_ids(ctx.storage, scope_obj) if i not in batched]
    else:
        ids = await batch_run_ids(ctx.storage, scope_obj, _batch_id_from(batch))
    if before:
        ids = [i for i in ids if i < before]

    runs: list[dict] = []
    scanned = 0
    while ids and len(runs) < limit and scanned < _MAX_SCAN:
        chunk, ids = ids[: limit - len(runs)], ids[limit - len(runs) :]
        scanned += len(chunk)
        last = chunk[-1]
        for record in await read_runs(ctx.storage, scope_obj, chunk):
            if provider is not None and record["provider"].get("id") != provider:
                continue
            if domain is not None and record["provider"].get("domain") != domain:
                continue
            view = await _view(ctx, request, scope_obj, record, strict=False)
            if statuses is not None and view.get("status") not in statuses:
                continue
            runs.append(view)
    if not ids:
        next_id = None
    elif len(runs) >= limit:
        next_id = runs[-1]["run_id"]
    else:
        next_id = last  # the scan stopped before the end: go on from the last record read
    return FiniteJSONResponse({"runs": runs, "next": next_id})


@router.get("/scopes/{scope}/simulations/batches")
async def api_simulation_batches(
    provider: str | None = None,
    domain: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The scope's batches of runs, the newest first: ``{"batches": [...], "unbatched": n}``, each
    ``{id, label, source, provider, providers, count, counts: {done, running, queued, error,
    cancelled}, active, progress, newest_at, oldest_at}`` (``ada.simulation.batches``), and how
    many of the scope's runs belong to no batch. ``provider`` / ``domain`` keep the batches that
    have a run on that provider, or of that kind.

    The counts are the stored records' statuses: a run's job is not asked, so a run whose job was
    lost is counted as its record left it until that run is looked at (``GET .../{run_id}``)."""
    _require_domain(domain)
    await purge_deleted(ctx.storage, scope_obj, ctx.jobs)
    index = await batch_index(ctx.storage, scope_obj)
    batches = await batch_summaries(ctx.storage, scope_obj, index)
    if provider is not None:
        batches = [b for b in batches if any(p["id"] == provider for p in b["providers"])]
    if domain is not None:
        batches = [b for b in batches if any(p["domain"] == domain for p in b["providers"])]
    batched = {i for members in index.values() for i in members}
    unbatched = sum(1 for i in await list_run_ids(ctx.storage, scope_obj) if i not in batched)
    return FiniteJSONResponse({"batches": batches, "unbatched": unbatched})


@router.get("/scopes/{scope}/simulations/batches/{batch_id}")
async def api_simulation_batch(
    batch_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One batch's line: ``{"batch": {...}}`` as ``GET .../batches`` lists it; 404 for a batch the
    scope has no runs of. Its runs are ``GET .../simulations?batch=<id>``."""
    run_ids = await batch_run_ids(ctx.storage, scope_obj, _batch_id_from(batch_id))
    summaries = await batch_summaries(ctx.storage, scope_obj, {batch_id: run_ids} if run_ids else {})
    if not summaries:
        raise HTTPException(status_code=404, detail=f"no batch {batch_id!r} in this scope")
    return FiniteJSONResponse({"batch": summaries[0]})


async def _view(ctx: RestContext, request: Request, scope_obj: Scope, record: dict, *, strict: bool) -> dict:
    """The run as a client sees it: its job's view here, the remote's for a relayed run."""
    if record.get("remote"):
        relay: SimulationRelay | None = relay_of(request)
        if relay is None:
            raise HTTPException(
                status_code=502,
                detail=f"run {record['run_id']} was handed to {record['remote'].get('url')}, "
                "but no remote Ada Studio is configured (ADA_SIMULATION_REMOTE_URL)",
            )
        view = await relay.view(ctx.storage, scope_obj, record, strict=strict)
    else:
        view = await run_view(ctx.storage, scope_obj, ctx.jobs, record)
    # A run on a connection's subject: its links follow its status (filed again only when it moves).
    await sim_run_link(ctx.storage, scope_obj, view, pool=optional_pool(request))
    return view


async def _require_run(ctx: RestContext, scope_obj: Scope, run_id: str) -> dict:
    try:
        record = await read_run(ctx.storage, scope_obj, run_id)
    except RunRecordError as exc:
        raise HTTPException(status_code=502, detail=f"unreadable simulation run {run_id}: {exc}") from exc
    if record is None or await ctx.storage.exists(scope_obj, deleted_key(run_id)):
        # A run deleted while it was going is gone, whatever its worker wrote since.
        raise HTTPException(status_code=404, detail=f"no simulation run {run_id!r} in this scope")
    return record


@router.get("/scopes/{scope}/simulations/{run_id}")
async def api_simulation_read(
    run_id: str,
    request: Request,
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
    return FiniteJSONResponse({"run": await _view(ctx, request, scope_obj, record, strict=True)})


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
    return FiniteJSONResponse(await cancel_run(ctx, request, user, scope_obj, record))


async def cancel_run(ctx: RestContext, request: Request, user: User, scope_obj: Scope, record: dict) -> dict:
    """``POST .../cancel``'s whole work on a run's record, audit row included: ``{"run", "cancelled"}``."""
    run_id = record["run_id"]
    if record.get("remote"):
        relay = relay_of(request)
        if relay is None:
            await _view(ctx, request, scope_obj, record, strict=True)  # raises the 502 that says why
        assert relay is not None
        await ctx.audit(request, user, scope_obj, "simulation_cancel", key=run_record_key(run_id), target_format=KIND)
        return await relay.cancel(ctx.storage, scope_obj, record)
    view = await run_view(ctx.storage, scope_obj, ctx.jobs, record)
    if view["status"] in TERMINAL_STATUSES:
        return {"run": view, "cancelled": False}

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
    return {"run": view, "cancelled": True}


# ── managing runs: moving them between batches, deleting them ────────────────────────────────────


def _ops(ctx: RestContext, request: Request, user: User, scope_obj: Scope) -> RunOps:
    async def view(record: dict) -> dict:
        return await _view(ctx, request, scope_obj, record, strict=False)

    async def cancel(record: dict) -> dict:
        return await cancel_run(ctx, request, user, scope_obj, record)

    async def unlink(record: dict) -> None:
        await drop_sim_run_links(ctx.storage, scope_obj, record, pool=optional_pool(request))

    return RunOps(
        storage=ctx.storage, scope=scope_obj, user=user, jobs=ctx.jobs, view=view, cancel=cancel, unlink=unlink
    )


def _run_ids_of(body) -> list[str]:
    try:
        return run_ids_from((body or {}).get("run_ids") if isinstance(body, dict) else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _audit_results(
    ctx: RestContext, request: Request, user: User, scope_obj: Scope, action: str, results: list[dict], done: str
) -> None:
    """One audit row per run that was changed, as the other run routes write one per run."""
    for r in results:
        if r.get("dry") or r.get("already") or r["status"] not in (done, "failed"):
            continue
        await ctx.audit(
            request,
            user,
            scope_obj,
            action,
            key=run_record_key(r["run_id"]),
            target_format=KIND,
            status="ok" if r["status"] == done else "error",
            error=r.get("reason") if r["status"] == "failed" else None,
        )


@router.post("/scopes/{scope}/simulations/batches/{batch_id}/runs")
async def api_simulation_batch_add(
    batch_id: str,
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Move runs into a batch. Body ``{run_ids: [...], label?: str, replace?: bool}``: the batch is
    made, called ``label``, when the scope has no runs of it yet (an existing one keeps its label).
    Answers ``{"batch": {id, label, source}, "results": [...], "counts": {...}}``, a result per run:
    ``moved`` (with ``from`` when it left another batch), ``unchanged``, or ``refused`` with a
    ``code`` and a ``reason`` -- ``forbidden`` (in the shared scope only whoever started a run, or an
    admin, may move it), ``missing``, ``running`` (its worker would write its old batch back; a queued
    run is moved), ``other_batch`` (it is in another batch and ``replace`` was not given)."""
    batch_id = _batch_id_from(batch_id)
    run_ids = _run_ids_of(body)
    label = body.get("label")
    if label is not None and (not isinstance(label, str) or not label.strip()):
        raise HTTPException(status_code=400, detail="'label' must be a non-empty string")
    try:
        check_batch({"id": batch_id, "label": label})
    except BatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = await move_runs(
        _ops(ctx, request, user, scope_obj), batch_id, run_ids, label=label, replace=bool(body.get("replace"))
    )
    await _audit_results(ctx, request, user, scope_obj, "simulation_batch_move", out["results"], "moved")
    return FiniteJSONResponse(out)


@router.delete("/scopes/{scope}/simulations/batches/{batch_id}/runs")
async def api_simulation_batch_remove(
    batch_id: str,
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Take runs out of a batch, back to the runs of no batch. Body ``{run_ids: [...]}``. A result
    per run: ``removed``, ``unchanged`` (it was not in it) or ``refused`` (``forbidden``, ``missing``,
    ``running``), as ``POST`` says."""
    batch_id = _batch_id_from(batch_id)
    out = await remove_runs(_ops(ctx, request, user, scope_obj), batch_id, _run_ids_of(body))
    await _audit_results(ctx, request, user, scope_obj, "simulation_batch_remove", out["results"], "removed")
    return FiniteJSONResponse(out)


@router.delete("/scopes/{scope}/simulations/batches/{batch_id}")
async def api_simulation_batch_delete(
    batch_id: str,
    request: Request,
    runs: bool = False,
    dry: bool = False,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Delete a batch. Its runs go back to the runs of no batch, or with ``?runs=true`` are deleted
    too, each as ``DELETE .../simulations/{run_id}`` deletes one. ``?dry=true`` changes nothing and
    says what would go (each run's ``bytes`` with ``runs``). Answers ``{"batch": {id, deleted, runs},
    "results": [...], "counts": {...}, "bytes": n}``; ``deleted`` is false while a run of it was
    refused (still running, or one the caller may not move). A batch the scope has no runs of is
    already deleted: ``deleted`` true, no results."""
    batch_id = _batch_id_from(batch_id)
    ops = _ops(ctx, request, user, scope_obj)
    out = await delete_batch(ops, batch_id, runs=runs, dry=dry)
    if not dry:
        if runs:
            await _audit_results(ctx, request, user, scope_obj, "simulation_delete", out["results"], "deleted")
        else:
            await _audit_results(ctx, request, user, scope_obj, "simulation_batch_remove", out["results"], "removed")
        await ctx.audit(
            request,
            user,
            scope_obj,
            "simulation_batch_delete",
            key=batch_prefix(batch_id),
            target_format=KIND,
            status="ok" if out["batch"]["deleted"] else "error",
            error=None if out["batch"]["deleted"] else "some of its runs were refused",
        )
    return FiniteJSONResponse(out)


@router.post("/scopes/{scope}/simulations/delete")
async def api_simulation_delete_many(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Delete runs. Body ``{run_ids: [...], dry?: bool}``. Each run as ``DELETE .../{run_id}``
    deletes one; answers ``{"results": [...], "counts": {...}, "bytes": n}``, a result per run:
    ``deleted`` (``bytes`` freed; ``already`` when there was nothing left; ``pending`` when its
    worker was still stopping) or ``failed`` with a ``code`` (``forbidden``, ``cancel``,
    ``storage``) and a ``reason``. One run that fails does not stop the others, and asking again is
    harmless. ``dry`` deletes nothing and says what would go."""
    run_ids = _run_ids_of(body)
    dry = bool(body.get("dry"))
    out = await delete_runs(_ops(ctx, request, user, scope_obj), run_ids, dry=dry)
    if not dry:
        await _audit_results(ctx, request, user, scope_obj, "simulation_delete", out["results"], "deleted")
        await purge_deleted(ctx.storage, scope_obj, ctx.jobs)
    return FiniteJSONResponse(out)


@router.delete("/scopes/{scope}/simulations/{run_id}")
async def api_simulation_delete(
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Delete a run: ``{"run_id", "status": "deleted", "bytes", ...}``.

    A run that is queued or running is cancelled first (``POST .../cancel``). Then its record goes,
    with everything it stored under ``_simulations/<run_id>/`` (artefacts, log, cancel marker),
    what was derived from those (``_derived/_simulations/<run_id>/``), its batch index entries and
    its element links. Its inputs and subject are the scope's files and stay. A run a worker is
    still running keeps its cancel marker until the worker has stopped (``pending``); it is not
    listed meanwhile, and what the worker writes until then is removed after. Deleting a run that
    is gone answers ``already: true``.

    403 when the caller may not (in the shared scope, only whoever started it or an admin); 502 when
    it could not be cancelled or its files could not all be deleted, which asking again retries."""
    try:
        run_record_key(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"no simulation run {run_id!r} in this scope") from exc
    out = await delete_runs(_ops(ctx, request, user, scope_obj), [run_id])
    (result,) = out["results"]
    if result["status"] != "deleted":
        await _audit_results(ctx, request, user, scope_obj, "simulation_delete", out["results"], "deleted")
        raise HTTPException(status_code=403 if result.get("code") == "forbidden" else 502, detail=result["reason"])
    await _audit_results(ctx, request, user, scope_obj, "simulation_delete", out["results"], "deleted")
    await purge_deleted(ctx.storage, scope_obj, ctx.jobs)
    return FiniteJSONResponse(result)
