"""Managing a scope's simulation runs after they were started: moving them between batches, and
deleting runs or whole batches.

A run's batch is stamped on its record and kept in the batch index beside the records
(``ada.simulation.batches``); both are written here the way a start with ``batch`` and the
after-the-fact backfill (``simulation_backfill``) write them, through ``write_batch_member`` /
``delete_batch_member`` / ``batch_index``.

RUNS THAT ARE GOING. A worker reads a run's record when it picks the job up and rewrites it from
that copy until the run ends (``ada.simulation.runner``). So:

* a QUEUED run can be moved: the worker that picks it up later reads the record with its new
  batch. (Should one pick it up in the same instant, the index still says the batch, which is what
  the list goes by; moving it again sets the record too.)
* a RUNNING run cannot be moved: the worker would write the old batch back over the new one, the
  way the backfill notes for "runs that were running the first time". It is refused, and moved
  once it has ended.
* a run that is deleted while it is going is cancelled first (the cancel route's work). One that
  had not been picked up ends there and then and is deleted whole: a worker that pulls its job
  later finds no record and does not run it. One a worker is running is deleted too, all but its
  cancel marker, which is how the worker learns to stop; a marker under
  ``_simulations/_deleted/<run_id>`` keeps it out of every listing, and what the worker writes
  until it has stopped is removed when the marker is looked at next (``purge_deleted``).

WHAT A RUN IS. Everything under its own directory (``_simulations/<run_id>/``: the record, the
artefacts the provider stored -- recordings, MCAP, GLBs, reports, the log -- and the cancel
marker), the derived blobs made from those (``_derived/_simulations/<run_id>/...``, the convert
cache of an artefact), its batch index entries and its ``sim_run`` element links. Not its inputs
or subject (files of the scope that other runs may use too), and not the "start a run of this
connection" claims (``ada.assoc`` intents), which say what was asked for, not what was kept. A run
handed to a remote Ada Studio is only this side's record: the remote keeps its own.

WHO. The same people who may write runs in the scope: in a personal or project scope, anyone who
can open it (its owner, the project's members); in the shared scope, whoever started the run,
or an admin (the shared scope's files are admin-managed). Admins may wherever they can open the
scope at all.

LIGHT, like ``simulation_runs``: stdlib, ``ada.simulation`` and the storage facade.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

from ada.simulation.batches import batch_of_record, check_batch
from ada.simulation.runs import (
    ACTIVE_STATUSES,
    STATUS_RUNNING,
    TERMINAL_STATUSES,
    RunRecordError,
    cancel_key,
    deleted_key,
    run_prefix,
    run_record_key,
    utc_now,
)

from .scope import Scope
from .simulation_runs import (
    SETTLED,
    batch_index,
    batch_run_ids,
    delete_batch_member,
    deleted_run_ids,
    read_run,
    read_runs,
    write_batch_member,
    write_run,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_RUN_IDS",
    "RunOps",
    "delete_batch",
    "delete_runs",
    "may_write_run",
    "may_write_scope",
    "move_runs",
    "purge_deleted",
    "remove_runs",
    "run_files",
    "run_ids_from",
    "summarise",
]

#: The most runs one request names.
MAX_RUN_IDS = 1000
#: How many runs are worked on at once.
_CONCURRENCY = 8
#: How long a run deleted while it was going may go without writing anything before it is taken as
#: stopped, when nothing knows its job any more.
_SILENT_S = 120.0
#: After this long a run deleted while it was going is taken as stopped whatever its job says.
_PENDING_MAX_S = 24 * 3600.0
#: How often one scope's deleted-while-going runs are looked at, at most.
_PURGE_EVERY_S = 10.0
_LAST_PURGE: dict[str, float] = {}


# ── who may ──────────────────────────────────────────────────────────────────────────────────────


def may_write_scope(user, scope: Scope) -> bool:
    """Whether ``user`` writes runs in ``scope`` whoever started them: an admin, or anyone who can
    open a personal or project scope (the caller has checked that they can)."""
    return bool(getattr(user, "is_admin", False)) or scope.kind in ("user", "project")


def may_write_run(user, scope: Scope, record: dict) -> bool:
    """Whether ``user`` may move or delete this run: as :func:`may_write_scope`, or the one who
    started it."""
    if may_write_scope(user, scope):
        return True
    started = str((record.get("started_by") or {}).get("id") or "")
    return bool(started) and started == str(getattr(user, "sub", "") or "")


# ── the request ──────────────────────────────────────────────────────────────────────────────────


def run_ids_from(raw: Any) -> list[str]:
    """``run_ids`` of a request body: a non-empty list of run ids, each once, in the order given.
    ValueError says what is wrong."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("'run_ids' must be a non-empty list of run ids")
    if len(raw) > MAX_RUN_IDS:
        raise ValueError(f"at most {MAX_RUN_IDS} runs at once (got {len(raw)})")
    out = []
    for item in raw:
        try:
            run_prefix(item)
        except ValueError as exc:
            raise ValueError(f"'run_ids': {exc}") from exc
        out.append(item)
    return list(dict.fromkeys(out))


