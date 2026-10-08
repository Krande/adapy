"""In-process plugin jobs, for a viewer running without a worker pool.

THIS IS THE ENGINE, NOT THE CONTRACT. Nothing outside
:mod:`ada.comms.rest.job_transport` calls into here: a route submits a
``JobRequest`` to ``RestContext.jobs`` and ``LocalJobTransport`` is what turns
that into the thread below. Start from ``job_transport`` for which job kinds exist without a
queue and what a route gets when one does not.

A plugin's on-demand backend job normally goes onto NATS and is picked up by a
capability worker. That is the right shape for a deployment: the checks are long,
CPU-heavy and want their own pods.

It is the wrong shape for one person running the viewer on their laptop. There is
no NATS, so ``/api/plugins/{id}/jobs`` answered 503 and the plugin's "run" button
was dead — in the exact setup the examples put you in, where you have the model
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
import hashlib
import importlib
import inspect
import json
import logging
import pathlib
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

from .plugin_registry import locally_registered_spec

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
    spec = locally_registered_spec(plugin_id)
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


def start_asset_build(
    *,
    request: Any,
    capability: str,
    options: dict[str, Any],
    derived_prefix: str,
    derived_key: str,
    storage: Any,
    scope: Any,
) -> LocalJob:
    """Run an ``asset_build`` in a thread -- the queue-less half of Decision 1's ``build`` kind.

    Same argument as ``start_plugin_job``: a single-node viewer with an asset published in its
    own scope should be able to load it, and a 503 there would make ``build`` a delivery kind
    that only exists in a cluster. The builder sees exactly what the worker hands it (the sync
    storage facade, the core-composed derived prefix, a cancel event), and the summary is
    validated and stored the same way, so neither the builder nor the browser can tell the
    transports apart.

    Raises ``LookupError`` before returning when no builder here serves the capability -- a 501
    the caller can read beats a job id that errors two polls later.
    """
    from ada.assets.build import (
        BuildError,
        BuildSummary,
        parse_build_summary,
        validate_build_summary,
    )
    from ada.assets.builders import asset_builder

    builder = asset_builder(capability)  # LookupError if this process does not serve it
    loop = asyncio.get_running_loop()

    from ada.comms.rest.worker import _SyncStorageFacade

    sync_storage = _SyncStorageFacade(storage, scope, loop)

    job = LocalJob(
        job_id=f"local-{uuid.uuid4().hex[:16]}",
        plugin_id=capability,
        scope_kind=getattr(scope, "kind", "shared"),
        scope_id=getattr(scope, "id", None),
        derived_key=derived_key,
    )
    registry.add(job)

    def _on_progress(stage: str, frac: float) -> None:
        job.stage = str(stage)
        try:
            job.progress = max(0.0, min(1.0, float(frac)))
        except (TypeError, ValueError):
            pass

    def _run() -> None:
        try:
            result = builder.build(
                options,
                request=request,
                storage=sync_storage,
                scope=scope,
                derived_prefix=derived_prefix,
                on_progress=_on_progress,
                cancel_event=job.cancel_event,
            )
            if job.status != STATUS_RUNNING:
                return
            if job.cancel_event.is_set():
                job.status = STATUS_CANCELLED
                job.stage = "cancelled"
                return
            summary = result if isinstance(result, BuildSummary) else parse_build_summary(result)
            validate_build_summary(
                summary,
                provider=request.provider,
                collection=request.collection,
                subject=request.subject,
                revision=request.revision,
                node=request.node,
                fingerprint=request.fingerprint,
                derived_prefix=derived_prefix,
            )
            job.stage = "upload"
            job.progress = 0.95
            payload = summary.to_dict()
            sync_storage.put_bytes(derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
            job.result = payload
            job.status = STATUS_DONE
            job.stage = "done"
            job.progress = 1.0
        except BuildError as exc:
            # A summary core refuses is the builder's bug, and it must not reach the store: the
            # browser would then validate a blob it did not ask for and blame the wrong side.
            if job.status != STATUS_RUNNING:
                return
            logger.error("local asset build %s refused its own summary: %s", job.job_id, exc)
            job.status = STATUS_ERROR
            job.error = str(exc)
            job.stage = "error"
        except Exception as exc:  # noqa: BLE001 — the build's failure is data, not ours
            if job.status != STATUS_RUNNING:
                return
            if job.cancel_event.is_set():
                job.status = STATUS_CANCELLED
                job.stage = "cancelled"
                return
            logger.exception("local asset build %s (%s) failed", job.job_id, capability)
            job.status = STATUS_ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            job.stage = "error"
            logger.debug("local asset build traceback:\n%s", traceback.format_exc())

    threading.Thread(target=_run, name=f"local-asset-build-{capability}", daemon=True).start()
    return job


def start_asset_publish(
    *,
    provider_id: str,
    staged: dict[str, str],
    collection: "str | None",
    options: dict[str, Any],
    published_by: str,
    published_by_display: "str | None",
    published_via: str,
    dry_run: bool,
    replace_existing: bool,
    derived_key: str,
    storage: Any,
    scope: Any,
) -> LocalJob:
    """Run an ``asset_publish`` in a thread. The queue-less half of the publish surface.

    The provider PLANS and core writes here too (``apply_publish_plan``), so the owner gate and
    the manifests-last ordering hold identically on a laptop and on a cluster -- a publish is the
    one operation where "it behaved differently in the small deployment" would mean a store whose
    records cannot be trusted.
    """
    from ada.assets.manifest import Actor
    from ada.assets.publish import apply_publish_plan, stored_encoding
    from ada.assets.publishers import asset_publisher

    publisher = asset_publisher(provider_id)  # LookupError if this process cannot publish it
    loop = asyncio.get_running_loop()

    from ada.comms.rest.worker import _SyncStorageFacade

    sync_storage = _SyncStorageFacade(storage, scope, loop)

    job = LocalJob(
        job_id=f"local-{uuid.uuid4().hex[:16]}",
        plugin_id=provider_id,
        scope_kind=getattr(scope, "kind", "shared"),
        scope_id=getattr(scope, "id", None),
        derived_key=derived_key,
    )
    registry.add(job)

    def _run() -> None:
        try:
            job.stage = "derive"
            job.progress = 0.1
            plan = publisher.derive(
                scope,
                dict(staged),
                storage=sync_storage,
                collection=collection,
                options=dict(options),
                dry_run=dry_run,
            )
            if job.status != STATUS_RUNNING:
                return
            occupied: set[str] = set()
            for prefix in sorted({w.key.rsplit("/", 1)[0] for w in plan.writes}):
                occupied.update(sync_storage.list_keys(f"{prefix}/"))
            job.stage = "publish"
            job.progress = 0.6
            outcome = apply_publish_plan(
                plan,
                published_by=Actor(id=published_by, display=published_by_display),
                published_via=published_via,
                dry_run=dry_run,
                replace_existing=replace_existing,
                occupied=occupied,
                # A spine arrives gzip-compressed (gzip at rest): stored as-is, and labelled.
                write=lambda key, data: sync_storage.put_bytes(
                    key, data, content_encoding=stored_encoding(data), pre_compressed=stored_encoding(data) is not None
                ),
                provider=provider_id,
            )
            payload = outcome.to_dict()
            job.stage = "upload"
            job.progress = 0.95
            sync_storage.put_bytes(derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
            # Clear what the publish consumed, as the queued path does (see `consumed_staging`).
            from ada.comms.rest.formats.asset_publish import consumed_staging

            for key in consumed_staging(staged, dry_run=dry_run):
                try:
                    asyncio.run_coroutine_threadsafe(storage.delete(scope, key), loop).result()
                except Exception:  # noqa: BLE001 — a key left staged is listed as such, not lost
                    logger.warning("local asset publish %s could not clear staged %s", job.job_id, key)
            job.result = payload
            job.status = STATUS_DONE
            job.stage = "done"
            job.progress = 1.0
        except Exception as exc:  # noqa: BLE001 — the publish's failure is data, not ours
            if job.status != STATUS_RUNNING:
                return
            logger.exception("local asset publish %s (%s) failed", job.job_id, provider_id)
            job.status = STATUS_ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            job.stage = "error"
            logger.debug("local asset publish traceback:\n%s", traceback.format_exc())

    threading.Thread(target=_run, name=f"local-asset-publish-{provider_id}", daemon=True).start()
    return job


def start_clash_check(
    *,
    source_key: str,
    options: dict[str, Any],
    derived_key: str,
    storage: Any,
    scope: Any,
) -> LocalJob:
    """Run a ``clash_check`` in a thread -- the queue-less half of Decision 10's identification
    surface. Same argument as ``start_asset_build``: a single-node viewer with a model open in its
    own scope should be able to check it for joints without a cluster, and it needs no capability
    pool to do that -- identifying and typing joints wants no kernel this process does not already
    have (the worker-side handler's own comment: "nothing here needs a capability pool")."""
    from ada.cadit.ifc.read.native_members import load_members_or_model
    from ada.clash import ClashOptions, run_clash_check
    from ada.clash.builtin_specs import register_builtin_specs
    from ada.comms.rest.converters.ada_load import _load_with_ada
    from ada.core.file_system import new_temp_path

    loop = asyncio.get_running_loop()

    from ada.comms.rest.worker import _SyncStorageFacade

    sync_storage = _SyncStorageFacade(storage, scope, loop)

    job = LocalJob(
        job_id=f"local-{uuid.uuid4().hex[:16]}",
        plugin_id="clash_check",
        scope_kind=getattr(scope, "kind", "shared"),
        scope_id=getattr(scope, "id", None),
        derived_key=derived_key,
    )
    registry.add(job)

    def _run() -> None:
        tmp = new_temp_path(suffix=pathlib.PurePosixPath(source_key).suffix or None)
        try:
            job.stage, job.progress = "loading", 0.15
            sync_storage.fetch_to_path(source_key, tmp)
            # For an IFC this reads MEMBERS natively -- no ifcopenshell, no geometry -- which is
            # the whole of what a check needs; anything else falls back to the full reader.
            model = load_members_or_model(tmp, tmp.suffix.lower(), _load_with_ada)
            with open(tmp, "rb") as fh:  # chunked: a source can be GBs
                source_sha256 = hashlib.file_digest(fh, "sha256").hexdigest()
            register_builtin_specs()
            clash_options = ClashOptions.from_dict(options)
            if job.status != STATUS_RUNNING:
                return
            job.stage, job.progress = "clash", 0.55
            # No live pool exists behind this transport (`advertised_specs` always answers
            # {} -- see LocalJobTransport below), so `capability_of` is left at its default:
            # every non-builtin spec that happens to be registered here reports capability
            # None, which the panel already reads as "registered, served by nobody right
            # now" -- the honest answer for a queue-less viewer.
            result = run_clash_check(
                model,
                source_key=source_key,
                options=clash_options,
                source_sha256=source_sha256,
            )
            if job.status != STATUS_RUNNING:
                return
            job.stage, job.progress = "upload", 0.95
            payload = result.to_dict()
            sync_storage.put_bytes(derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
            job.result = payload
            job.status, job.stage, job.progress = STATUS_DONE, "done", 1.0
        except Exception as exc:  # noqa: BLE001 — the check's failure is data, not ours
            if job.status != STATUS_RUNNING:
                return
            if job.cancel_event.is_set():
                job.status, job.stage = STATUS_CANCELLED, "cancelled"
                return
            logger.exception("local clash_check %s failed", job.job_id)
            job.status = STATUS_ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            job.stage = "error"
            logger.debug("local clash_check traceback:\n%s", traceback.format_exc())
        finally:
            tmp.unlink(missing_ok=True)

    threading.Thread(target=_run, name="local-clash-check", daemon=True).start()
    return job


def start_clash_check_asset(
    *,
    collection: str,
    subject: str,
    revision: "str | None",
    node: "str | None",
    options: dict[str, Any],
    derived_key: str,
    storage: Any,
    scope: Any,
) -> LocalJob:
    """Run a clash check over a PUBLISHED NODE in a thread -- the queue-less twin of
    ``start_clash_check``.

    No temp file and no extension: the model comes from the provider that owns the format, which
    reads whatever blobs it needs through the same storage facade. A single-node viewer can
    therefore check a published export it has no reader for, provided the provider's package is
    installed here -- and when it is not, the error says so by name rather than answering "no
    joints".
    """
    from ada.clash import ClashOptions
    from ada.clash.from_asset import clash_check_from_asset_node
    from ada.comms.rest.worker import _SyncStorageFacade

    loop = asyncio.get_running_loop()
    sync_storage = _SyncStorageFacade(storage, scope, loop)

    job = LocalJob(
        job_id=f"local-{uuid.uuid4().hex[:16]}",
        plugin_id="clash_check",
        scope_kind=getattr(scope, "kind", "shared"),
        scope_id=getattr(scope, "id", None),
        derived_key=derived_key,
    )
    registry.add(job)

    def _run() -> None:
        try:
            job.stage, job.progress = "clash", 0.4
            clash_options = ClashOptions.from_dict(options)
            payload = clash_check_from_asset_node(
                collection=collection,
                subject=subject,
                storage=sync_storage,
                revision=revision,
                node=node,
                options=clash_options,
            )
            if job.status != STATUS_RUNNING:
                return
            job.stage, job.progress = "upload", 0.95
            sync_storage.put_bytes(derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
            job.result = payload
            job.status, job.stage, job.progress = STATUS_DONE, "done", 1.0
        except Exception as exc:  # noqa: BLE001 — the check's failure is data, not ours
            if job.status != STATUS_RUNNING:
                return
            if job.cancel_event.is_set():
                job.status, job.stage = STATUS_CANCELLED, "cancelled"
                return
            logger.exception("local clash_check_asset %s failed", job.job_id)
            job.status = STATUS_ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            job.stage = "error"
            logger.debug("local clash_check_asset traceback:\n%s", traceback.format_exc())

    threading.Thread(target=_run, name="local-clash-check-asset", daemon=True).start()
    return job


def start_clash_check_group(
    *,
    group: dict[str, Any],
    token: str,
    options: dict[str, Any],
    derived_key: str,
    storage: Any,
    scope: Any,
) -> LocalJob:
    """Run a clash check over a named GROUP in a thread -- the queue-less twin of the worker's
    ``clash_check_group`` kind, through the same ``ada.clash.group_model.clash_check_group``.

    Every member's source is read here: files with this process's readers, published nodes with
    their providers' concepts. A member whose provider is not installed is left out with a warning
    naming it, the same as on a worker that cannot serve it.
    """
    from ada.clash import ClashOptions
    from ada.clash.group_model import clash_check_group
    from ada.comms.rest.worker import _SyncStorageFacade

    sync_storage = _SyncStorageFacade(storage, scope, asyncio.get_running_loop())

    job = LocalJob(
        job_id=f"local-{uuid.uuid4().hex[:16]}",
        plugin_id="clash_check",
        scope_kind=getattr(scope, "kind", "shared"),
        scope_id=getattr(scope, "id", None),
        derived_key=derived_key,
    )
    registry.add(job)

    def _run() -> None:
        try:
            job.stage, job.progress = "clash", 0.4
            payload = clash_check_group(
                group,
                token=token,
                options=ClashOptions.from_dict(options),
                storage=sync_storage,
            )
            if job.status != STATUS_RUNNING:
                return
            job.stage, job.progress = "upload", 0.95
            sync_storage.put_bytes(derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
            job.result = payload
            job.status, job.stage, job.progress = STATUS_DONE, "done", 1.0
        except Exception as exc:  # noqa: BLE001 — the check's failure is data, not ours
            if job.status != STATUS_RUNNING:
                return
            if job.cancel_event.is_set():
                job.status, job.stage = STATUS_CANCELLED, "cancelled"
                return
            logger.exception("local clash_check_group %s failed", job.job_id)
            job.status = STATUS_ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            job.stage = "error"
            logger.debug("local clash_check_group traceback:\n%s", traceback.format_exc())

    threading.Thread(target=_run, name="local-clash-check-group", daemon=True).start()
    return job


def start_clash_detail(
    *,
    result_key: str,
    joint_ids: list[str],
    spec_name: str,
    options: dict[str, Any],
    glb_key: "str | None",
    derived_key: str,
    storage: Any,
    scope: Any,
) -> LocalJob:
    """Run a ``clash_detail`` in a thread. Same argument as ``start_clash_check``: a queue-less
    viewer must be able to hand a group of joints to a BUILT-IN generator without a cluster.

    An out-of-tree spec is never reachable here: ``get_registered`` raises before a job (and its
    thread) is even started when this process never imported it, which on a single-node viewer
    means it was never registered at all -- the caller gets a 501 naming the spec rather than a
    job id that errors two polls later.
    """
    from ada.api.connections.spec import get_registered

    try:
        registered = get_registered(spec_name)
    except KeyError as exc:
        raise LookupError(
            f"spec {spec_name!r} is not registered in this process -- a queue-less viewer can "
            "only detail a joint with a spec it has itself imported (the built-ins always are)"
        ) from exc

    from ada.clash import ClashOptions, ClashResultError, parse_clash_result
    from ada.clash.detail import build_detail, joints_by_id
    from ada.comms.rest.converters.ada_load import _load_with_ada
    from ada.core.file_system import new_temp_path

    loop = asyncio.get_running_loop()

    from ada.comms.rest.worker import _SyncStorageFacade

    sync_storage = _SyncStorageFacade(storage, scope, loop)

    job = LocalJob(
        job_id=f"local-{uuid.uuid4().hex[:16]}",
        plugin_id=spec_name,
        scope_kind=getattr(scope, "kind", "shared"),
        scope_id=getattr(scope, "id", None),
        derived_key=derived_key,
    )
    registry.add(job)

    def _run() -> None:
        try:
            job.stage, job.progress = "fetch", 0.10
            raw = sync_storage.get_bytes(result_key)
            cached = parse_clash_result(raw)
            if not cached.source_key:
                raise ClashResultError(f"{result_key}: clash result carries no source_key")
            clash_options = ClashOptions.from_dict(cached.options)
            job.stage, job.progress = "loading", 0.25
            group = cached.provenance.get("group")
            if isinstance(group, dict):
                # A result over a named group: no one source to fetch, so the model is rebuilt the
                # way the check built it -- which is what makes the ids reproduce.
                from ada.clash.group_model import build_group_model

                model = build_group_model(group, storage=sync_storage).model
            else:
                tmp = new_temp_path(suffix=pathlib.PurePosixPath(cached.source_key).suffix or None)
                try:
                    sync_storage.fetch_to_path(cached.source_key, tmp)
                    model = _load_with_ada(tmp, tmp.suffix.lower())
                finally:
                    tmp.unlink(missing_ok=True)
            by_id = joints_by_id(model, clash_options)
            absent = [jid for jid in joint_ids if jid not in by_id]
            if absent:
                raise ValueError(
                    "joint id(s) not reproducible from this source at these options: "
                    f"{', '.join(absent)} -- the cached result and the source may have drifted apart"
                )
            if job.status != STATUS_RUNNING:
                return

            job.stage, job.progress = "detail", 0.55
            # The worker's loop, not a copy of it: the two engines must not differ in what a
            # builder receives (see `ada.clash.detail`).
            glb_bytes, stats = build_detail(
                registered, spec_name=spec_name, joint_ids=joint_ids, by_id=by_id, gen_options=options
            )

            if job.status != STATUS_RUNNING:
                return
            job.stage, job.progress = "upload", 0.9
            if glb_key:
                sync_storage.put_bytes(glb_key, glb_bytes, content_encoding="gzip")
            payload = stats
            sync_storage.put_bytes(derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
            job.result = payload
            job.status, job.stage, job.progress = STATUS_DONE, "done", 1.0
        except Exception as exc:  # noqa: BLE001 — the detail's failure is data, not ours
            if job.status != STATUS_RUNNING:
                return
            if job.cancel_event.is_set():
                job.status, job.stage = STATUS_CANCELLED, "cancelled"
                return
            logger.exception("local clash_detail %s (%s) failed", job.job_id, spec_name)
            job.status = STATUS_ERROR
            job.error = f"{type(exc).__name__}: {exc}"
            job.stage = "error"
            logger.debug("local clash_detail traceback:\n%s", traceback.format_exc())

    threading.Thread(target=_run, name=f"local-clash-detail-{spec_name}", daemon=True).start()
    return job


def start_export_selection(
    *,
    source_key: "str | None",
    options: dict[str, Any],
    derived_key: str,
    storage: Any,
    scope: Any,
) -> LocalJob:
    """Write a selection as STEP/IFC in a thread -- the queue-less twin of the worker's
    ``export_selection`` / ``export_selection_asset`` kinds (``formats/export_selection.py``).

    ``source_key`` names a file this process reads; ``None`` means ``options`` names a published
    node, whose provider reads its own format here -- and says so by name when it is not
    installed. Either way the same leaf functions the worker calls do the work, so the file a
    single-node viewer hands out is the one a cluster would.
    """
    from ada.comms.rest.selection_export import (
        SELECTION_EXPORT_FORMATS,
        SelectionExportError,
        export_asset_selection,
        export_source_selection,
    )
    from ada.comms.rest.worker import _SyncStorageFacade
    from ada.core.file_system import new_temp_path

    fmt = str(options.get("format") or "")
    if fmt not in SELECTION_EXPORT_FORMATS:
        raise LookupError(f"unsupported export format {fmt!r} (expected one of {sorted(SELECTION_EXPORT_FORMATS)})")

    sync_storage = _SyncStorageFacade(storage, scope, asyncio.get_running_loop())

    job = LocalJob(
        job_id=f"local-{uuid.uuid4().hex[:16]}",
        plugin_id="export_selection",
        scope_kind=getattr(scope, "kind", "shared"),
        scope_id=getattr(scope, "id", None),
        derived_key=derived_key,
    )
    registry.add(job)

    def _run() -> None:
        out_path = new_temp_path(suffix=SELECTION_EXPORT_FORMATS[fmt])
        src = new_temp_path(suffix=pathlib.PurePosixPath(source_key).suffix or None) if source_key else None
        try:
            job.stage, job.progress = "export", 0.3
            element = options.get("element") or None
            path = options.get("path") or ()
            if source_key is not None:
                sync_storage.fetch_to_path(source_key, src)
                export_source_selection(src, fmt, out_path, element=element, path=path)
            else:
                export_asset_selection(
                    collection=str(options.get("collection") or ""),
                    subject=str(options.get("subject") or ""),
                    storage=sync_storage,
                    fmt=fmt,
                    out_path=out_path,
                    revision=options.get("revision") or None,
                    node=options.get("node") or None,
                    element=element,
                    path=path,
                )
            if job.status != STATUS_RUNNING:
                return
            job.stage, job.progress = "upload", 0.9
            # Identity, not gzip: the blob GET must hand the browser a file it can save as is.
            # Streamed from disk, never read whole.
            sync_storage.put_path(derived_key, out_path)
            job.status, job.stage, job.progress = STATUS_DONE, "done", 1.0
        except Exception as exc:  # noqa: BLE001 — the export's failure is data, not ours
            if job.status != STATUS_RUNNING:
                return
            if job.cancel_event.is_set():
                job.status, job.stage = STATUS_CANCELLED, "cancelled"
                return
            job.status = STATUS_ERROR
            job.stage = "error"
            if isinstance(exc, SelectionExportError):
                # The selection's own problem, worded for the user who made it.
                job.error = str(exc)
                return
            logger.exception("local export_selection %s failed", job.job_id)
            job.error = f"{type(exc).__name__}: {exc}"
            logger.debug("local export_selection traceback:\n%s", traceback.format_exc())
        finally:
            out_path.unlink(missing_ok=True)
            if src is not None:
                src.unlink(missing_ok=True)

    threading.Thread(target=_run, name="local-export-selection", daemon=True).start()
    return job
