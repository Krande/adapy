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

    def __init__(self) -> None:
        self._jobs: dict[str, LocalJob] = {}
        self._lock = threading.Lock()

    def add(self, job: LocalJob) -> None:
        with self._lock:
            self._jobs[job.job_id] = job
            if len(self._jobs) > self.MAX_JOBS:
                for jid, j in list(self._jobs.items()):
                    if j.status != STATUS_RUNNING:
                        del self._jobs[jid]
                    if len(self._jobs) <= self.MAX_JOBS:
                        break

    def get(self, job_id: str) -> LocalJob | None:
        with self._lock:
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
    return getattr(importlib.import_module(mod_name), attr)


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

    Raises before returning if the plugin is not importable here — a 500 the
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
            if job.cancel_event.is_set():
                job.status = STATUS_CANCELLED
                job.stage = "cancelled"
                return
            job.result = result if isinstance(result, dict) else {"result": result}
            job.status = STATUS_DONE
            job.stage = "done"
            job.progress = 1.0
        except Exception as exc:  # noqa: BLE001 — the job's failure is data, not ours
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
