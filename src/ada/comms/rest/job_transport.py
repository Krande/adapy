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
* :class:`LocalJobTransport` — no queue. Runs what this process can run
  itself: plugin jobs and the lighter kinds on a thread (:mod:`local_jobs`), and
  every worker kind whose handler is here -- conversions, FEA bakes, utilities,
  component builds -- in a child process of this interpreter, through the
  worker's own pipeline (:mod:`local_runner`). A local viewer is started from an
  environment that has the same readers and plugins a worker has, so it is the
  worker. What it reports as unavailable is what needs something it does not
  have: a database a job writes back to, a pool a job is chained onto, or a
  sweep that measures worker pools.

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
import pathlib
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
    "asset_build",
    "asset_publish",
    "audit_conversion",
    "bake",
    "bbox_inference",
    "clash_check",
    "clash_detail",
    "component_build",
    "conversion",
    "export_search",
    "export_selection",
    "geometry_compare",
    "job_status_report",
    "plugin_jobs",
    "procedural_build",
    "procedural_export",
    "procedural_import",
    "procedural_relocations",
    "result_meta",
    "simulation",
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
    # An admin audit sweep's conversions: they measure worker pools and write their results to
    # audit rows, so they need both. Worded as the conversion gate the routes always answered.
    "audit_conversion": "conversion disabled (no NATS configured)",
    "job_status_report": "no job queue configured",
    "asset_build": "asset builds disabled (no NATS configured)",
    "asset_publish": "asset publishing disabled (no NATS configured)",
    "clash_check": "clash check disabled (no NATS configured)",
    "clash_detail": "clash detail disabled (no NATS configured)",
    "export_search": "search-result export disabled (no NATS configured)",
    "export_selection": "selection export disabled (no NATS configured)",
    "geometry_compare": "geometry comparison disabled (no NATS configured)",
    "plugin_jobs": "plugin jobs disabled (no NATS configured)",
    "procedural_build": "procedural build disabled (no NATS configured)",
    "procedural_export": "procedural export disabled (no NATS configured)",
    "procedural_import": "procedural import disabled (no NATS configured)",
    "procedural_relocations": "procedural relocations disabled (no NATS configured)",
    "result_meta": "result-meta disabled (no NATS configured)",
    "simulation": "simulations disabled (no NATS configured)",
    "utilities": "utilities disabled (no NATS configured)",
    "worker_registry": "worker registry requires a NATS-backed queue",
}

#: What :class:`LocalJobTransport` can run. Everything else it reports as
#: unavailable — see the module docstring on why that is a statement rather
#: than an omission. ``clash_check``/``clash_detail`` joined for the same
#: reason ``asset_build`` did: identifying and typing joints (and detailing a
#: BUILT-IN spec) wants no kernel this process does not already have, so a 503
#: there would make Decision 10's Clashes tab a cluster-only feature.
#: ``export_selection`` for the same reason: writing a selected part of a model this process can
#: already read needs nothing a cluster adds, and "Download as" is a button on every selection.
#: ``simulation`` like ``plugin_jobs``: a provider registered in this process runs here, so a
#: desktop install with one simulator installed needs no queue to use it.
#: ``procedural_build`` too: the built-in engines and any engine a tool registers in this
#: process compile with nothing a pool adds, and a local viewer's Build mode is otherwise a
#: document editor that can never show its model (``local_jobs.start_procedural_build``).
#: ``bake`` and ``result_meta`` as well: this process has ada's result readers, and a result
#: deck put on a local viewer (a ``.sin`` from a GeniE/Sestra run) is otherwise a file that
#: Results and Capacity cannot open (``local_jobs.start_fea_job``).
#: ``conversion``, ``utilities`` and ``component_build`` because the environment a local viewer
#: is started from has the readers and plugins a worker has: a file put there converts to GLB (and
#: IFC, STEP, ...) without a queue or a worker image, by the same handler a worker runs, in a
#: child process so a native crash takes the job down and not the viewer
#: (``local_jobs.start_worker_job``). The bakes run there too.
LOCAL_FEATURES: frozenset[str] = frozenset(
    {
        "asset_build",
        "asset_publish",
        "bake",
        "clash_check",
        "clash_detail",
        "component_build",
        "conversion",
        "export_selection",
        "plugin_jobs",
        "procedural_build",
        "result_meta",
        "simulation",
        "utilities",
    }
)