def _result(run_id: str, status: str, *, code: str | None = None, reason: str | None = None, **extra) -> dict:
    out: dict[str, Any] = {"run_id": run_id, "status": status}
    if code is not None:
        out["code"] = code
    if reason is not None:
        out["reason"] = reason
    out.update(extra)
    return out


def summarise(results: Iterable[dict]) -> dict:
    """``{"results": [...], "counts": {status: n}, "bytes": n}`` of a bulk request."""
    results = list(results)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"results": results, "counts": counts, "bytes": sum(int(r.get("bytes") or 0) for r in results)}


async def _each(items: Iterable, fn: Callable[[Any], Awaitable[dict]]) -> list[dict]:
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(item):
        async with gate:
            return await fn(item)

    return list(await asyncio.gather(*(one(i) for i in items)))


@dataclass
class RunOps:
    """What the work needs from the API: storage, who is asking, and the route's own steps --
    the run as a client sees it, cancelling it (with its audit row), and dropping its links."""

    storage: Any
    scope: Scope
    user: Any
    jobs: Any
    view: Callable[[dict], Awaitable[dict]]
    cancel: Callable[[dict], Awaitable[dict]]
    unlink: Callable[[dict], Awaitable[None]]


async def _read(ops: RunOps, run_id: str) -> tuple[dict | None, str | None]:
    """The run's record, or None and why not (``missing`` / ``unreadable``)."""
    try:
        record = await read_run(ops.storage, ops.scope, run_id)
    except RunRecordError:
        return None, "unreadable"
    return record, (None if record is not None else "missing")


async def _status_now(ops: RunOps, record: dict) -> str:
    """The run's status with its job laid over the record (a queued record whose job a worker is
    running reads as running)."""
    if record.get("status") in TERMINAL_STATUSES:
        return str(record["status"])
    try:
        return str((await ops.view(record)).get("status") or record.get("status"))
    except Exception:  # noqa: BLE001 - a run that cannot be looked at is taken as its record says
        return str(record.get("status") or "")


def _memberships(index: dict[str, list[str]], run_id: str) -> set[str]:
    return {b for b, ids in index.items() if run_id in ids}


# ── moving runs between batches ──────────────────────────────────────────────────────────────────


async def _batch_named(storage, scope: Scope, batch_id: str, index: dict[str, list[str]]) -> dict | None:
    """The batch as its runs carry it (its label and source), from its newest record that says;
    None for a batch the scope has no runs of."""
    ids = index.get(batch_id) or []
    for start in range(0, len(ids), 16):
        for record in await read_runs(storage, scope, ids[start : start + 16]):
            batch = batch_of_record(record)
            if batch is not None and batch["id"] == batch_id:
                return batch
    return {"id": batch_id, "label": batch_id, "source": None} if ids else None


