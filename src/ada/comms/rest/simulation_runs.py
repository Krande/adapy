"""The API's half of simulation runs: which providers exist, and reading and writing run records.

A run record is a JSON manifest in the scope's storage (``ada.simulation.runs``): written here
when a run is accepted, rewritten by whichever process runs the provider, and read back here for
as long as it is kept. The job behind a run is only looked at while the record is not finished,
to overlay live progress and to notice a job that ended without the record saying so.

LIGHT, like ``clash_runs``: stdlib, ``ada.simulation`` and the queue's token normaliser. Nothing
here imports a format handler.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from ada.simulation.interfaces import SIMULATION_DOMAINS
from ada.simulation.runs import (
    RUNS_PREFIX,
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_RUNNING,
    TERMINAL_STATUSES,
    RunRecordError,
    cancel_key,
    dump_run_record,
    parse_run_record,
    run_record_key,
    utc_now,
)

from .job_transport import JobSnapshot, JobTransport
from .plugin_registry import locally_registered_simulation_providers
from .queue import capability_token
from .scope import Scope

__all__ = [
    "domain_listing",
    "list_providers",
    "list_run_ids",
    "provider_by_id",
    "providers_from_workers",
    "read_run",
    "run_view",
    "write_run",
]

#: The fields of a provider every listing carries, whatever a worker advertised beside them.
_PROVIDER_FIELDS = ("id", "domain", "engine", "title", "version", "description", "kinds", "parameters_schema")

#: How long a record may sit without a job id before it is read as never submitted: the API
#: writes the record, then submits the job, and a process that died in between leaves one.
_UNSUBMITTED_AFTER_S = 120.0


def domain_listing() -> list[dict]:
    """The kinds of simulation, in their fixed order, each with its artefact roles: what a client
    groups providers by and what it may rely on in a result of that kind."""
    return [d.to_dict() for d in SIMULATION_DOMAINS.values()]


def _sorted(entries) -> list[dict]:
    """Grouped by domain, in the domains' own order, then by title."""
    order = {d: i for i, d in enumerate(SIMULATION_DOMAINS)}
    return sorted(entries, key=lambda e: (order[e["domain"]], str(e["title"]), e["id"]))


def _provider_entry(spec: dict, *, online: bool, workers: int) -> dict:
    entry = {k: spec.get(k) for k in _PROVIDER_FIELDS}
    entry["title"] = entry.get("title") or entry["id"]
    entry["kinds"] = entry.get("kinds") if isinstance(entry.get("kinds"), list) else []
    schema = entry.get("parameters_schema")
    entry["parameters_schema"] = schema if isinstance(schema, dict) else {}
    entry["capability"] = capability_token(spec.get("capability"))
    entry["online"] = online
    entry["workers"] = workers
    return entry


def providers_from_workers(rows: list[dict], *, now: float, stale_after_s: float) -> list[dict]:
    """Every simulation provider in the worker registry, one entry per provider id, grouped by
    domain, each with whether a worker serving it is alive.

    A worker counts when its heartbeat is fresh AND it serves the provider's capability (it may
    advertise a provider whose capability it was not allowed to serve). The description comes
    from a worker that counts when there is one, else from the worker heard from last, so a
    provider whose workers have all stopped is still listed -- as offline -- until its registry
    rows are pruned. ``unavailable_reason`` says why a live worker withholds it, when one does.
    """
    out: dict[str, dict] = {}
    seen_at: dict[str, float] = {}
    # Sorted so the entry that describes a provider does not depend on listing order.
    for row in sorted(rows, key=lambda r: str(r.get("worker_id") or "")):
        heartbeat = row.get("last_heartbeat")
        heartbeat = float(heartbeat) if isinstance(heartbeat, (int, float)) else 0.0
        alive = (now - heartbeat) <= stale_after_s
        withheld = {
            capability_token(w.get("capability")): str(w.get("reason") or "")
            for w in (row.get("withheld") or [])
            if isinstance(w, dict) and w.get("capability")
        }
        for spec in row.get("simulation_providers") or []:
            if not isinstance(spec, dict) or not isinstance(spec.get("id"), str) or not spec["id"]:
                continue
            if spec.get("domain") not in SIMULATION_DOMAINS:
                # A worker on another version naming a kind this API does not have: the set is
                # closed, so it is not listed rather than listed under a kind nothing can open.
                continue
            provider_id = spec["id"]
            serving = alive and bool(spec.get("serving"))
            current = out.get(provider_id)
            if current is None:
                out[provider_id] = _provider_entry(spec, online=serving, workers=1 if serving else 0)
                seen_at[provider_id] = heartbeat
            elif serving:
                workers = current["workers"] + 1
                if not current["online"]:
                    # The first worker that actually serves it describes it.
                    out[provider_id] = _provider_entry(spec, online=True, workers=workers)
                else:
                    current["workers"] = workers
                seen_at[provider_id] = max(seen_at[provider_id], heartbeat)
            elif not current["online"] and heartbeat > seen_at[provider_id]:
                reason = current.get("unavailable_reason")
                out[provider_id] = _provider_entry(spec, online=False, workers=0)
                if reason:
                    out[provider_id]["unavailable_reason"] = reason
                seen_at[provider_id] = heartbeat
            entry = out[provider_id]
            reason = withheld.get(capability_token(spec.get("capability")))
            if alive and reason and not entry["online"]:
                entry["unavailable_reason"] = reason
    for entry in out.values():
        if entry["online"]:
            entry.pop("unavailable_reason", None)
    return _sorted(out.values())