#: The job kind each worker-process feature submits, beyond ``conversion`` (whose kinds are the
#: converter registry's targets). A request naming another kind under one of these features is a
#: route's mistake and refused as the feature being unavailable here.
_WORKER_PROCESS_KINDS: dict[str, frozenset[str]] = {
    "utilities": frozenset({"utility"}),
    "component_build": frozenset({"component_build"}),
}


#: The catalogs a queue-less server advertises from its own process, as the worker
#: heartbeat does (worker/registration.py): field -> (module, spec-list function).
#: Only those that belong to the in-process procedural compile.
_LOCAL_PROCEDURAL_SPECS: dict[str, tuple[str, str]] = {
    "procedural_blueprint_specs": ("ada.topo_model", "procedural_blueprint_specs"),
    "procedural_template_specs": ("ada.topo_model", "procedural_template_specs"),
    "procedural_engine_specs": ("ada.topo_model", "procedural_engine_specs"),
}


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
    #: What an IN-PROCESS run needs that a worker reads for itself, handed over by the
    #: route that already has it. ``procedural_build``: ``{"model_row": ..., "db_pool": ...}``
    #: (a worker reads the row from postgres; a local viewer's model is a storage file the
    #: route just read). ``db_pool`` is the route's pool or None: a server with a database
    #: but no queue compiles against the same catalogs its catalog fingerprint describes.
    #: Never sent to a queue: :class:`QueueJobTransport` ignores it.
    inprocess_inputs: dict[str, Any] | None = None


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

    def local_specs(self) -> list[dict] | None:
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

    def local_specs(self) -> list[dict] | None:
        # Behind a queue a job goes to a worker, so a spec this API process
        # happens to have imported says nothing about whether one is up.
        return None

    def worker_image_tag(self) -> str | None:
        return self._registry["image_tag"]


