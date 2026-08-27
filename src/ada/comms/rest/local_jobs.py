"""In-process plugin jobs, for a viewer running without a worker pool.

A plugin's on-demand backend job normally goes onto NATS and is picked up by a
capability worker. That is the right shape for a deployment: the checks are long,
CPU-heavy and want their own pods.

It is the wrong shape for one person running the viewer on their laptop. There is
no NATS, so ``/api/plugins/{id}/jobs`` answered 503 and the plugin's "run" button
was dead — in the exact setup the examples put you in, where you have the SIN
open, the plugin installed, and nothing between you and the answer but a message
saying the queue is disabled.

So: when no queue is configured, run the job HERE, in a thread, and report it
through a job id the existing ``GET /api/convert/{job_id}`` endpoint can serve.
The plugin sees the same contract either way — the same ``job_entrypoint``, the
same sync storage facade, the same ``on_progress`` and ``cancel_event`` — so
nothing about a plugin has to know which mode it is in.

Deliberately NOT a queue. One dict, one executor, no persistence, no retries, no
cross-process anything. A single-node convenience with the failure modes of one:
jobs die with the process, and a second job runs concurrently with the first
rather than queueing behind it. Anything that wants more than that wants NATS,
which is the thing this stands in for rather than replaces.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Terminal + running states, spelled the way the queue spells them so the
#: frontend polling loop cannot tell the two paths apart.
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"


@dataclass
class LocalJob:
    job_id: str
    plugin_id: str
    scope_kind: str
    scope_id: str | None
    derived_key: str
    status: str = STATUS_RUNNING
    stage: str = "queued"
    progress: float = 0.0
    error: str | None = None
    result: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def as_json(self) -> dict[str, Any]:
        """The shape ``GET /api/convert/{job_id}`` returns for a queued job."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "error": self.error,
            "derived_key": self.derived_key,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
        }


class LocalJobRegistry:
    """Every in-process job this server has run. Bounded, and never persisted."""

    #: Jobs are tiny (a dict and a status string) but unbounded growth in a
    #: long-lived dev server is still a leak. Oldest-first eviction, and only of
    #: finished jobs — a running job is never evicted out from under its poller.
    MAX_JOBS = 200

    #: ...which is why MAX_JOBS alone does not bound anything: a job that never
    #: reaches a terminal state is never evictable, so N stuck jobs hold N entries
    #: (and N daemon threads) forever and the cap silently stops applying. A
    #: plugin that deadlocks on a lock, blocks on a socket with no timeout, or
    #: loops on bad input does exactly that, and nothing else in this module can
    #: notice: the thread cannot be killed and the job has no deadline.
    #:
    #: Past this age a job is declared stuck. Its cancel_event is set — a plugin
    #: that observes it between units of work stops burning CPU, the same
    #: cooperative contract the worker's cancel poller uses — and it goes terminal,
    #: so its poller gets an answer instead of "running" until the process dies,
    #: and the cap can reclaim it. Well above the minutes-to-an-hour a real check
    #: takes, because the cost of being wrong here is abandoning a legitimate run.
    MAX_RUNTIME_S = 6 * 60 * 60

    def __init__(self) -> None:
        self._jobs: dict[str, LocalJob] = {}
        self._lock = threading.Lock()

    def _sweep_locked(self) -> None:
        """Declare over-age running jobs stuck. Caller holds ``_lock``."""
        now = time.time()
        for job in self._jobs.values():
            if job.status != STATUS_RUNNING or (now - job.started_at) <= self.MAX_RUNTIME_S:
                continue
            logger.warning(
                "local plugin job %s (%s) has run for %.0fs (limit %ds) — abandoning it",
                job.job_id,
                job.plugin_id,
                now - job.started_at,
                self.MAX_RUNTIME_S,
            )
            job.cancel_event.set()
            # STATUS_ERROR, not a status of its own: the frontend polls one loop
            # for both paths and only knows what the queue spells. The nuance
            # goes in `stage`, which is free text either way.
            job.status = STATUS_ERROR
            job.stage = "timeout"
            job.error = f"job exceeded the {self.MAX_RUNTIME_S}s in-process limit and was abandoned"

    def add(self, job: LocalJob) -> None:
        with self._lock:
            self._sweep_locked()
            self._jobs[job.job_id] = job
            if len(self._jobs) > self.MAX_JOBS:
                for jid, j in list(self._jobs.items()):
                    if j.status != STATUS_RUNNING:
                        del self._jobs[jid]
                    if len(self._jobs) <= self.MAX_JOBS:
                        break

    def get(self, job_id: str) -> LocalJob | None:
        with self._lock:
            # Sweeping on read, not only on write, is what makes the deadline
            # reachable at all: a server that runs one job and never another gets
            # no `add` to sweep from, and polling is the one thing that is
            # guaranteed to happen while someone is waiting on the answer.
            self._sweep_locked()
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status != STATUS_RUNNING:
            return False
        job.cancel_event.set()
        return True


#: Module-level so the two endpoints (`POST .../jobs`, `GET /convert/{id}`) share
#: one registry without threading it through app state.
registry = LocalJobRegistry()


