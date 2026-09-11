"""One contract for "run this job", whether or not a queue is configured.

THE PROBLEM THIS SOLVES. The API had two ways to run work — put it on NATS and
let a capability worker pick it up, or (for plugin jobs, see :mod:`local_jobs`)
run it here in a thread — and no name for the choice between them. So every
route that wanted to run something asked ``queue.enabled`` itself and wrote its
own answer for "and if not?". That came to ~45 reads of one flag and a dozen
hand-spelled variants of the same 503, each one a place where the two paths
could drift apart without anything noticing: a route that forgot the in-process
branch simply 503'd on a laptop, and a route that forgot the gate raised
:class:`~.queue.QueueDisabled` out of a handler as a bare 500.

THE SHAPE. A :class:`JobTransport` is "the thing that runs jobs here". There are
two, chosen once at app build time and carried on ``RestContext.jobs``:

* :class:`QueueJobTransport` — a NATS-backed pool. Runs every job kind.
* :class:`LocalJobTransport` — no queue. Runs PLUGIN jobs in-process and
  reports every other kind as unavailable, because no local path exists for
  them (a conversion needs a worker's CAD stack; a procedural build needs the
  engine's pool). That asymmetry is the honest one: it is a statement about
  what a single-node viewer can actually do, not about which branch someone
  remembered to write.

Three things a route needs, and one call each:

* ``supports(feature)`` / ``require(feature)`` / ``unavailable(feature)`` —
  the gate. ``require`` is ``supports`` plus ``unavailable``; ``unavailable``
  raises the 503 whose detail text this module owns, so the message a client
  sees for "no queue here" is written once instead of per route.
* ``submit`` / ``status`` / ``inprocess`` / ``cancel`` — the job itself.
* ``capabilities`` / ``advertised_specs`` / ``local_specs`` /
  ``worker_image_tag`` — what a route can truthfully say is available.

WHAT IS DELIBERATELY NOT HERE. Anything that is not "run a job": the audit
rows, the KV compression-sweep state, the schedule tables. Those need a queue
too, but they need it as *storage*, and folding them in would make this the
place where "is NATS configured" is asked rather than the place where "can this
job run" is answered.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, NoReturn, Protocol, runtime_checkable

from fastapi import HTTPException

from . import local_jobs
from .queue import JobQueue
from .scope import Scope
from .storage import Storage

logger = logging.getLogger(__name__)

#: A capability a transport either has or has not. Every one of these except
#: ``plugin_jobs`` needs a worker pool, which is the whole content of the
#: distinction: ``plugin_jobs`` is the one kind of work the API process can do
#: itself (:mod:`local_jobs`).
TransportFeature = Literal[
    "bake",
    "bbox_inference",
    "component_build",
    "conversion",
    "job_status_report",
    "plugin_jobs",
    "procedural_build",
    "procedural_export",
    "procedural_import",
    "procedural_relocations",
    "result_meta",
    "utilities",
    "worker_registry",
]

#: The 503 detail text for each feature, VERBATIM as the routes used to spell
#: it inline. These strings are API surface — clients and tests read them — so
#: they are pinned here rather than generated from the feature name. Changing
#: one changes what a user sees; adding a feature means adding its message.
FEATURE_UNAVAILABLE_DETAIL: dict[str, str] = {
    "bake": "bake disabled (no NATS configured)",
    "bbox_inference": "bbox inference disabled (no NATS configured)",
    "component_build": "component build disabled (no NATS configured)",
    "conversion": "conversion disabled (no NATS configured)",
    "job_status_report": "no job queue configured",
    "plugin_jobs": "plugin jobs disabled (no NATS configured)",
    "procedural_build": "procedural build disabled (no NATS configured)",
    "procedural_export": "procedural export disabled (no NATS configured)",
    "procedural_import": "procedural import disabled (no NATS configured)",
    "procedural_relocations": "procedural relocations disabled (no NATS configured)",
    "result_meta": "result-meta disabled (no NATS configured)",
    "utilities": "utilities disabled (no NATS configured)",
    "worker_registry": "worker registry requires a NATS-backed queue",
}

#: What :class:`LocalJobTransport` can run. Everything else it reports as
#: unavailable — see the module docstring on why that is a statement rather
#: than an omission.
LOCAL_FEATURES: frozenset[str] = frozenset({"plugin_jobs"})


@dataclass(frozen=True)
class JobRequest:
    """One job to run, in the terms both transports understand.

    Mirrors :meth:`~.queue.JobQueue.enqueue`'s parameters, plus the two things
    only the in-process path needs (``plugin_id``, ``derived_prefix``) and the
    ``feature`` the request belongs to — which is what lets a transport refuse
    it with the right message instead of a generic one.
    """

    source_key: str
    target_format: str
    scope: Scope
    feature: TransportFeature
    #: Plugin jobs only: which plugin, and the options handed opaquely to its
    #: ``job_entrypoint``. Ignored by every other job kind.
    plugin_id: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    derived_prefix: str | None = None
    conversion_options: dict | None = None
    derived_key: str | None = None
    target_capability: str | None = None
    step: int | None = None
    field_name: str | None = None
    force_rebuild: bool = False


@dataclass(frozen=True)
class SubmittedJob:
    """What came back from a submit. ``job_id`` is the contract — every caller
    needs it and both transports mint one. The rest is what the queue recorded
    about the job, carried so a route can answer with the shape it always did
    (several return ``asdict(job)`` wholesale, which is ``payload``)."""

    job_id: str
    derived_key: str
    status: str
    stage: str
    progress: float
    target_capability: str | None
    payload: dict[str, Any]


#: Called after a job is durable and BEFORE anything can start running it.
#: See :meth:`JobTransport.submit`.
BeforeDispatch = Callable[["SubmittedJob"], Awaitable[None]]


@dataclass(frozen=True)
class JobSnapshot:
    """A job's current state, from whichever transport is running it.

    ``scope`` is the job's RECORDED scope, which is what a caller has to be
    able to reach before being shown the job — the id alone carries no
    authorization. ``local`` says which path produced it; the cancel route
    needs that because the two paths cancel for real in different ways.
    """

    job_id: str
    status: str
    scope: Scope
    payload: dict[str, Any]
    local: bool


@runtime_checkable
class JobTransport(Protocol):
    """The contract. See the module docstring."""

    #: Which implementation this is. Prefer ``supports()`` over reading it —
    #: it exists for logging and for the ``/config`` flags that genuinely
    #: report the deployment shape rather than gate on it.
    kind: Literal["queue", "local"]

    def supports(self, feature: TransportFeature) -> bool:
        """Whether this transport can run ``feature``'s work."""

    def unavailable(self, feature: TransportFeature) -> NoReturn:
        """Raise the 503 for a feature this transport cannot run."""

    def require(self, feature: TransportFeature) -> None:
        """``supports`` or raise — the one-call gate."""

    async def submit(self, req: JobRequest, *, before_dispatch: BeforeDispatch | None = None) -> SubmittedJob:
        """Run (or enqueue) ``req``. Raises the feature's 503 if unsupported.

        ``before_dispatch`` is awaited in the window between the job being
        DURABLE and being VISIBLE to anything that would run it. That window
        is where a caller writes its audit row: the worker's own audit writes
        are bare ``UPDATE ... WHERE job_id``, so a job the worker finishes in
        milliseconds can outrun the API's INSERT and strand the row at
        ``queued`` for ever (see :meth:`~.queue.JobQueue.publish`).

        A transport with NO dispatch step does not call it, and that is the
        contract rather than an oversight: the in-process job is already
        running by the time ``submit`` returns, so there is no window to
        sequence against -- and nothing in that deployment would move such a
        row off ``queued`` afterwards either, since the status-report route
        needs the queue it does not have.
        """

    def inprocess(self, job_id: str) -> JobSnapshot | None:
        """The IN-PROCESS job with this id, if this transport runs any.

        Synchronous and free: a queue transport runs nothing in-process and
        always answers None without touching the network. Routes check this
        before the queue so one frontend polling loop serves both paths.
        """

    async def status(self, job_id: str) -> JobSnapshot | None:
        """This job's current state, or None if the transport has no record."""

    async def cancel(self, job_id: str) -> bool:
        """Ask the transport to stop a job. Returns whether it took effect."""

    async def capabilities(self) -> list[dict]:
        """The worker-registry snapshot rows (empty without a pool)."""

    async def advertised_specs(self, spec_field: str, fallback_field: str | None = None) -> dict[str, dict]:
        """Catalog-shaped specs advertised by live workers, keyed by slug."""

    def local_specs(self) -> dict[str, dict] | None:
        """Specs registered in THIS process, or None when a pool is what
        decides what is online. See :func:`.plugin_registry.locally_registered_specs`."""

    def worker_image_tag(self) -> str | None:
        """The worker image tag the pool published, or None without one."""