class LocalJobTransport(_BaseTransport):
    """No queue: what this process can run runs HERE (threads, or a job process for a worker's
    kinds -- see the module docstring); everything else is unavailable.

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
        if req.feature == "asset_build":
            return self._submit_asset_build(req)
        if req.feature == "asset_publish":
            return self._submit_asset_publish(req)
        if req.target_format == "clash_contacts":
            # A convex-contact run (check / annotate) or a detail from one: the same kind behind
            # both features, told apart by its own mode (formats/clash_contacts.py).
            return self._submit_clash_contacts(req)
        if req.feature == "clash_check":
            return self._submit_clash_check(req)
        if req.feature == "clash_detail":
            return self._submit_clash_detail(req)
        if req.feature == "export_selection":
            return self._submit_export_selection(req)
        if req.feature == "simulation":
            return self._submit_simulation(req)
        if req.feature == "procedural_build":
            return self._submit_procedural_build(req)
        if req.feature in ("bake", "result_meta"):
            return self._submit_fea(req)
        if req.feature == "conversion" or req.feature in _WORKER_PROCESS_KINDS:
            return self._submit_worker_job(req)
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

    def _submit_procedural_build(self, req: JobRequest) -> SubmittedJob:
        """A procedural compile in this process. Only the single-stage build: an EXTERNAL
        detailing engine is a chained job on its own capability pool, which a local viewer
        does not have (``resolve_detailing_engine`` finds no advertised spec without one)."""
        if req.target_format != "procedural_build":
            self.unavailable(req.feature)
        row = (req.inprocess_inputs or {}).get("model_row")
        if not isinstance(row, dict):
            raise HTTPException(status_code=500, detail="an in-process procedural build needs the model row")
        job = local_jobs.start_procedural_build(
            model_row=row,
            source_key=req.source_key,
            derived_key=req.derived_key or "",
            conversion_options=req.conversion_options or {},
            force_rebuild=req.force_rebuild,
            storage=self._storage,
            scope=req.scope,
            db_pool=(req.inprocess_inputs or {}).get("db_pool"),
        )
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=job.as_json(),
        )

    def _submit_fea(self, req: JobRequest) -> SubmittedJob:
        """A FEA bake, result inventory, or lazy combination / envelope, in a job process."""
        if req.target_format not in local_jobs.LOCAL_FEA_KINDS:
            self.unavailable(req.feature)
        try:
            job = local_jobs.start_fea_job(
                target_format=req.target_format,
                source_key=req.source_key,
                derived_key=req.derived_key or "",
                step=req.step,
                field_name=req.field_name,
                force_rebuild=req.force_rebuild,
                storage=self._storage,
                scope=req.scope,
            )
        except LookupError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return _submitted_from_local(job)

    def _submit_worker_job(self, req: JobRequest) -> SubmittedJob:
        """A worker's job kind -- a conversion, a utility, a component build -- in a child
        process of this interpreter, through the worker's pipeline (``local_jobs.start_worker_job``).

        501, not 503, when this process cannot: the deployment runs these, this environment
        lacks the converter (or the handler) for this one. The message names what is missing.
        """
        if req.feature == "conversion":
            targets = local_conversion_targets(req.source_key)
            if req.target_format not in targets:
                ext = pathlib.PurePosixPath(req.source_key).suffix.lower() or req.source_key
                raise HTTPException(
                    status_code=501,
                    detail=(
                        f"this server cannot convert {ext} to {req.target_format!r}: no converter for it is "
                        f"registered in its environment (it can: {', '.join(targets) or 'none'})"
                    ),
                )
        elif req.target_format not in _WORKER_PROCESS_KINDS[req.feature]:
            self.unavailable(req.feature)
        try:
            job = local_jobs.start_worker_job(
                target_format=req.target_format,
                source_key=req.source_key,
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
                step=req.step,
                field_name=req.field_name,
                conversion_options=req.conversion_options,
                force_rebuild=req.force_rebuild,
            )
        except LookupError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return _submitted_from_local(job)

    def _submit_asset_build(self, req: JobRequest) -> SubmittedJob:
        """The build kind's local engine. Its identity travels in
        ``conversion_options`` (core composed it), not in ``options``, which stay the
        provider's opaque dict."""
        from ada.assets.builders import BuildRequest

        opts = req.conversion_options or {}
        try:
            request = BuildRequest(
                provider=opts["provider"],
                collection=opts["collection"],
                subject=opts["subject"],
                revision=opts["revision"],
                node=opts.get("node"),
                fingerprint=opts["fingerprint"],
                hierarchy_source=opts["hierarchy_source"],
            )
        except KeyError as exc:
            raise HTTPException(status_code=500, detail=f"asset build is missing {exc} in conversion_options") from exc
        try:
            job = local_jobs.start_asset_build(
                request=request,
                capability=opts.get("capability") or "",
                options=opts.get("options") or {},
                derived_prefix=opts.get("derived_prefix") or "",
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
                exclude_nodes=opts.get("exclude_nodes") or (),
            )
        except LookupError as exc:
            # No builder HERE for that capability: 501 (this process cannot), not 503 (this
            # deployment cannot) — the message names the capability to install or route to.
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=job.as_json(),
        )

    def _submit_clash_check(self, req: JobRequest) -> SubmittedJob:
        """The check kind's local engine. Its identity travels in ``conversion_options`` (the
        route composed it), matching ``_submit_asset_build``'s argument for why."""
        opts = req.conversion_options or {}
        if opts.get("group"):
            # A named GROUP of members from several sources, checked as one model.
            job = local_jobs.start_clash_check_group(
                group=dict(opts["group"]),
                token=str(opts.get("group_token") or ""),
                options=dict(opts.get("options") or {}),
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
            )
            return SubmittedJob(
                job_id=job.job_id,
                derived_key=job.derived_key,
                status=job.status,
                stage=job.stage,
                progress=job.progress,
                target_capability=None,
                payload=job.as_json(),
            )
        # A published NODE names itself with a subject, not a key -- and its model comes from the
        # provider that owns the format rather than from a file this process can open. Told apart
        # by what the route put in the options, not by the source key's shape: an asset source IS a
        # real key, so sniffing it would be guessing at something already stated.
        if opts.get("collection") and opts.get("subject"):
            job = local_jobs.start_clash_check_asset(
                collection=str(opts["collection"]),
                subject=str(opts["subject"]),
                revision=opts.get("revision") or None,
                node=opts.get("node") or None,
                options=dict(opts.get("options") or {}),
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
            )
            return SubmittedJob(
                job_id=job.job_id,
                derived_key=job.derived_key,
                status=job.status,
                stage=job.stage,
                progress=job.progress,
                target_capability=None,
                payload=job.as_json(),
            )
        source_key = str(opts.get("source_key") or req.source_key)
        job = local_jobs.start_clash_check(
            source_key=source_key,
            options=dict(opts.get("options") or {}),
            derived_key=req.derived_key or "",
            storage=self._storage,
            scope=req.scope,
        )
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=job.as_json(),
        )

    def _submit_clash_contacts(self, req: JobRequest) -> SubmittedJob:
        """The ``clash_contacts`` kind's local engine (``local_jobs.start_clash_contacts``)."""
        try:
            job = local_jobs.start_clash_contacts(
                options=dict(req.conversion_options or {}),
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
            )
        except LookupError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=job.as_json(),
        )

    def _submit_clash_detail(self, req: JobRequest) -> SubmittedJob:
        """The detail kind's local engine -- only a BUILT-IN spec can ever be served here (see
        ``local_jobs.start_clash_detail``); an out-of-tree spec has no pool in a queue-less
        deployment for a route to route it to, and ``LookupError`` from the engine becomes the
        501 naming that."""
        opts = req.conversion_options or {}
        try:
            job = local_jobs.start_clash_detail(
                result_key=str(opts["result_key"]),
                joint_ids=[str(j) for j in (opts.get("joint_ids") or [])],
                spec_name=str(opts["spec"]),
                options=dict(opts.get("options") or {}),
                glb_key=opts.get("glb_key"),
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
            )
        except KeyError as exc:
            raise HTTPException(status_code=500, detail=f"clash_detail is missing {exc} in conversion_options") from exc
        except LookupError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=job.as_json(),
        )

    def _submit_export_selection(self, req: JobRequest) -> SubmittedJob:
        """The selection export's local engine. Both ways of naming the model run here -- a file
        this process reads, or a published node whose provider is installed here -- told apart
        by what the route put in the options, as ``_submit_clash_check`` does."""
        opts = dict(req.conversion_options or {})
        if req.target_format == "export_selection_group":
            try:
                job = local_jobs.start_export_selection_group(
                    options=opts, derived_key=req.derived_key or "", storage=self._storage, scope=req.scope
                )
            except LookupError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return SubmittedJob(
                job_id=job.job_id,
                derived_key=job.derived_key,
                status=job.status,
                stage=job.stage,
                progress=job.progress,
                target_capability=None,
                payload=job.as_json(),
            )
        try:
            job = local_jobs.start_export_selection(
                source_key=None if opts.get("collection") else str(opts.get("source_key") or req.source_key),
                options=opts,
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
            )
        except LookupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=job.as_json(),
        )

    def _submit_simulation(self, req: JobRequest) -> SubmittedJob:
        """The ``simulation_run`` kind's local engine (``local_jobs.start_simulation_run``). A
        provider this process has not registered is a 501 naming it, as for a plugin job."""
        opts = req.conversion_options or {}
        try:
            job = local_jobs.start_simulation_run(
                provider_id=str(opts["provider_id"]),
                run_id=str(opts["run_id"]),
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=500, detail=f"simulation_run is missing {exc} in conversion_options"
            ) from exc
        except LookupError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=job.as_json(),
        )

    def _submit_asset_publish(self, req: JobRequest) -> SubmittedJob:
        """The publish kind's local engine -- same argument as the build's: a single-node viewer
        that can stage a file into its own scope must be able to publish it, or `publish` is a
        verb that only exists in a cluster."""
        opts = req.conversion_options or {}
        try:
            job = local_jobs.start_asset_publish(
                provider_id=str(opts.get("provider") or ""),
                staged=dict(opts.get("staged") or {}),
                collection=opts.get("collection"),
                options=dict(opts.get("options") or {}),
                published_by=str(opts.get("published_by_id") or "unknown"),
                published_by_display=opts.get("published_by_display"),
                published_via=str(opts.get("published_via") or "user"),
                dry_run=bool(opts.get("dry_run")),
                replace_existing=bool(opts.get("replace")),
                derived_key=req.derived_key or "",
                storage=self._storage,
                scope=req.scope,
            )
        except LookupError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return SubmittedJob(
            job_id=job.job_id,
            derived_key=job.derived_key,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            target_capability=None,
            payload=job.as_json(),
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
        #
        # Except the procedural catalogs: a queue-less server compiles procedural
        # models itself (`procedural_build` is a local feature), so it IS the worker
        # those catalogs describe, and answers for them exactly as a worker's
        # heartbeat would - the built-ins plus what a preloaded tool registered
        # (its engine's blueprints, its templates). See _LOCAL_PROCEDURAL_SPECS.
        source = _LOCAL_PROCEDURAL_SPECS.get(spec_field)
        if source is None:
            return {}
        import importlib

        module, attr = source
        try:
            specs = getattr(importlib.import_module(module), attr)()
        except ImportError:
            return {}  # a slim API without the modelling stack has none to offer
        except Exception:
            logger.exception("local transport: failed to list %s (non-fatal)", spec_field)
            return {}
        return {
            s["slug"]: dict(s) for s in specs if isinstance(s, dict) and isinstance(s.get("slug"), str) and s["slug"]
        }

    def local_specs(self) -> list[dict] | None:
        from .plugin_registry import locally_registered_specs

        return locally_registered_specs()

    def worker_image_tag(self) -> str | None:
        return None


def _submitted_from_local(job: local_jobs.LocalJob) -> SubmittedJob:
    return SubmittedJob(
        job_id=job.job_id,
        derived_key=job.derived_key,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        target_capability=None,
        payload=job.as_json(),
    )


def local_advertisement() -> dict[str, list]:
    """What a queue-less server can convert and run in its own environment, in the shape a
    worker's heartbeat carries (``source_exts``, ``conversions``, ``utilities``): the very
    computation a worker advertises with (``worker.registration.base_advertisement``), so the
    frontend offers on a local viewer exactly what its job processes will do.

    Computed on each call -- a plugin can register a converter after start-up -- and empty, never
    raising, where the worker's modules are not importable (a slim API).
    """
    try:
        from .worker.registration import base_advertisement

        advert = base_advertisement()
    except Exception:
        logger.exception("local transport: could not evaluate what this process converts (non-fatal)")
        return {"source_exts": [], "conversions": [], "utilities": []}
    return {"source_exts": advert.source_exts, "conversions": advert.conversions, "utilities": advert.utilities}


def local_conversion_targets(source_key: str) -> list[str]:
    """The targets this process converts ``source_key`` to, per :func:`local_advertisement`. A
    bundle (``.zip``) answers for the Abaqus deck it unpacks to, as ``supported_targets_for`` does."""
    from .converters.keys import _BUNDLE_EXTS

    ext = pathlib.PurePosixPath(source_key).suffix.lower()
    if ext in _BUNDLE_EXTS:
        ext = ".inp"
    for row in local_advertisement()["conversions"]:
        if row.get("from") == ext:
            return sorted(str(t) for t in row.get("to") or [])
    return []


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
    logger.info("no job queue configured — jobs run in this environment (see ada.comms.rest.local_jobs)")
    return LocalJobTransport(storage)