def _resolve_entrypoint(plugin_id: str) -> Any:
    """Import the callable a plugin advertises as its ``job_entrypoint``."""
    from ada.plugins import plugin_backend_spec

    spec = plugin_backend_spec(plugin_id)
    entry = (spec or {}).get("job_entrypoint")
    if not spec or not entry:
        raise LookupError(
            f"plugin {plugin_id!r} is not registered in this process or advertises no "
            f"job_entrypoint — is its backend on ADA_WORKER_PRELOAD / an ada.plugins "
            f"entry point? Running jobs in-process needs the plugin importable HERE, "
            f"not in a worker."
        )
    mod_name, _, attr = str(entry).partition(":")
    try:
        if not attr:
            raise ValueError(f"{entry!r} is not in 'module:callable' form")
        return getattr(importlib.import_module(mod_name), attr)
    except Exception as exc:
        # A registered plugin whose entrypoint does not import is a different
        # failure from an unregistered one, but it has the same answer here, so it
        # gets the same exception type. The worker turns this into a job error and
        # keeps serving; in-process there is no job yet, so it has to become the
        # response — and if it escapes as the raw ImportError/AttributeError it
        # becomes a bare 500 that names neither the plugin nor the entrypoint.
        #
        # The message carries the entrypoint string (already public: it rides in
        # the spec `GET /api/plugins` returns) and the exception TYPE, but not the
        # exception text — that is the one part that can quote a private module
        # path or a filesystem layout back to whoever called the endpoint. The
        # traceback goes to the log, where the person who can act on it is.
        logger.exception("local plugin job: entrypoint %r for plugin %r failed to resolve", entry, plugin_id)
        raise LookupError(
            f"plugin {plugin_id!r} advertises job_entrypoint {entry!r}, which this process could not "
            f"import ({type(exc).__name__}) — see the server log for the traceback. Running jobs "
            f"in-process needs the plugin importable HERE, not in a worker."
        ) from exc


def start_plugin_job(
    *,
    plugin_id: str,
    options: dict[str, Any],
    derived_prefix: str | None,
    derived_key: str,
    storage: Any,
    scope: Any,
) -> LocalJob:
    """Run a plugin job in a thread and return its handle immediately.

    Raises ``LookupError`` before returning if the plugin is not resolvable here
    (unregistered, or registered but its entrypoint will not import) — a 501 the
    caller can read beats a job id that reports an error two polls later.
    """
    fn = _resolve_entrypoint(plugin_id)
    loop = asyncio.get_running_loop()

    # The same synchronous storage view the worker hands a plugin, so the
    # entrypoint's `storage.get_bytes(...)` / `put_bytes(...)` work unchanged.
    from ada.comms.rest.worker import _SyncStorageFacade

    sync_storage = _SyncStorageFacade(storage, scope, loop)

    job = LocalJob(
        job_id=f"local-{uuid.uuid4().hex[:16]}",
        plugin_id=plugin_id,
        scope_kind=getattr(scope, "kind", "shared"),
        scope_id=getattr(scope, "id", None),
        derived_key=derived_key,
    )
    registry.add(job)

    def _on_progress(stage: str, frac: float) -> None:
        # Called from the job thread. Plain assignment: these are only ever read
        # by the polling endpoint, and a torn read of a float progress bar is not
        # a correctness problem worth a lock on every tick.
        job.stage = str(stage)
        try:
            job.progress = max(0.0, min(1.0, float(frac)))
        except (TypeError, ValueError):
            pass

    # Only pass cancel_event to an entrypoint that accepts it, so a plugin whose
    # signature predates the kwarg keeps working.
    try:
        sig = inspect.signature(fn)
        accepts_cancel = "cancel_event" in sig.parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        accepts_cancel = False

    def _run() -> None:
        try:
            kwargs: dict[str, Any] = {
                "storage": sync_storage,
                "scope": scope,
                "on_progress": _on_progress,
                "derived_prefix": derived_prefix,
            }
            if accepts_cancel:
                kwargs["cancel_event"] = job.cancel_event
            result = fn(options, **kwargs)
            if job.status != STATUS_RUNNING:
                # The registry already ruled on this job (age sweep) and a poller
                # may have read that verdict. Whatever the call finally returned,
                # the job is over — do not walk a terminal state back to `done`.
                return
            if job.cancel_event.is_set():
                job.status = STATUS_CANCELLED
                job.stage = "cancelled"
                return
            # Store the summary at `derived_key`, which is what the worker does
            # with the same return value. The caller was handed that key in the
            # POST response and fetches the JSON from it once the job reports
            # done, so without this write the two paths are identical right up to
            # the part where one of them produces an answer: the plugin runs, and
            # its result sits in a dict nothing can read (`as_json` does not
            # return it, by design — it is a blob, not a status field).
            payload = result if isinstance(result, dict) else {"result": result}
            job.stage = "upload"
            job.progress = 0.95
            sync_storage.put_bytes(derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
            job.result = payload
            job.status = STATUS_DONE
            job.stage = "done"
            job.progress = 1.0
        except Exception as exc:  # noqa: BLE001 — the job's failure is data, not ours
            if job.status != STATUS_RUNNING:
                return
            if job.cancel_event.is_set():
                job.status = STATUS_CANCELLED
                job.stage = "cancelled"
                return
            logger.exception("local plugin job %s (%s) failed", job.job_id, plugin_id)
            job.status = STATUS_ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            job.stage = "error"
            logger.debug("local plugin job traceback:\n%s", traceback.format_exc())

    # A plain daemon thread rather than the default executor: a capacity check
    # runs for minutes to an hour, and parking that on the loop's shared pool
    # would starve every other threadpool user for the duration.
    threading.Thread(target=_run, name=f"local-plugin-job-{plugin_id}", daemon=True).start()
    return job