async def move_runs(ops: RunOps, batch_id: str, run_ids: list[str], *, label: str | None, replace: bool) -> dict:
    """Put ``run_ids`` in the batch ``batch_id``, which is made, called ``label``, when the scope
    has no runs of it yet (an existing batch keeps its label and source). A run in another batch is
    refused unless ``replace``; a running one is refused (see the module). Returns
    ``{"batch": {...}, "results": [...], ...}``, each result ``moved``, ``unchanged`` or
    ``refused`` with a ``code`` (``forbidden``, ``missing``, ``running``, ``other_batch``) and a
    ``reason``."""
    storage, scope = ops.storage, ops.scope
    index = await batch_index(storage, scope)
    target = await _batch_named(storage, scope, batch_id, index)
    if target is None:
        target = check_batch({"id": batch_id, "label": label or batch_id})
    assert target is not None
    indexed = set(index.get(batch_id) or [])

    async def one(run_id: str) -> dict:
        record, why = await _read(ops, run_id)
        if record is None:
            return _result(run_id, "refused", code="missing", reason=f"no simulation run {run_id} ({why})")
        if not may_write_run(ops.user, scope, record):
            return _result(
                run_id, "refused", code="forbidden", reason="only whoever started it or an admin may move it"
            )
        current = batch_of_record(record)
        elsewhere = (_memberships(index, run_id) | ({current["id"]} if current else set())) - {batch_id}
        if elsewhere and not replace:
            other = sorted(elsewhere)[0]
            return _result(
                run_id,
                "refused",
                code="other_batch",
                reason=f"it is in batch {other!r}; pass replace to move it",
                batch=other,
            )
        stamp = current != target
        if not stamp and run_id in indexed and not elsewhere:
            return _result(run_id, "unchanged")
        status = await _status_now(ops, record)
        if status == STATUS_RUNNING or (record.get("remote") and status in ACTIVE_STATUSES):
            return _result(
                run_id,
                "refused",
                code="running",
                reason="it is running, and its worker would write its old batch back: move it once it has ended",
            )
        for other in elsewhere:
            await delete_batch_member(storage, scope, other, run_id)
        if stamp:
            await write_run(storage, scope, {**record, "batch": target})
            SETTLED.forget(scope, run_id)
        await write_batch_member(storage, scope, batch_id, run_id)
        return _result(run_id, "moved", **({"from": sorted(elsewhere)[0]} if elsewhere else {}))

    out = summarise(await _each(run_ids, one))
    return {"batch": target, **out}


async def _take_out(ops: RunOps, batch_id: str, run_id: str, index: dict[str, list[str]]) -> dict:
    storage, scope = ops.storage, ops.scope
    in_index = run_id in (index.get(batch_id) or [])
    record, why = await _read(ops, run_id)
    if record is None:
        if in_index and may_write_scope(ops.user, scope):
            # An entry naming a run that is not there: nothing to keep it for.
            await delete_batch_member(storage, scope, batch_id, run_id)
            return _result(run_id, "removed", reason=f"no simulation run {run_id} ({why}); its batch entry was dropped")
        return _result(run_id, "refused", code="missing", reason=f"no simulation run {run_id} ({why})")
    if not may_write_run(ops.user, scope, record):
        return _result(run_id, "refused", code="forbidden", reason="only whoever started it or an admin may move it")
    current = batch_of_record(record)
    stamped = current is not None and current["id"] == batch_id
    if not in_index and not stamped:
        return _result(run_id, "unchanged")
    status = await _status_now(ops, record)
    if status == STATUS_RUNNING or (record.get("remote") and status in ACTIVE_STATUSES):
        return _result(
            run_id,
            "refused",
            code="running",
            reason="it is running, and its worker would write its batch back: take it out once it has ended",
        )
    if stamped:
        await write_run(storage, scope, {**record, "batch": None})
        SETTLED.forget(scope, run_id)
    await delete_batch_member(storage, scope, batch_id, run_id)
    return _result(run_id, "removed")


async def remove_runs(ops: RunOps, batch_id: str, run_ids: list[str]) -> dict:
    """Take ``run_ids`` out of the batch, back to the runs of no batch. Each result ``removed``,
    ``unchanged`` (not in it) or ``refused`` (``forbidden``, ``missing``, ``running``)."""
    index = await batch_index(ops.storage, ops.scope)
    return summarise(await _each(run_ids, lambda run_id: _take_out(ops, batch_id, run_id, index)))


# ── deleting runs ────────────────────────────────────────────────────────────────────────────────


def _derived_prefix(run_id: str) -> str:
    """Where the convert cache keeps what was made from the run's files (``_derived/<key>...``)."""
    return f"_derived/{run_prefix(run_id)}"


async def run_files(storage, scope: Scope, run_id: str) -> list:
    """Every stored file that is the run's: under its directory, and derived from those."""
    own = await storage.list_prefix(scope, run_prefix(run_id))
    derived = await storage.list_prefix(scope, _derived_prefix(run_id))
    return [*own, *derived]


