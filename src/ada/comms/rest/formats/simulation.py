"""A simulation provider's run (``simulation_run``).

Synthetic: there is no source file. ``conversion_options`` carries ``{"provider_id", "run_id"}``;
everything else about the run -- its parameters, its input keys, who started it -- is in the run
record the API wrote at ``job.derived_key`` before the job was published
(``ada.simulation.runs``). The job is routed to the provider's capability subject, so it only
reaches a worker that registered the provider.

The work itself is ``ada.simulation.runner.execute_run``, the same call the queue-less viewer
makes in-process. This module wires that call to the job: progress and stage go to the job's
status, and each stored artefact updates the job's ``artefacts_ready``
(``{"artefacts": [...], "complete": bool}``) so a viewer polling ``/convert/{job_id}`` can fetch
a partial artefact while the run is still going, as it does for the FEA bake.
"""

from __future__ import annotations

import asyncio
import time

import asyncpg

from ada.config import logger
from ada.simulation.registry import get_simulation_provider, simulation_providers
from ada.simulation.runner import execute_run
from ada.simulation.runs import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    TERMINAL_STATUSES,
    RunRecordError,
    dump_run_record,
    parse_run_record,
    utc_now,
)

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from ..worker.source_nodes import _SyncStorageFacade
from ..worker.state import _touch_liveness
from .registry import JobContext, SyntheticFormatHandler

SIMULATION_RUN_KIND = "simulation_run"


def unserved_provider_reason(provider_id: str) -> str:
    """Why this worker cannot run ``provider_id``: the one sentence for a misrouted job."""
    here = sorted(p.id for p in simulation_providers())
    return (
        f"simulation provider {provider_id!r} is not registered on this worker "
        f"(registered here: {here or 'none'}) -- is its module on ADA_WORKER_PRELOAD / an "
        f"ada.plugins entry point?"
    )


def _job_outcome(record: dict) -> tuple[str, str | None]:
    """The job status (and error) a finished run record reads as."""
    status = record.get("status")
    if status == STATUS_DONE:
        return JOB_STATUS_DONE, None
    if status == STATUS_CANCELLED:
        return "cancelled", None
    return JOB_STATUS_ERROR, str(record.get("error") or "the run failed")


async def _run_simulation(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    job_id = job.job_id
    opts = job.conversion_options or {}
    provider_id, run_id = opts.get("provider_id"), opts.get("run_id")

    async def fail(message: str, record: dict | None = None) -> None:
        # The record is what outlives the job, so a failure is written there too when there is
        # one to write to; a record that cannot be read is left alone.
        if record is not None:
            try:
                failed = {**record, "status": STATUS_ERROR, "stage": "error", "error": message}
                failed["finished_at"] = utc_now()
                await storage.put_bytes(scope, job.derived_key, dump_run_record(failed), content_encoding="gzip")
            except Exception:
                logger.exception("worker: could not record the failure of simulation run %s", run_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="simulation", error=message)
        await _audit_done(db_pool, job_id, "error", message, started_at)

    if not provider_id or not run_id:
        await fail("conversion_options.provider_id and run_id are required for a simulation_run")
        return

    try:
        record = parse_run_record(await storage.get_bytes(scope, job.derived_key))
    except (FileNotFoundError, KeyError):
        await fail(f"simulation run {run_id} has no run record at {job.derived_key}")
        return
    except RunRecordError as exc:
        await fail(f"simulation run {run_id}: unreadable run record ({exc})")
        return

    if record.get("status") in TERMINAL_STATUSES:
        # A redelivered message for a run that already ended (or was cancelled while queued):
        # the record is the outcome, and the provider is not run a second time.
        status, error = _job_outcome(record)
        await queue.update(job_id, status=status, stage=str(record.get("status")), progress=1.0, error=error)
        await _audit_done(db_pool, job_id, status, error, started_at)
        return

    provider = get_simulation_provider(str(provider_id))
    if provider is None:
        await fail(unserved_provider_reason(str(provider_id)), record)
        return

    record["job_id"] = job_id
    loop = asyncio.get_running_loop()
    sync_storage = _SyncStorageFacade(storage, scope, loop)
    # Every status write of this job goes through one lock: ``queue.update`` reads the job, sets
    # fields and writes it back, so a progress tick and an artefact update running interleaved
    # would each write the other's field back to its old value.
    status_lock = asyncio.Lock()
    last_progress_write = 0.0

    async def _update(**fields) -> None:
        async with status_lock:
            await queue.update(job_id, **fields)

    def on_progress(fraction: float, stage: str) -> None:
        # Called on the provider's thread. Throttled like the conversion progress callback, and
        # fire-and-forget: the provider does not wait for the status write.
        nonlocal last_progress_write
        _touch_liveness()
        now = time.monotonic()
        if now - last_progress_write < 0.25 and fraction < 1.0:
            return
        last_progress_write = now
        # Kept below 1.0: only the final status says the run is over.
        asyncio.run_coroutine_threadsafe(_update(stage=stage or "running", progress=min(fraction, 0.99)), loop)

    def on_artefacts(artefacts: list[dict], complete: bool) -> None:
        # Waited for, unlike progress: by the time ``store_artefact`` returns, a viewer that
        # polls the job can see the artefact.
        coro = _update(artefacts_ready={"artefacts": artefacts, "complete": complete})
        try:
            asyncio.run_coroutine_threadsafe(coro, loop).result()
        except Exception:
            logger.debug("worker: artefacts_ready update failed for %s", job_id, exc_info=True)

    await _update(stage="simulation", progress=0.05)
    try:
        finished = await loop.run_in_executor(
            None,
            lambda: execute_run(
                provider,
                record,
                sync_storage,
                scope=scope.prefix(),
                on_progress=on_progress,
                on_artefacts=on_artefacts,
            ),
        )
    except Exception as exc:
        # Not the provider failing (``execute_run`` reports that in the record): storage would
        # not take the record.
        logger.exception("worker: simulation run %s could not be recorded", run_id)
        await fail(f"{type(exc).__name__}: {exc}")
        return

    status, error = _job_outcome(finished)
    await _update(
        status=status,
        stage="ready" if status == JOB_STATUS_DONE else str(finished.get("status")),
        progress=1.0,
        error=error,
        artefacts_ready={"artefacts": finished.get("artefacts") or [], "complete": True},
    )
    logger.info(
        "worker: simulation run %s (%s) ended %s in %.1fs",
        run_id,
        provider_id,
        finished.get("status"),
        time.monotonic() - started_at,
    )
    await _audit_done(db_pool, job_id, status, error, started_at)


class SimulationRunHandler(SyntheticFormatHandler):
    kind = SIMULATION_RUN_KIND
    # The derived key is the run record, which exists from the moment the run is accepted: its
    # presence says nothing about the run being finished. The handler reads the record's own
    # status instead (see the redelivery branch above).
    skip_cached_short_circuit = True

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_simulation(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )


__all__ = ["SIMULATION_RUN_KIND", "SimulationRunHandler", "unserved_provider_reason"]