class _BaseTransport:
    """The half of the contract that is the same either way."""

    kind: Literal["queue", "local"]
    _features: frozenset[str]

    def supports(self, feature: TransportFeature) -> bool:
        return feature in self._features

    def unavailable(self, feature: TransportFeature) -> NoReturn:
        # KeyError here would be a programming error (a feature with no
        # message), and it is better as a 500 naming the feature than as a 503
        # whose detail is the word "None".
        raise HTTPException(status_code=503, detail=FEATURE_UNAVAILABLE_DETAIL[feature])

    def require(self, feature: TransportFeature) -> None:
        if not self.supports(feature):
            self.unavailable(feature)


class QueueJobTransport(_BaseTransport):
    """Jobs go to a worker pool over NATS. Everything is supported."""

    kind: Literal["queue", "local"] = "queue"
    _features = frozenset(FEATURE_UNAVAILABLE_DETAIL)

    def __init__(self, queue: JobQueue, worker_registry: dict) -> None:
        self._queue = queue
        self._registry = worker_registry

    @property
    def queue(self) -> JobQueue:
        """The underlying queue, for the operations that are queue-only by
        nature (the KV meta keyspace, the compression-sweep state, the
        two-phase publish). A route reaching for this is saying "this is not a
        job", which is the honest thing to say about those."""
        return self._queue

    async def submit(self, req: JobRequest, *, before_dispatch: BeforeDispatch | None = None) -> SubmittedJob:
        # The publish is held back ONLY when there is a hook to run in the
        # gap. A caller with nothing to sequence takes the single-call form,
        # so the common path stays one round trip and one code path in the
        # queue rather than a split that exists for someone else's benefit.
        job = await self._queue.enqueue(
            req.source_key,
            # By keyword, not position: `enqueue` accepts it either way, and the
            # keyword form is what the call sites this replaced used.
            target_format=req.target_format,
            scope_kind=req.scope.kind,
            scope_id=req.scope.id,
            step=req.step,
            field=req.field_name,
            conversion_options=req.conversion_options,
            derived_key=req.derived_key,
            target_capability=req.target_capability,
            force_rebuild=req.force_rebuild,
            publish=before_dispatch is None,
        )
        submitted = _submitted_from_job(job)
        if before_dispatch is not None:
            await before_dispatch(submitted)
            await self._queue.publish(job)
        return submitted

    def inprocess(self, job_id: str) -> JobSnapshot | None:
        # Nothing runs in this process behind a queue: `local_jobs` is only
        # ever written by LocalJobTransport, and answering from it here would
        # be a lookup that can only ever miss.
        return None

    async def status(self, job_id: str) -> JobSnapshot | None:
        job = await self._queue.get(job_id)
        if job is None:
            return None
        scope = (
            Scope.shared()
            if job.scope_kind == "shared"
            else Scope(kind=job.scope_kind, id=job.scope_id)  # type: ignore[arg-type]
        )
        return JobSnapshot(job_id=job.job_id, status=job.status, scope=scope, payload=asdict(job), local=False)

    async def cancel(self, job_id: str) -> bool:
        """Nudge the KV record so an active poll sees the new status.

        Best-effort and decorative, exactly as the cancel route always treated
        it: the worker is not notified, so a bake mid-run runs to completion,
        and its next progress tick overwrites this. The audit row is the
        source of truth for what the user is shown.
        """
        try:
            await self._queue.update(job_id, status="cancelled", error="cancelled by user")
        except Exception:
            return False
        return True

    async def capabilities(self) -> list[dict]:
        return list(self._registry["workers"])

    async def advertised_specs(self, spec_field: str, fallback_field: str | None = None) -> dict[str, dict]:
        # Imported here, not at module scope: routes/deps.py annotates
        # RestContext with JobTransport, so the module-level dependency has to
        # run the other way.
        from .routes.deps import live_worker_specs

        return await live_worker_specs(self._queue, spec_field, fallback_field)

    def local_specs(self) -> dict[str, dict] | None:
        # Behind a queue a job goes to a worker, so a spec this API process
        # happens to have imported says nothing about whether one is up.
        return None

    def worker_image_tag(self) -> str | None:
        return self._registry["image_tag"]