async def _delete_files(
    storage, scope: Scope, run_id: str, *, keep: frozenset[str] = frozenset()
) -> tuple[int, list[str]]:
    """Delete the run's files but ``keep``: the record last, so a run whose files could not all be
    deleted is still listed and can be deleted again. Returns the bytes freed and the keys left."""
    entries = [e for e in await run_files(storage, scope, run_id) if e.key not in keep]
    record_key = run_record_key(run_id)
    rest = [e.key for e in entries if e.key != record_key]
    failed = await storage.delete_many(scope, rest)
    if not failed and any(e.key == record_key for e in entries):
        failed = await storage.delete_many(scope, [record_key])
    gone = set(failed)
    if not failed and not keep:
        _prune_local_dirs(storage, scope, (run_prefix(run_id), _derived_prefix(run_id)))
    return sum(int(e.size or 0) for e in entries if e.key not in gone), failed


def _prune_local_dirs(storage, scope: Scope, prefixes: Iterable[str]) -> None:
    """On a filesystem store, the run's directories once they are empty: an object store has no
    directories, but a filesystem keeps them, and the run would still be named by the one listing
    that finds the scope's runs."""
    local_path = getattr(storage, "local_path", None)
    if local_path is None:
        return
    for prefix in prefixes:
        root = local_path(scope, prefix.rstrip("/"))
        if root is None or not root.is_dir():
            continue
        for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass  # not empty: something else is there
        try:
            root.rmdir()
        except OSError:
            pass


async def _delete_one(ops: RunOps, run_id: str, index: dict[str, list[str]], deleted: set[str], *, dry: bool) -> dict:
    storage, scope = ops.storage, ops.scope
    record, why = await _read(ops, run_id)
    members = _memberships(index, run_id)
    if record is None:
        files = await run_files(storage, scope, run_id)
        size = sum(int(e.size or 0) for e in files)
        if not files and not members:
            # Already gone (or never was): deleting it again is what it was.
            return _result(run_id, "deleted", already=True, bytes=0, **({"pending": True} if run_id in deleted else {}))
        if not may_write_scope(ops.user, scope):
            return _result(
                run_id, "failed", code="forbidden", reason="only an admin may delete what is left of this run"
            )
        if dry:
            return _result(run_id, "deleted", dry=True, bytes=size)
        freed, failed = await _delete_files(
            storage, scope, run_id, keep=frozenset({cancel_key(run_id)}) if run_id in deleted else frozenset()
        )
        if failed:
            return _result(
                run_id,
                "failed",
                code="storage",
                reason=f"{len(failed)} of its files could not be deleted; try again",
                bytes=freed,
            )
        for batch_id in members:
            await delete_batch_member(storage, scope, batch_id, run_id)
        return _result(
            run_id, "deleted", bytes=freed, **({"reason": f"its record was {why}"} if why == "unreadable" else {})
        )

    if not may_write_run(ops.user, scope, record):
        return _result(run_id, "failed", code="forbidden", reason="only whoever started it or an admin may delete it")
    if dry:
        files = await run_files(storage, scope, run_id)
        return _result(
            run_id, "deleted", dry=True, bytes=sum(int(e.size or 0) for e in files), status_now=record.get("status")
        )

    extra: dict[str, Any] = {}
    pending = False
    status = await _status_now(ops, record)
    if status in ACTIVE_STATUSES:
        try:
            answer = await ops.cancel(record)
            status = str((answer.get("run") or {}).get("status") or status)
            extra["cancelled"] = True
        except Exception as exc:  # noqa: BLE001
            if not record.get("remote"):
                return _result(
                    run_id, "failed", code="cancel", reason=f"it could not be cancelled: {getattr(exc, 'detail', exc)}"
                )
            # The remote studio is not answering: this side's record goes, the remote keeps its run.
            extra["note"] = (
                f"the remote studio was not reached, so its run was not stopped: {getattr(exc, 'detail', exc)}"
            )
        if not record.get("remote") and status in ACTIVE_STATUSES:
            # A worker is running it: it stops at its next check of the cancel marker, which stays,
            # and what it writes until then is removed later (``purge_deleted``).
            pending = True
            marker = {
                "run_id": run_id,
                "job_id": record.get("job_id"),
                "at": utc_now(),
                "by": getattr(ops.user, "sub", None),
            }
            await storage.put_bytes(scope, deleted_key(run_id), json.dumps(marker).encode("utf-8"))
            deleted.add(run_id)
    freed, failed = await _delete_files(
        storage, scope, run_id, keep=frozenset({cancel_key(run_id)}) if pending else frozenset()
    )
    if failed:
        return _result(
            run_id,
            "failed",
            code="storage",
            reason=f"{len(failed)} of its files could not be deleted; try again",
            bytes=freed,
        )
    current = batch_of_record(record)
    for batch_id in members | ({current["id"]} if current else set()):
        await delete_batch_member(storage, scope, batch_id, run_id)
    SETTLED.forget(scope, run_id)
    try:
        await ops.unlink(record)
    except Exception as exc:  # noqa: BLE001 - links left behind name a run that is gone; not the delete's failure
        logger.warning("simulation delete: the links of run %s were not dropped: %s", run_id, exc)
    if pending:
        extra["pending"] = True
    return _result(run_id, "deleted", bytes=freed, **extra)


