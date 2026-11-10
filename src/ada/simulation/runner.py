"""Running one simulation: the provider call, its context and the run record around them.

One implementation for both ways a run is started: a worker's ``simulation_run`` job and the
in-process job of a viewer without a queue call :func:`execute_run` with the same arguments, so a
provider cannot tell which one it is running under.

Storage is whatever synchronous, scope-bound facade the caller hands in (``get_bytes``,
``put_bytes``, ``put_path``, ``fetch_to_path`` -- what a plugin job is handed); the two callbacks
are how progress and the artefact list reach the job's status while the run is going.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import mimetypes
import os
import pathlib
import shutil
import tempfile
import threading
import time
import traceback
from typing import Any, Callable, Mapping, Protocol

from .interfaces import (
    ArtefactData,
    SimulationArtefact,
    SimulationCancelled,
    SimulationCase,
    SimulationContext,
    SimulationProvider,
    SimulationResult,
    allowed_artefact_roles,
    simulation_domain,
)
from .runs import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_RUNNING,
    artefact_key,
    cancel_key,
    dump_run_record,
    run_record_key,
    utc_now,
)

logger = logging.getLogger(__name__)

__all__ = ["LOG_ARTEFACT", "RunStorage", "execute_run", "write_run_record"]

#: The artefact ``SimulationContext.log`` lines are kept in.
LOG_ARTEFACT = "log.txt"

# How often a running provider's context looks for the cancel marker in storage, writes the log
# artefact, and rewrites the run record for an artefact that was only stored again.
_CANCEL_POLL_S = 1.0
_LOG_FLUSH_S = 2.0
_RECORD_WRITE_S = 2.0

# A summary is shown beside the run, not downloaded: data belongs in an artefact.
_MAX_SUMMARY_BYTES = 256 * 1024

ProgressCallback = Callable[[float, str], None]
ArtefactsCallback = Callable[[list[dict], bool], None]


class RunStorage(Protocol):
    """The scope-bound storage a run reads and writes through (keys are scope-relative)."""

    def get_bytes(self, key: str) -> bytes: ...

    def put_bytes(self, key: str, data: bytes, content_encoding: str | None = None) -> None: ...

    def put_path(self, key: str, src: Any) -> Any: ...

    def fetch_to_path(self, key: str, dest: Any) -> Any: ...


def write_run_record(storage: RunStorage, record: Mapping[str, Any]) -> None:
    """Store a run record at its key (gzip at rest, like the other JSON manifests)."""
    storage.put_bytes(run_record_key(record["run_id"]), dump_run_record(record), content_encoding="gzip")


def _cancel_requested(storage: RunStorage, run_id: str) -> bool:
    try:
        storage.get_bytes(cancel_key(run_id))
    except (FileNotFoundError, KeyError):
        return False
    except Exception:  # noqa: BLE001 - storage being unreachable is not a cancel
        logger.debug("ada.simulation: cancel marker check failed for %s", run_id, exc_info=True)
        return False
    return True


class _RunContext(SimulationContext):
    """The context of one run. Thread-safe: a provider may report from threads of its own."""

    def __init__(
        self,
        *,
        record: dict[str, Any],
        case: SimulationCase,
        storage: RunStorage,
        workdir: pathlib.Path,
        domain: str,
        cancel_event: threading.Event,
        on_progress: ProgressCallback | None,
        on_artefacts: ArtefactsCallback | None,
    ) -> None:
        self.workdir = workdir
        self._domain = domain
        self._roles = allowed_artefact_roles(domain)
        self._record = record
        self._case = case
        self._storage = storage
        self._cancel_event = cancel_event
        self._on_progress = on_progress
        self._on_artefacts = on_artefacts
        self._lock = threading.RLock()
        self._artefacts: dict[str, SimulationArtefact] = {}
        self._log_lines: list[str] = []
        self._log_dirty = False
        self._last_log_flush = time.monotonic()
        self._last_record_write = 0.0
        self._last_cancel_poll = 0.0
        self.fraction = 0.0
        self.stage = ""

    # -- reporting ---------------------------------------------------------

    def progress(self, fraction: float, stage: str = "") -> None:
        try:
            fraction = max(0.0, min(1.0, float(fraction)))
        except (TypeError, ValueError):
            return
        with self._lock:
            self.fraction = fraction
            if stage:
                self.stage = str(stage)
            stage_now = self.stage
        if self._on_progress is not None:
            try:
                self._on_progress(fraction, stage_now)
            except Exception:  # noqa: BLE001 - a status hiccup must never sink the run
                logger.debug("ada.simulation: progress callback failed", exc_info=True)

    def log(self, line: str) -> None:
        with self._lock:
            self._log_lines.append(str(line).rstrip("\n"))
            self._log_dirty = True
            due = time.monotonic() - self._last_log_flush >= _LOG_FLUSH_S
        if due:
            self.flush_log(partial=True)

    def flush_log(self, *, partial: bool) -> None:
        """Write the log lines so far as the log artefact. A run that logged nothing has none."""
        with self._lock:
            if not self._log_lines or not (self._log_dirty or not partial):
                return
            data = ("\n".join(self._log_lines) + "\n").encode("utf-8")
            self._log_dirty = False
            self._last_log_flush = time.monotonic()
        try:
            self.store_artefact(LOG_ARTEFACT, data, partial=partial, role="log", media_type="text/plain")
        except Exception:  # noqa: BLE001 - a log that cannot be stored must not fail the run
            logger.debug("ada.simulation: storing the run log failed", exc_info=True)

    # -- cancellation ------------------------------------------------------

    def cancelled(self) -> bool:
        if self._cancel_event.is_set():
            return True
        now = time.monotonic()
        with self._lock:
            if now - self._last_cancel_poll < _CANCEL_POLL_S:
                return False
            self._last_cancel_poll = now
        if _cancel_requested(self._storage, self._case.run_id):
            self._cancel_event.set()
            return True
        return False

    # -- artefacts ---------------------------------------------------------

    def store_artefact(
        self,
        name: str,
        data_or_path: ArtefactData,
        *,
        partial: bool = False,
        role: str = "data",
        media_type: str | None = None,
    ) -> SimulationArtefact:
        key = artefact_key(self._case.run_id, name)  # ValueError for a name that is not one
        if role not in self._roles:
            raise ValueError(
                f"artefact {name!r}: {role!r} is not a role of a {self._domain} run (use one of {', '.join(self._roles)})"
            )
        if isinstance(data_or_path, (bytes, bytearray, memoryview)):
            data = bytes(data_or_path)
            self._storage.put_bytes(key, data)
            size = len(data)
        else:
            path = pathlib.Path(os.fspath(data_or_path))
            if not path.is_file():
                raise FileNotFoundError(f"artefact {name!r}: no file at {path}")
            size = path.stat().st_size
            self._storage.put_path(key, path)
        guessed = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        with self._lock:
            previous = self._artefacts.get(name)
            artefact = SimulationArtefact(
                name=name,
                key=key,
                media_type=guessed,
                role=role,
                size=size,
                partial=bool(partial),
                revision=(previous.revision + 1) if previous else 1,
            )
            self._artefacts[name] = artefact
            # The record is rewritten at once for a new artefact or one that became final, and
            # at most every few seconds for one that was only stored again: a provider may
            # store a growing file many times a second, and the job status carries those.
            changed = previous is None or previous.partial != artefact.partial
            write = changed or time.monotonic() - self._last_record_write >= _RECORD_WRITE_S
        if write:
            self.persist()
        self._notify_artefacts(complete=False)
        return artefact

    def artefact_dicts(self) -> list[dict]:
        with self._lock:
            return [a.to_dict() for a in self._artefacts.values()]

    def _notify_artefacts(self, *, complete: bool) -> None:
        if self._on_artefacts is None:
            return
        try:
            self._on_artefacts(self.artefact_dicts(), complete)
        except Exception:  # noqa: BLE001 - as for progress
            logger.debug("ada.simulation: artefacts callback failed", exc_info=True)

    def finalise(self, result: SimulationResult) -> dict[str, Any]:
        """Mark the artefacts the result names as final, check the domain's required roles are
        all there, and return the result's summary, checked."""
        with self._lock:
            for item in result.artefacts:
                stored = self._artefacts.get(getattr(item, "name", None))
                if stored is None or stored.key != getattr(item, "key", None):
                    raise ValueError(
                        f"the result lists artefact {getattr(item, 'name', item)!r}, which was not stored "
                        "through the context (use ctx.store_artefact and return what it gave back)"
                    )
                self._artefacts[stored.name] = dataclasses.replace(stored, partial=False)
            final_roles = {a.role for a in self._artefacts.values() if not a.partial}
        missing = [
            r.id for r in simulation_domain(self._domain).artefact_roles if r.required and r.id not in final_roles
        ]
        if missing:
            raise ValueError(
                f"a finished {self._domain} run needs a final artefact for each of the roles "
                f"{', '.join(missing)}, and this one stored none"
            )
        summary = dict(result.summary or {})
        try:
            encoded = json.dumps(summary)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"the result's summary is not JSON-serialisable: {exc}") from exc
        if len(encoded) > _MAX_SUMMARY_BYTES:
            raise ValueError(
                f"the result's summary is {len(encoded)} bytes; keep it under {_MAX_SUMMARY_BYTES} "
                "and put data in an artefact"
            )
        return summary

    # -- inputs ------------------------------------------------------------

    def _input(self, key: str) -> str:
        if key not in self._case.inputs:
            raise ValueError(f"{key!r} is not one of this run's input files")
        return key

    def read_input(self, key: str) -> bytes:
        return self._storage.get_bytes(self._input(key))

    def fetch_input(self, key: str, dest: "str | os.PathLike[str] | None" = None) -> pathlib.Path:
        key = self._input(key)
        if dest is None:
            target = self.workdir / "inputs" / key.strip("/")
        else:
            target = pathlib.Path(os.fspath(dest))
        target.parent.mkdir(parents=True, exist_ok=True)
        self._storage.fetch_to_path(key, target)
        return target

    # -- the record --------------------------------------------------------

    def persist(self, **fields: Any) -> None:
        """Rewrite the run record with the current progress and artefact list (and ``fields``)."""
        with self._lock:
            self._record.update(fields)
            self._record["progress"] = self.fraction
            if self.stage:
                self._record["stage"] = self.stage
            self._record["artefacts"] = [a.to_dict() for a in self._artefacts.values()]
            snapshot = dict(self._record)
            self._last_record_write = time.monotonic()
        write_run_record(self._storage, snapshot)