class LocalJobTransport(_BaseTransport):
    """No queue: plugin jobs run HERE, everything else is unavailable.

    The storage handed in is the one :mod:`local_jobs` gives the plugin
    through the same synchronous facade a worker would — the plugin cannot
    tell which transport it is running under, which is the point.
    """

    kind: Literal["queue", "local"] = "local"
    _features = LOCAL_FEATURES

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def submit(self, req: JobRequest, *, before_dispatch: BeforeDispatch | None = None) -> SubmittedJob:
        # `before_dispatch` is deliberately never called -- see the Protocol.
        if not self.supports(req.feature):
            self.unavailable(req.feature)
        if not req.plugin_id:
            raise HTTPException(status_code=500, detail="a local job needs a plugin_id")
        try:
            job = local_jobs.start_plugin_job(
                plugin_id=req.plugin_id,
                options=req.options,
                derived_prefix=req.derived_prefix,
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
            )
        except LookupError as exc:
            # A plugin this process cannot import is a different failure from a
            # deployment that cannot run plugin jobs at all — 501, not 503, and
            # the message names what to fix.
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        payload = job.as_json()
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=payload,
        )

    def inprocess(self, job_id: str) -> JobSnapshot | None:
        job = local_jobs.registry.get(job_id)
        if job is None:
            return None
        return JobSnapshot(
            job_id=job.job_id,
            status=job.status,
            scope=Scope(kind=job.scope_kind, id=job.scope_id),  # type: ignore[arg-type]
            payload=job.as_json(),
            local=True,
        )

    async def status(self, job_id: str) -> JobSnapshot | None:
        return self.inprocess(job_id)

    async def cancel(self, job_id: str) -> bool:
        """Cancel for real: the job's ``cancel_event`` is the same object its
        entrypoint was handed, so a cooperative plugin stops between units of
        work."""
        return local_jobs.registry.cancel(job_id)

    async def capabilities(self) -> list[dict]:
        return []

    async def advertised_specs(self, spec_field: str, fallback_field: str | None = None) -> dict[str, dict]:
        # There is no pool to advertise anything. What this process registered
        # is reported through `local_specs` instead — it is online by
        # definition, being the thing that would run the job.
        return {}

    def local_specs(self) -> dict[str, dict] | None:
        from .plugin_registry import locally_registered_specs

        return locally_registered_specs()

    def worker_image_tag(self) -> str | None:
        return None


def _submitted_from_job(job) -> SubmittedJob:
    return SubmittedJob(
        job_id=job.job_id,
        derived_key=job.derived_key,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        target_capability=job.target_capability,
        payload=asdict(job),
    )


def build_transport(queue: JobQueue, storage: Storage, worker_registry: dict) -> JobTransport:
    """The one place the choice is made. Called once at app build time, so no
    request ever has to ask which shape the deployment is."""
    if queue.enabled:
        return QueueJobTransport(queue, worker_registry)
    logger.info("no job queue configured — plugin jobs will run in-process (see ada.comms.rest.local_jobs)")
    return LocalJobTransport(storage)