async def delete_runs(ops: RunOps, run_ids: list[str], *, dry: bool = False) -> dict:
    """Delete each of ``run_ids``: cancelled first while it is going, then its files, its batch
    entries and its links (see the module). Each result ``deleted`` (``already`` when there was
    nothing to delete, ``pending`` when its worker was still stopping) or ``failed`` with a
    ``code`` (``forbidden``, ``cancel``, ``storage``) and a ``reason``. ``dry`` deletes nothing and
    says what would go, with each run's ``bytes``."""
    index = await batch_index(ops.storage, ops.scope)
    deleted = await deleted_run_ids(ops.storage, ops.scope)
    return summarise(await _each(run_ids, lambda run_id: _delete_one(ops, run_id, index, deleted, dry=dry)))


async def delete_batch(ops: RunOps, batch_id: str, *, runs: bool, dry: bool = False) -> dict:
    """Delete the batch: with ``runs`` every run of it too (:func:`delete_runs`), else its runs go
    back to the runs of no batch (:func:`remove_runs`). ``batch.deleted`` is whether it is gone --
    not while a run of it was refused (one still running, one the caller may not move)."""
    run_ids = await batch_run_ids(ops.storage, ops.scope, batch_id)
    if not run_ids:
        return {"batch": {"id": batch_id, "deleted": True, "runs": 0}, **summarise([])}
    if runs:
        out = await delete_runs(ops, run_ids, dry=dry)
    elif dry:
        out = summarise(_result(i, "removed", dry=True) for i in run_ids)
    else:
        out = await remove_runs(ops, batch_id, run_ids)
    left = run_ids if dry else await batch_run_ids(ops.storage, ops.scope, batch_id)
    return {"batch": {"id": batch_id, "deleted": not left, "runs": len(run_ids)}, **out}


# ── the runs deleted while they were going ───────────────────────────────────────────────────────


def _age_s(iso: Any) -> float:
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds()


async def purge_deleted(storage, scope: Scope, jobs, *, force: bool = False) -> int:
    """Finish deleting the runs deleted while they were going whose worker has stopped: its last
    record says it ended, or nothing knows its job any more and it has been quiet a while, or it is
    a day old. Returns how many were finished. At most every few seconds per scope unless
    ``force``."""
    now = time.monotonic()
    if not force and now - _LAST_PURGE.get(scope.prefix(), -_PURGE_EVERY_S) < _PURGE_EVERY_S:
        return 0
    _LAST_PURGE[scope.prefix()] = now
    finished = 0
    for run_id in sorted(await deleted_run_ids(storage, scope)):
        try:
            try:
                marker = json.loads(await storage.get_bytes(scope, deleted_key(run_id)))
            except Exception:  # noqa: BLE001 - a marker that cannot be read is an old one
                marker = {}
            age = _age_s(marker.get("at"))
            try:
                record = await read_run(storage, scope, run_id)
            except RunRecordError:
                record = None
            over = age >= _PENDING_MAX_S or (record is not None and record.get("status") in TERMINAL_STATUSES)
            if not over and age >= _SILENT_S:
                job_id = (record or {}).get("job_id") or marker.get("job_id")
                over = not job_id or await jobs.status(str(job_id)) is None
            if not over:
                continue
            _, failed = await _delete_files(storage, scope, run_id)
            if failed:
                continue
            await storage.delete_many(scope, [deleted_key(run_id)])
            SETTLED.forget(scope, run_id)
            finished += 1
        except Exception as exc:  # noqa: BLE001 - one run is not the purge
            logger.warning("simulation purge: run %s not finished: %s", run_id, exc)
    return finished