def execute_run(
    provider: SimulationProvider,
    record: Mapping[str, Any],
    storage: RunStorage,
    *,
    scope: str,
    cancel_event: threading.Event | None = None,
    on_progress: ProgressCallback | None = None,
    on_artefacts: ArtefactsCallback | None = None,
) -> dict[str, Any]:
    """Run ``record``'s case with ``provider`` and return the finished record.

    Blocking; call it from a worker thread. It does not raise for anything the provider does: the
    outcome is the record's ``status`` (``done``, ``error`` or ``cancelled``), ``error`` and
    artefact list, which are also what it leaves in storage. ``scope`` is the label handed to the
    provider as ``SimulationCase.scope``.

    Cancellation is cooperative. ``cancel_event`` is set by a caller that can reach this process;
    a cancel asked for anywhere else is found through the run's cancel marker in storage, which
    the context looks for when the provider asks ``ctx.cancelled()``.
    """
    record = dict(record)
    run_id = record["run_id"]
    cancel_event = cancel_event or threading.Event()
    case = SimulationCase(
        run_id=run_id,
        scope=scope,
        parameters=dict(record.get("parameters") or {}),
        inputs=tuple(record.get("inputs") or ()),
        kind=record.get("kind"),
    )
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="ada-sim-"))
    ctx = _RunContext(
        record=record,
        case=case,
        storage=storage,
        workdir=workdir,
        domain=provider.domain,
        cancel_event=cancel_event,
        on_progress=on_progress,
        on_artefacts=on_artefacts,
    )

    status, error, summary = STATUS_DONE, None, {}
    try:
        if cancel_event.is_set() or _cancel_requested(storage, run_id):
            # Cancelled while it was still queued: the provider is never called.
            cancel_event.set()
            raise SimulationCancelled("cancelled before it started")
        ctx.persist(status=STATUS_RUNNING, stage="running", started_at=utc_now(), error=None)
        result = provider.run(case, ctx)
        if not isinstance(result, SimulationResult):
            raise TypeError(f"run() must return a SimulationResult, got {type(result).__name__}")
        summary = ctx.finalise(result)
        if cancel_event.is_set():
            # The provider returned although a cancel was asked for: what it stored is kept
            # and listed, and the run still reads as cancelled, which is what the user asked.
            status = STATUS_CANCELLED
    except SimulationCancelled:
        status = STATUS_CANCELLED
    except Exception as exc:  # noqa: BLE001 - the provider's failure is the run's outcome
        if cancel_event.is_set():
            status = STATUS_CANCELLED
        else:
            status, error = STATUS_ERROR, f"{type(exc).__name__}: {exc}"
            logger.exception("ada.simulation: provider %s failed run %s", provider.id, run_id)
            ctx.log(traceback.format_exc())
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    ctx.flush_log(partial=False)
    if status == STATUS_DONE:
        ctx.fraction = 1.0
    ctx.stage = status
    ctx.persist(status=status, error=error, summary=summary, finished_at=utc_now())
    ctx._notify_artefacts(complete=True)
    return dict(record)