async def list_providers(jobs: JobTransport, *, stale_after_s: float) -> list[dict]:
    """The providers this deployment knows. Behind a queue: the worker registry's. Without one:
    the providers registered in this process, which are online by definition -- it is the
    process that would run them."""
    if jobs.kind == "local":
        return _sorted(
            _provider_entry(spec, online=True, workers=1) for spec in locally_registered_simulation_providers()
        )
    return providers_from_workers(await jobs.capabilities(), now=time.time(), stale_after_s=stale_after_s)


def provider_by_id(providers: list[dict], provider_id: str) -> dict | None:
    return next((p for p in providers if p["id"] == provider_id), None)


async def write_run(storage, scope: Scope, record: dict) -> None:
    await storage.put_bytes(scope, run_record_key(record["run_id"]), dump_run_record(record), content_encoding="gzip")


async def read_run(storage, scope: Scope, run_id: str) -> dict | None:
    """The run's record, or None when there is none (or ``run_id`` is not a run id)."""
    try:
        key = run_record_key(run_id)
    except ValueError:
        return None
    try:
        return parse_run_record(await storage.get_bytes(scope, key))
    except (FileNotFoundError, KeyError):
        return None


async def list_run_ids(storage, scope: Scope) -> list[str]:
    """The scope's run ids, newest first. One delimited listing: run ids sort by creation time."""
    names = await storage.list_child_names(scope, RUNS_PREFIX)
    return sorted((n for n in names if len(n) == 32), reverse=True)


def _age_s(iso: Any) -> float:
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds()


async def _end_without_engine(storage, scope: Scope, record: dict, status: str, error: str | None) -> dict:
    """Finish a record nothing is running any more, and store it."""
    record = {**record, "status": status, "stage": status, "error": error, "finished_at": utc_now()}
    await write_run(storage, scope, record)
    return record


async def run_view(storage, scope: Scope, jobs: JobTransport, record: dict) -> dict:
    """The run as a client sees it: the stored record, brought up to date with its job.

    A finished record is returned as stored. An unfinished one is compared with its job:

    * the job is alive: its progress, stage and artefact list (``artefacts_ready``) are laid over
      the record, so a partial artefact is listed as soon as it is in storage;
    * the job ended in an error the record does not carry (refused by a pool that does not have
      the provider, lost to a crashed worker): the record is finished with the job's error;
    * the server has no such job any more: the record is finished as cancelled when a cancel
      was asked for, else as an error saying the job was lost.

    ``cancel_requested`` is true while a cancel has been asked for and the run has not ended.
    """
    if record.get("status") in TERMINAL_STATUSES:
        return {**record, "cancel_requested": False}

    job_id = record.get("job_id")
    cancel_requested = await storage.exists(scope, cancel_key(record["run_id"]))
    if not job_id:
        # In-process, the job stamps its id on the record as it starts; behind a queue the id is
        # written before the job is published. So no id means "not started yet", for a while.
        if _age_s(record.get("created_at")) > _UNSUBMITTED_AFTER_S:
            record = await _end_without_engine(
                storage, scope, record, STATUS_ERROR, "the run was accepted but its job was never submitted"
            )
            return {**record, "cancel_requested": False}
        return {**record, "cancel_requested": cancel_requested}

    snapshot: JobSnapshot | None = await jobs.status(str(job_id))
    if snapshot is None:
        if cancel_requested:
            record = await _end_without_engine(storage, scope, record, STATUS_CANCELLED, None)
        else:
            record = await _end_without_engine(
                storage,
                scope,
                record,
                STATUS_ERROR,
                "the job behind this run is no longer known to the server; it was lost before the run finished",
            )
        return {**record, "cancel_requested": False}

    payload = snapshot.payload
    if snapshot.status == "error":
        # The engine writes the record before it reports the job, so a failed job with an
        # unfinished record is one the engine never got to finish: read once more, then say so.
        fresh = await read_run(storage, scope, record["run_id"])
        if fresh is not None and fresh.get("status") in TERMINAL_STATUSES:
            return {**fresh, "cancel_requested": False}
        record = await _end_without_engine(
            storage, scope, record, STATUS_ERROR, str(payload.get("error") or "the job failed")
        )
        return {**record, "cancel_requested": False}

    view = {**record, "cancel_requested": cancel_requested}
    if snapshot.status == STATUS_RUNNING:
        view["status"] = STATUS_RUNNING
    if isinstance(payload.get("progress"), (int, float)):
        view["progress"] = max(float(record.get("progress") or 0.0), float(payload["progress"]))
    if payload.get("stage"):
        view["stage"] = payload["stage"]
    ready = payload.get("artefacts_ready")
    if isinstance(ready, dict) and isinstance(ready.get("artefacts"), list):
        view["artefacts"] = ready["artefacts"]
    return view


__all__ += ["RunRecordError"]
