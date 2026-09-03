"""NATS JetStream-backed conversion job queue.

Two NATS primitives are used together:

* a JetStream **stream** with `WORK_QUEUE` retention — work-queue
  semantics mean a message is removed once acked, so each job is
  processed exactly once across any number of workers.
* a JetStream **KV bucket** — point-in-time status lookups for the
  frontend to poll. The work-queue stream alone can't answer
  "what's the state of job X right now?" efficiently.

The queue stream carries only the `job_id` as the message body; the
full Job record (status, progress, error, ...) is stored in KV under
that id and updated as the worker progresses.

Both connect-time stream/bucket creation are idempotent; restarts are
safe.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass

import nats
from nats.js.api import ConsumerConfig, RetentionPolicy, StreamConfig
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyNotFoundError,
    NotFoundError,
)

from ada.config import logger

from .config import QueueConfig
from .converter import derived_key_for
from .nats_ws import install_websocket_close_fix

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_ERROR = "error"


@dataclass
class Job:
    job_id: str
    source_key: str
    derived_key: str
    status: str
    target_format: str = "glb"
    progress: float = 0.0
    stage: str = ""
    error: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    # Scope under which the source/derived blobs live. Defaults to
    # "shared" for backward compat with phase-1 jobs already in flight
    # at the moment of upgrade.
    scope_kind: str = "shared"
    scope_id: str | None = None
    # FEA result selection. None means "let the converter pick a
    # default" — the auto-convert path leaves these unset so the
    # default-rendered GLB lives at the bare derived_key. A picker
    # request sets both, and derived_key is computed off the pair so
    # picked combos cache distinct from the default.
    step: int | None = None
    field: str | None = None
    # Per-conversion overrides for the global app_settings knobs
    # (use_sat_pcurves / skip_shapefix /
    # merge_meshes / profile_conversions). Worker merges these on
    # top of the global settings before forking. Stored as plain
    # str/bool/None so the JSON round-trip through KV stays stable.
    conversion_options: dict | None = None
    # Worker-pool routing (M2 admin audit panel). When set, only
    # workers whose ``ADA_WORKER_CAPABILITIES`` env-derived
    # capability set includes this token will accept the job;
    # everything else NAKs with a small delay so a matching pool
    # has a chance to grab the redelivery. The audit dispatcher
    # stamps this from the run config; regular user-driven
    # ``/convert`` leaves it None so any worker can pick the job
    # up. Honoured by the existing capability gate in
    # ``worker.py:_run`` alongside the extension allowlist.
    target_capability: str | None = None
    # Skip the worker's cached-blob short-circuit and actually
    # re-run the conversion. Set by the audit dispatcher when the
    # operator picks "force rebuild" — used for perf measurement
    # runs where a cache hit defeats the point. Regular convert
    # jobs leave this False so the worker keeps its safety-net
    # short-circuit on NATS redelivery.
    force_rebuild: bool = False

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> "Job":
        # Tolerate older serialized jobs that pre-date scope_kind /
        # scope_id by ignoring unknown fields and supplying defaults.
        data = json.loads(raw.decode("utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class QueueDisabled(RuntimeError):
    """Raised when queue operations are attempted but no NATS URL is configured."""


class MissingTransportDependency(RuntimeError):
    """A connection option needs a package this environment does not have."""


#: Optional packages ``nats-py`` imports lazily, and what each one buys.
#: ``nats-py`` itself declares neither, so an environment can be perfectly
#: healthy and still be unable to do these two things.
_TRANSPORT_EXTRAS = {
    "aiohttp": "WebSocket transport (ws:// and wss:// URLs)",
    "nkeys": "nkey and credentials-file authentication",
}


def _require(package: str, feature: str, setting: str) -> None:
    """Fail early, and in a sentence, when a configured option cannot work.

    ``nats-py`` imports ``aiohttp`` and ``nkeys`` lazily, deep inside connect —
    so without this the operator gets a bare ``ImportError`` (or, for
    websockets, ``Could not import aiohttp transport``) raised from inside a
    library they did not configure, at the bottom of a stack that mentions
    neither the setting they set nor the package to install.

    It matters most for exactly the deployment this exists to serve: a worker
    on a machine somebody assembled by hand, where the environment is not the
    one CI tested. The check is cheap and it runs before any socket is opened.
    """
    import importlib.util

    if importlib.util.find_spec(package) is not None:
        return
    raise MissingTransportDependency(
        f"{setting} is set, which needs {_TRANSPORT_EXTRAS.get(package, package)}, "
        f"but the {package!r} package is not installed in this environment. "
        f"Install it (adapy's viewer-api environment carries it; a hand-built env may not) "
        f"or unset {setting}."
    )


#: Longest a single capability token may be. Generous for a project code or a
#: device name, short enough that a pasted paragraph cannot become a subject.
MAX_CAPABILITY_TOKEN_LEN = 48


def capability_token(value: object) -> str:
    """Normalise ``value`` into one NATS subject token, or ``""``.

    A capability becomes a subject segment (``ada.viewer.jobs.convert.<cap>``)
    and a durable-consumer name suffix, so it may not contain ``.``, ``*``,
    ``>`` or whitespace — a project code with a dot in it would silently create
    a *deeper* subject that no consumer filters on, and the job would sit in the
    stream forever looking merely slow.

    THIS FUNCTION IS THE CONTRACT between the API and the worker. The API
    derives the subject it publishes on and the worker derives the subject it
    subscribes to; if the two normalised differently — one lower-casing, the
    other not — every sharded job would be published to a subject nobody is
    listening on. Both call this.

    Underscore is kept, not rewritten to a dash. It is a legal subject
    character and existing pools are named with it (``fem_solver``), so
    rewriting it would move those workers to a new subject while some job
    producers still published to the old one — a silent delivery failure caused
    by a normaliser meant to prevent exactly that.

    Idempotent: ``capability_token(capability_token(x)) == capability_token(x)``,
    which is what makes it safe to apply at more than one point on the path.

    Returns ``""`` when nothing usable survives, which callers must read as "no
    shard", never as a token.
    """
    text = str(value or "").strip().lower()
    out: list[str] = []
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "-_"):
            out.append(ch)
        elif out and out[-1] != "-":
            # Collapse any run of separators (dots, spaces, slashes) into one
            # dash rather than dropping them: `site-a/2` and `site-a 2` should
            # not both become `site-a2`, which would merge two distinct pools.
            out.append("-")
    return "".join(out).strip("-")[:MAX_CAPABILITY_TOKEN_LEN].strip("-")


def _worker_advertises_engine(w: dict, ext: str, engine: str) -> bool:
    """True if worker registry entry ``w`` lists ``engine`` in its step_glb_pipeline enum for the
    ``ext`` → glb conversion — i.e. that pool can actually run the requested STEP→GLB engine."""
    want = ext.lstrip(".").lower()
    for entry in w.get("conversions") or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("from") or "").strip().lstrip(".").lower() != want:
            continue
        for opt in (entry.get("options") or {}).get("glb") or []:
            if isinstance(opt, dict) and opt.get("name") == "step_glb_pipeline":
                return engine in (opt.get("enum") or [])
    return False


class JobQueue:
    """Connection-managed wrapper around NATS JetStream + KV."""

    def __init__(self, cfg: QueueConfig):
        self._cfg = cfg
        self._nc = None
        self._js = None
        self._kv = None
        # The bucket holding worker registry rows. Aliases ``_kv`` unless
        # ``registry_kv_bucket`` names a different one, so every registry method
        # below has exactly one code path whichever mode the deployment is in.
        self._registry_kv = None

    # --- lifecycle ---------------------------------------------------

    @property
    def registry_bucket(self) -> str:
        """Bucket the worker registry lives in — the jobs bucket unless split out."""
        return self._cfg.registry_kv_bucket or self._cfg.kv_bucket

    @property
    def registry_is_separate(self) -> bool:
        """True when the registry has a bucket of its own.

        The only thing this changes is whether a NATS credential *can* be
        scoped to one worker's own row; nothing about the data differs.
        """
        return bool(self._cfg.registry_kv_bucket) and self._cfg.registry_kv_bucket != self._cfg.kv_bucket

    @property
    def _registry_bucket_handle(self):
        """The KV handle registry rows are written to.

        Derived rather than assigned in the un-split mode: that mode IS the jobs
        bucket, and a second attribute that has to be kept pointing at ``_kv``
        is a second thing that can be forgotten to be set (by a new connect
        path, or by a test that builds a queue by hand).
        """
        if self.registry_is_separate:
            return self._registry_kv
        return self._kv

    @property
    def enabled(self) -> bool:
        return self._cfg.url is not None

    # Default capability tag for jobs with no explicit ``target_capability``.
    # Maps to the per-pool subject suffix the base worker subscribes to,
    # so a user-driven /convert with no pool selection always lands on the
    # base pool. Capability workers only get jobs whose
    # ``target_capability`` matches their tag — NATS subject routing
    # replaces the in-loop NAK gate that previously burned the message's
    # delivery budget when a capability pod pulled a job it couldn't
    # handle.
    DEFAULT_CAPABILITY = "base"

    # Heartbeat-staleness window for treating a registered worker as online.
    # Must match the admin endpoint's threshold (``/api/admin/workers``) so the
    # routing view and the UI view agree on which pools are live.
    WORKER_STALE_AFTER_S = 60.0

    # How long ``connect(manage=False)`` waits for the KV bucket the API
    # is responsible for creating. A non-managing client that starts
    # before the API has nothing to bind to; rather than fail on the
    # first attempt (crash-loop on ordering alone) or wait forever (a
    # worker that looks healthy but is bound to nothing), poll for a
    # window that comfortably covers a rolling restart and then raise
    # with a message that names the real cause.
    _BIND_WAIT_SECONDS = 60.0
    _BIND_POLL_SECONDS = 2.0

    @staticmethod
    def _is_websocket_url(url: str) -> bool:
        """Whether this URL selects nats-py's WebSocket transport.

        Off the URL rather than off a credential, because the URL is what
        actually chooses the transport.
        """
        return url.startswith("ws://") or url.startswith("wss://")

    def _connect_options(self, name: str | None) -> dict:
        """Build the ``nats.connect()`` kwargs for the configured credentials.

        Everything is optional and everything defaults to absent, so a
        server with no accounts block behaves exactly as it did before
        these fields existed.

        Raises :class:`MissingTransportDependency` when the configuration asks
        for something the installed environment cannot do. See :func:`_require`
        for why that check is here rather than left to nats-py.
        """
        # A ws:// or wss:// URL selects nats-py's WebSocket transport, which
        # needs aiohttp. Checked off the URL rather than off a credential
        # because that is what actually chooses the transport — and a
        # WebSocket URL is the shape an off-cluster worker uses when the bus is
        # reached through an HTTPS ingress rather than a raw TCP port.
        url = (self._cfg.url or "").strip().lower()
        if self._is_websocket_url(url):
            _require("aiohttp", "the WebSocket transport", "ADA_VIEWER_NATS_URL")

        opts: dict = {}
        if name:
            # Shows up in `nats server report connections` / monitoring.
            # Worth setting: during an auth rollout the useful question is
            # "which principal is this connection", and an unnamed client
            # answers it with an ip:port.
            opts["name"] = name
        cfg = self._cfg
        if cfg.creds_file:
            _require("nkeys", "a credentials file", "ADA_VIEWER_NATS_CREDS")
            opts["user_credentials"] = cfg.creds_file
        if cfg.nkey_seed_file and cfg.nkey_seed:
            # Both point at the same principal in any sane deployment, so this
            # is redundancy rather than danger — not worth refusing to start
            # over. Deterministic and said out loud beats a silent coin flip:
            # the wrong pick would surface as an auth failure that reads like a
            # network problem.
            logger.warning(
                "queue: both ADA_VIEWER_NATS_NKEY_SEED (file) and "
                "ADA_VIEWER_NATS_NKEY_SEED_VALUE (inline) are set; using the file"
            )
        if cfg.nkey_seed_file:
            _require("nkeys", "nkey authentication", "ADA_VIEWER_NATS_NKEY_SEED")
            opts["nkeys_seed"] = cfg.nkey_seed_file
        elif cfg.nkey_seed:
            # The seed itself rather than a path to it. Secret-injection systems
            # that populate the environment (rather than mounting files) have no
            # way to use the path form, and writing the seed to a temp file just
            # to hand back a path would put it on disk for no reason.
            _require("nkeys", "nkey authentication", "ADA_VIEWER_NATS_NKEY_SEED_VALUE")
            opts["nkeys_seed_str"] = cfg.nkey_seed
        if cfg.user:
            opts["user"] = cfg.user
        if cfg.password:
            opts["password"] = cfg.password
        if cfg.token:
            opts["token"] = cfg.token
        if cfg.tls_ca:
            import ssl

            ctx = ssl.create_default_context()
            ctx.load_verify_locations(cafile=cfg.tls_ca)
            opts["tls"] = ctx
        return opts

    async def connect(self, manage: bool = True, name: str | None = None) -> None:
        """Connect, and when ``manage`` is set, create the stream and bucket.

        ``manage=True`` is the API: it owns the JetStream topology and
        brings it forward on deploy. ``manage=False`` is a worker: it
        only ever *uses* the topology, so its credential needs no
        stream-admin rights. That split is the prerequisite for granting
        workers a narrower permission set than the API — until it existed
        a worker's first act was ``add_stream``, which meant nothing in
        the design distinguished a worker from an administrator.
        """
        if not self.enabled:
            raise QueueDisabled("ADA_VIEWER_NATS_URL not set")
        options = self._connect_options(name)
        if self._is_websocket_url((self._cfg.url or "").strip().lower()):
            # Teach nats-py to treat a server-closed WebSocket as EOF, so it
            # reconnects instead of dying silently inside its read loop. See
            # ada.comms.rest.nats_ws for what breaks without this. Installed
            # here rather than at import so it only touches processes that
            # actually use the transport.
            install_websocket_close_fix()
        self._nc = await nats.connect(self._cfg.url, **options)
        self._js = self._nc.jetstream()

        if not manage:
            await self._bind_existing()
            return

        # Stream — idempotent. Carries both the legacy bare subject
        # (so messages already in flight at the moment of upgrade
        # keep draining) and the new wildcard form
        # ``<subject>.<capability>`` that powers per-pool routing.
        # The wildcard is what every new ``enqueue`` publishes on;
        # the bare subject is kept in the subject list only so the
        # stream accepts in-flight ``convert`` messages already
        # queued by a pre-upgrade replica.
        stream_subjects = [self._cfg.subject, f"{self._cfg.subject}.>"]
        try:
            await self._js.add_stream(
                StreamConfig(
                    name=self._cfg.stream,
                    subjects=stream_subjects,
                    retention=RetentionPolicy.WORK_QUEUE,
                )
            )
        except BadRequestError:
            # Stream exists with a different config; bring it
            # forward to include the wildcard subject. ``update_stream``
            # is idempotent and tolerant of the existing config so
            # repeated calls are safe.
            try:
                await self._js.update_stream(
                    StreamConfig(
                        name=self._cfg.stream,
                        subjects=stream_subjects,
                        retention=RetentionPolicy.WORK_QUEUE,
                    )
                )
            except Exception:
                # Failures here are non-fatal — the stream still
                # works for the old subject; just the new wildcard
                # routing won't activate until manual intervention.
                pass

        # Remove the legacy un-filtered durable consumer if a
        # previous deploy created it. The new per-pool design uses
        # ``<durable>-<capability>`` consumers; leaving the legacy
        # ``<durable>`` consumer alive would just be dead weight on
        # the stream (no one subscribes to it after this deploy).
        # Idempotent: ignore NotFound.
        try:
            await self._js.delete_consumer(self._cfg.stream, self._cfg.durable)
        except Exception:
            pass

        # KV bucket — idempotent.
        try:
            self._kv = await self._js.create_key_value(bucket=self._cfg.kv_bucket, history=1)
        except BadRequestError:
            self._kv = await self._js.key_value(self._cfg.kv_bucket)

        # Registry bucket — also idempotent, and created LAST for the same
        # reason the jobs bucket is: ``_bind_existing`` waits on it, so a bucket
        # that exists has to imply everything created before it exists too.
        if self.registry_is_separate:
            try:
                self._registry_kv = await self._js.create_key_value(bucket=self._cfg.registry_kv_bucket, history=1)
            except BadRequestError:
                self._registry_kv = await self._js.key_value(self._cfg.registry_kv_bucket)

    async def _bind_existing(self) -> None:
        """Bind to the API-created KV bucket without creating anything.

        Waiting on the bucket also covers the stream: ``connect(manage=True)``
        creates the stream first and the bucket last, so a bucket that
        exists implies a stream that exists. Keep that order if you touch
        the managing path — a worker that binds the bucket then finds no
        stream to ``pull_subscribe`` on is a much worse failure than
        waiting a little longer here.
        """
        deadline = time.monotonic() + self._BIND_WAIT_SECONDS
        while True:
            try:
                self._kv = await self._js.key_value(self._cfg.kv_bucket)
                break
            # BucketNotFoundError subclasses NotFoundError; the plain
            # form also covers "the backing KV_<bucket> stream is absent".
            except NotFoundError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"KV bucket {self._cfg.kv_bucket!r} does not exist after "
                        f"{self._BIND_WAIT_SECONDS:.0f}s. It is created by the API on startup — "
                        "either the API has not come up yet, or this client's credentials "
                        "cannot see the bucket."
                    ) from None
                logger.info(
                    "queue: waiting for KV bucket %s to be created by the API",
                    self._cfg.kv_bucket,
                )
                await asyncio.sleep(self._BIND_POLL_SECONDS)

        if not self.registry_is_separate:
            return

        # The registry bucket, on the same terms. Deliberately NOT fatal when it
        # is missing: registration is best-effort everywhere else in this class
        # (``register_worker`` returns quietly when there is no bucket, the
        # admin panel just shows one fewer row), and an API that has not yet
        # been upgraded to create this bucket must not stop a worker from
        # pulling jobs it is otherwise able to serve. A worker that cannot
        # register is a worker missing from a listing; a worker that will not
        # start is an outage.
        deadline = time.monotonic() + self._BIND_WAIT_SECONDS
        while True:
            try:
                self._registry_kv = await self._js.key_value(self._cfg.registry_kv_bucket)
                return
            except NotFoundError:
                if time.monotonic() >= deadline:
                    logger.warning(
                        "queue: registry KV bucket %r does not exist after %.0fs — this worker "
                        "will run but will not appear in the worker registry. It is created by "
                        "the API on startup; either the API has not been upgraded to a version "
                        "that creates it, or this client's credentials cannot see it.",
                        self._cfg.registry_kv_bucket,
                        self._BIND_WAIT_SECONDS,
                    )
                    return
                logger.info(
                    "queue: waiting for registry KV bucket %s to be created by the API",
                    self._cfg.registry_kv_bucket,
                )
                await asyncio.sleep(self._BIND_POLL_SECONDS)

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()
            self._nc = None
            self._js = None
            self._kv = None
            self._registry_kv = None

    async def purge_jobs(self, job_ids) -> int:
        """Delete still-queued work-queue messages whose body (a job_id) is in
        ``job_ids``. WORK_QUEUE retention removes a message on ack, so the stream
        only holds the un-processed backlog — the scan is over the pending jobs,
        not all history. Used to deep-clean a cancelled/deleted audit run's cells
        so the worker never pulls and (partially) processes a doomed conversion.
        Best-effort; returns the count purged."""
        ids = {j for j in (job_ids or []) if j}
        if not ids or self._js is None:
            return 0
        try:
            info = await self._js.stream_info(self._cfg.stream)
        except Exception:
            logger.exception("queue: stream_info failed during purge")
            return 0
        first = info.state.first_seq or 1
        last = info.state.last_seq or 0
        purged = 0
        for seq in range(first, last + 1):
            try:
                msg = await self._js.get_msg(self._cfg.stream, seq)
            except Exception:
                continue  # already acked/deleted — gap in the sequence
            data = getattr(msg, "data", b"") or b""
            try:
                jid = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
            except Exception:
                continue
            if jid in ids:
                try:
                    await self._js.delete_msg(self._cfg.stream, seq)
                    purged += 1
                except Exception:
                    logger.debug("queue: delete_msg failed for seq %s", seq)
        if purged:
            logger.info("queue: purged %d cancelled job message(s) from the stream", purged)
        return purged

    # --- producer side (called from API) -----------------------------

    async def enqueue(
        self,
        source_key: str,
        target_format: str = "glb",
        *,
        scope_kind: str = "shared",
        scope_id: str | None = None,
        step: int | None = None,
        field: str | None = None,
        conversion_options: dict | None = None,
        derived_key: str | None = None,
        target_capability: str | None = None,
        force_rebuild: bool = False,
        publish: bool = True,
    ) -> Job:
        # ``derived_key`` lets callers pin an explicit produced-blob
        # path. The convert flow leaves it None and lets
        # ``derived_key_for`` derive ``_derived/<src>.<fmt>``; the
        # fea_artefacts flow passes the manifest key explicitly because
        # the bake's TARGET_FORMATS has no entry for it (the bake
        # produces a tree of files, not one bytes blob).
        if derived_key is None:
            derived_key = derived_key_for(source_key, target_format, step=step, field=field)
        now = time.time()
        job = Job(
            job_id=uuid.uuid4().hex,
            source_key=source_key,
            derived_key=derived_key,
            status=JOB_STATUS_QUEUED,
            target_format=target_format,
            progress=0.0,
            stage="queued",
            created_at=now,
            updated_at=now,
            scope_kind=scope_kind,
            scope_id=scope_id,
            step=step,
            field=field,
            conversion_options=conversion_options,
            target_capability=target_capability,
            force_rebuild=force_rebuild,
        )
        # Resolve which pool should handle this job. When the caller
        # passes ``target_capability`` explicitly (admin audit form,
        # CI pipeline tagging), honour it. Otherwise look up the
        # source extension in the live worker registry: whichever
        # pool advertises this extension picks it up. Falls back to
        # ``DEFAULT_CAPABILITY`` (= base) when nothing matches —
        # which surfaces as an explicit misroute error at the worker
        # instead of stuck-pending forever, so the operator sees
        # the actual problem (unsupported file type, missing pool).
        if target_capability is None:
            # A STEP→GLB job pinned to a specific engine routes to a pool that ADVERTISES that engine
            # (capability gating makes that truthful); otherwise route by source extension as before.
            requested_engine = None
            if target_format == "glb" and conversion_options:
                requested_engine = conversion_options.get("step_glb_pipeline")
            target_capability = await self._capability_for_ext(source_key, requested_engine)
            # Persist the resolved capability so the worker / UI can
            # show which pool a job was dispatched to without a second
            # registry lookup.
            job.target_capability = target_capability
        # Always persist before publishing so the worker (and the
        # /api/convert/{job_id} status endpoint) can read the job
        # record back from KV. Previously this _put only ran in the
        # auto-routing branch — explicit target_capability callers
        # (component_build) lost the record and the worker saw the
        # job_id from NATS but couldn't look it up.
        await self._put(job)
        # ``publish=False`` hands the caller the persisted Job without letting a
        # worker see it yet, so the caller can write its audit row first.
        if publish:
            await self.publish(job)
        return job

    async def publish(self, job: Job) -> None:
        """Hand an already-persisted job to its pool's subject.

        Split out of :meth:`enqueue` so a caller that ALSO records the job in
        Postgres can insert that row BEFORE any worker can see the message. The
        worker's audit writes are ``UPDATE audit_log ... WHERE job_id = $1``
        (:func:`db.mark_audit_running`, :func:`db.update_audit_by_job`) with no
        upsert — they silently affect zero rows when the row is not there yet.
        A job the worker finishes in tens of milliseconds (a ``plugin_job``, or
        anything hitting the cached-derived-blob short circuit) can outrun the
        API's INSERT, and the row is then created as ``queued`` and stays there
        forever: no message left in the work queue, and the KV entry gone once
        the terminal-status cleanup sweep runs.
        """
        # Normalised HERE rather than at each producer. `target_capability` is
        # set from a dozen places — the plugin route, the audit dispatcher, a
        # detailing spec, a procedural engine lookup, a manifest — and several
        # of them pass a value straight through from a worker's advertisement.
        # The worker derives its subscription subject through the same function,
        # so converging here is what guarantees the two agree no matter which
        # producer built the job. Doing it per-producer would mean the next one
        # added is a silent delivery failure nobody notices.
        cap = capability_token(job.target_capability) or self.DEFAULT_CAPABILITY
        subject = f"{self._cfg.subject}.{cap}"
        await self._js.publish(subject, job.job_id.encode("utf-8"))

    async def _capability_for_ext(self, source_key: str, engine: str | None = None) -> str:
        """Look up the capability tag of the online worker pool that should handle this job.

        Routes to the first online pool whose advertised ``source_exts`` includes the source's suffix
        (a solver's result suffix → its capability pool). When ``engine`` is given (a STEP→GLB job pinned to a specific
        tessellation engine), PREFER a pool that also advertises that engine in its conversion matrix
        — so an ``adacpp-native`` job lands on a pool that actually has adacpp — and fall back to any
        ext-capable pool (the worker's own engine fallback chain then applies) rather than stranding it.

        Falls back to :data:`DEFAULT_CAPABILITY` when no online worker advertises the extension. The
        worker-side misroute guard catches that case and writes an explicit error so the operator sees
        what's wrong instead of a silently-stuck job.
        """
        import pathlib

        ext = pathlib.PurePosixPath(source_key).suffix.lower()
        try:
            workers = await self.list_workers()
        except Exception:
            return self.DEFAULT_CAPABILITY
        # ``list_workers`` returns the raw registry entries, which carry a
        # ``last_heartbeat`` timestamp but NO ``online`` key — that boolean is
        # derived downstream (the admin endpoint annotates it for the UI). So we
        # must compute staleness here too; reading ``w["online"]`` directly would
        # be falsy for EVERY worker and silently route every job to the default
        # pool (the ``.odb`` → base misroute bug). Mirror the admin endpoint's
        # 60s threshold via the shared :data:`WORKER_STALE_AFTER_S`.
        now = time.time()
        ext_cap: str | None = None  # first ext-capable pool — fallback when no pool has the engine
        for w in workers:
            hb = w.get("last_heartbeat")
            if not (isinstance(hb, (int, float)) and (now - hb) <= self.WORKER_STALE_AFTER_S):
                continue
            if not any(isinstance(s, str) and s.strip().lower() == ext for s in (w.get("source_exts") or [])):
                continue
            cap = self.DEFAULT_CAPABILITY
            for c in w.get("capabilities") or []:
                if isinstance(c, str) and c.strip():
                    cap = c.strip().lower()
                    break
            if ext_cap is None:
                ext_cap = cap
            # No engine pinned → the first ext-capable pool wins (unchanged behaviour). Engine pinned →
            # only accept a pool whose matrix advertises that engine for this source.
            if engine is None or _worker_advertises_engine(w, ext, engine):
                return cap
        return ext_cap or self.DEFAULT_CAPABILITY

    async def get(self, job_id: str) -> Job | None:
        try:
            entry = await self._kv.get(job_id)
        except KeyNotFoundError:
            return None
        except BucketNotFoundError:
            return None
        return Job.from_json(entry.value)

    async def update(self, job_id: str, **fields) -> Job | None:
        job = await self.get(job_id)
        if job is None:
            return None
        for k, v in fields.items():
            setattr(job, k, v)
        job.updated_at = time.time()
        await self._put(job)
        return job

    async def _put(self, job: Job) -> None:
        await self._kv.put(job.job_id, job.to_json())

    # Terminal statuses whose KV entry is disposable once the client has had a
    # chance to read the final state. The durable record lives in Postgres
    # (audit_log / audit runs) and S3 (derived blobs, profiles) — the KV is only
    # a transient progress cache for in-flight polling.
    _TERMINAL_STATUSES = frozenset({JOB_STATUS_DONE, JOB_STATUS_ERROR, "cancelled"})

    async def purge_completed_jobs(self, grace_s: float = 600.0) -> int:
        """Drop KV entries for jobs that reached a terminal state more than
        ``grace_s`` ago, so the bucket stays small and ``keys()`` scans stay
        cheap. Without this, completed job-status entries accumulated forever
        (observed: 47k entries), turning every registry scan into a full-bucket
        replay that hammered NATS. ``__meta_*`` keys are never touched."""
        if self._kv is None:
            return 0
        try:
            keys = await self._kv.keys()
        except (BucketNotFoundError, Exception):
            return 0
        cutoff = time.time() - grace_s
        purged = 0
        for key in keys:
            if key.startswith(self._META_KEY_PREFIX):
                continue
            try:
                entry = await self._kv.get(key)
            except KeyNotFoundError:
                continue
            except Exception:
                continue
            try:
                job = Job.from_json(entry.value)
            except Exception:
                continue
            if job.status in self._TERMINAL_STATUSES and (job.updated_at or 0.0) < cutoff:
                try:
                    await self._kv.purge(key)
                    purged += 1
                except Exception:
                    pass
        if purged:
            logger.info("queue: purged %d completed job entr(ies) from the KV", purged)
        return purged

    # --- meta-state helpers ------------------------------------------
    #
    # The shared KV bucket also holds small operational metadata under
    # an ``__meta:`` prefix that can't collide with uuid.hex-shaped job
    # IDs. Used today for the worker pod self-reporting its image tag
    # so the viewer's /api/config can surface it.

    _META_KEY_PREFIX = "__meta_"

    async def set_meta(self, key: str, value: str) -> None:
        if self._kv is None:
            return
        await self._kv.put(f"{self._META_KEY_PREFIX}{key}", value.encode("utf-8"))

    async def get_meta(self, key: str) -> str | None:
        if self._kv is None:
            return None
        try:
            entry = await self._kv.get(f"{self._META_KEY_PREFIX}{key}")
        except KeyNotFoundError:
            return None
        except BucketNotFoundError:
            return None
        if entry.value is None:
            return None
        return entry.value.decode("utf-8", errors="replace")

    # --- compression sweep state -------------------------------------
    #
    # One entry per scope under ``__meta_compress_sweep_<slug>``.
    # Survives a viewer pod restart so a new session can see an
    # in-flight sweep that was started elsewhere. State is a small
    # JSON blob; mutations are read-modify-write at low frequency
    # (per-file completion) so the race window is acceptable.
    #
    # NATS KV restricts key characters to ``[A-Za-z0-9_=./-]`` — so
    # scope labels containing ``:`` (``user:me``, ``project:<uuid>``)
    # need slugification before they're safe to use as the key tail.
    # The original label is stored inside the JSON payload so reads
    # return the same shape regardless of slugging.

    _COMPRESS_SWEEP_KEY_PREFIX = "__meta_compress_sweep_"

    @staticmethod
    def _slugify_scope(scope_label: str) -> str:
        # ``:`` -> ``__`` is reversible *by convention* (no normal scope
        # label uses double-underscores) but reverse-mapping isn't
        # needed at read time — the canonical label lives inside the
        # JSON payload.
        return scope_label.replace(":", "__")

    async def set_compress_sweep_state(self, scope_label: str, state: dict) -> None:
        if self._kv is None:
            return
        payload = dict(state)
        payload["scope"] = scope_label
        key = f"{self._COMPRESS_SWEEP_KEY_PREFIX}{self._slugify_scope(scope_label)}"
        await self._kv.put(key, json.dumps(payload).encode("utf-8"))

    async def get_compress_sweep_state(self, scope_label: str) -> dict | None:
        if self._kv is None:
            return None
        key = f"{self._COMPRESS_SWEEP_KEY_PREFIX}{self._slugify_scope(scope_label)}"
        try:
            entry = await self._kv.get(key)
        except KeyNotFoundError:
            return None
        if entry.value is None:
            return None
        try:
            return json.loads(entry.value.decode("utf-8", errors="replace"))
        except ValueError:
            return None

    async def list_compress_sweep_states(self) -> dict[str, dict]:
        """Return ``{scope_label: state}`` for every recorded sweep.

        Uses the ``scope`` field inside each entry's JSON payload as
        the dict key — the KV key is slugified (``:`` -> ``__``) but
        the payload preserves the original label so callers see
        ``user:me`` / ``project:<uuid>`` round-trip intact.
        """
        if self._kv is None:
            return {}
        try:
            keys = await self._kv.keys()
        except (BucketNotFoundError, Exception):
            return {}
        out: dict[str, dict] = {}
        for key in keys:
            if not key.startswith(self._COMPRESS_SWEEP_KEY_PREFIX):
                continue
            try:
                entry = await self._kv.get(key)
            except KeyNotFoundError:
                continue
            if entry.value is None:
                continue
            try:
                payload = json.loads(entry.value.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            # Prefer the canonical scope from the payload; fall back to
            # the un-slugged key tail for forward compat with entries
            # written before this field existed.
            label = payload.get("scope") or key[len(self._COMPRESS_SWEEP_KEY_PREFIX) :]
            out[label] = payload
        return out

    # --- worker registry ---------------------------------------------
    #
    # Each running worker self-registers a small JSON blob under
    # ``__meta_worker:<worker_id>`` and refreshes it on a heartbeat.
    # The admin panel reads the whole set via ``list_workers``. Keys
    # are flat (no slashes) since NATS KV doesn't permit ``:`` in keys
    # — we use a hyphen-shaped worker id and rely on the meta prefix
    # for namespacing.

    _WORKER_KEY_PREFIX = "__meta_worker__"

    async def register_worker(self, worker_id: str, info: dict) -> None:
        """Write/refresh the worker entry. Idempotent — workers call
        this on startup and again on each heartbeat tick.

        Writes to the registry bucket, which is the jobs bucket unless
        ``registry_kv_bucket`` split it out. The key keeps its
        ``__meta_worker__`` prefix in both modes: it costs nothing, it keeps one
        code path, and it means a deployment can copy rows between buckets
        during a changeover without rewriting keys.
        """
        bucket = self._registry_bucket_handle
        if bucket is None:
            return
        key = f"{self._WORKER_KEY_PREFIX}{worker_id}"
        await bucket.put(key, json.dumps(info).encode("utf-8"))

    async def unregister_worker(self, worker_id: str) -> None:
        """Drop the worker entry. Best-effort — called from the worker's
        shutdown path. If it fails the entry will go stale within one
        heartbeat-staleness window, which the admin panel filters out."""
        key = f"{self._WORKER_KEY_PREFIX}{worker_id}"
        bucket = self._registry_bucket_handle
        if bucket is not None:
            try:
                await bucket.delete(key)
            except KeyNotFoundError:
                pass
        # A row this worker wrote before the registry moved buckets. Deleting it
        # is what stops a shut-down worker lingering in the listing for the full
        # prune horizon, and it is why ``list_workers`` can stop reading the old
        # bucket once no such rows come back. Broad except: a narrowed
        # credential may no longer be allowed to touch the jobs bucket's
        # registry rows at all, and that is not a shutdown failure.
        if self.registry_is_separate and self._kv is not None:
            try:
                await self._kv.delete(key)
            except Exception:
                pass

    async def list_workers(self) -> list[dict]:
        """Return every worker entry. Each row carries whatever the
        worker last wrote — image_tag, capabilities, started_at,
        last_heartbeat — plus the id derived from the KV key.

        Staleness filtering is the caller's concern: this method just
        snapshots the bucket.

        Reads the OLD bucket too while the registry is split out, so that
        turning ``registry_kv_bucket`` on does not empty the admin panel — and
        with it ``/api/plugins`` and extension-based routing — for every worker
        that has not restarted onto the new bucket yet. A row in the dedicated
        bucket wins over a same-id row in the jobs bucket: that is the worker
        that has already moved, so its entry is the fresher one.
        """
        rows: dict[str, dict] = {}
        # Old bucket first, so the dedicated bucket's rows overwrite it.
        buckets = []
        if self.registry_is_separate and self._kv is not None:
            buckets.append(self._kv)
        handle = self._registry_bucket_handle
        if handle is not None:
            buckets.append(handle)
        for bucket in buckets:
            for worker_id, info in await self._read_worker_rows(bucket):
                rows[worker_id] = info
        return list(rows.values())

    async def _read_worker_rows(self, bucket) -> list[tuple[str, dict]]:
        """``(worker_id, entry)`` for every registry row in one KV bucket."""
        try:
            keys = await bucket.keys()
        except (BucketNotFoundError, Exception):
            return []
        out: list[tuple[str, dict]] = []
        for key in keys:
            if not key.startswith(self._WORKER_KEY_PREFIX):
                continue
            try:
                entry = await bucket.get(key)
            except KeyNotFoundError:
                continue
            if entry.value is None:
                continue
            try:
                info = json.loads(entry.value.decode("utf-8", errors="replace"))
            except (ValueError, AttributeError):
                continue
            if not isinstance(info, dict):
                continue
            worker_id = key[len(self._WORKER_KEY_PREFIX) :]
            info["worker_id"] = worker_id
            out.append((worker_id, info))
        return out

    # Hard-prune horizon for dead worker entries — much longer than the ``WORKER_STALE_AFTER_S``
    # online window used for routing/UI. A pod that crashes or scales down can leave its registry
    # entry behind (``unregister_worker`` is best-effort); without pruning these accumulate and
    # pollute the capability union, so we drop entries unseen for 2 days.
    WORKER_PRUNE_AFTER_S = 2 * 24 * 3600

    async def prune_stale_workers(self, max_age_s: float | None = None) -> int:
        """Delete worker registry entries whose ``last_heartbeat`` is older than ``max_age_s`` (default
        :data:`WORKER_PRUNE_AFTER_S`, 2 days). Entries with a missing/garbage heartbeat are pruned too
        (they can never be online). Returns the number removed."""
        if self._kv is None:
            return 0
        cutoff = time.time() - (max_age_s if max_age_s is not None else self.WORKER_PRUNE_AFTER_S)
        pruned = 0
        for w in await self.list_workers():
            hb = w.get("last_heartbeat")
            if isinstance(hb, (int, float)) and hb >= cutoff:
                continue
            wid = w.get("worker_id")
            if wid:
                await self.unregister_worker(wid)
                pruned += 1
        return pruned

    # --- consumer side (called from worker) --------------------------

    # ack_wait is the window after which JetStream redelivers an un-acked
    # message — i.e. how long a *dead* worker's job sits stuck before retry.
    # It used to be 30 min so a long bake wouldn't be redelivered mid-run, but
    # that also meant an OOM-killed pod left its job stuck for 30 min × up to
    # MAX_DELIVERIES (~80 min observed). The worker now refreshes the deadline
    # with ``msg.in_progress()`` every IN_PROGRESS_REFRESH_SECONDS, so a healthy
    # long job keeps its lease indefinitely and ack_wait only governs how fast a
    # *crashed* worker is detected. Keep it a few minutes: long enough to absorb
    # a brief event-loop stall, short enough that a poison/OOM job dead-letters
    # in minutes. Must be comfortably larger than the worker's refresh cadence.
    _ACK_WAIT_SECONDS = 3 * 60

    async def pull_subscribe(self, capability: str | None = None):
        """Create a per-pool durable pull-subscriber on the work-queue stream.

        Each worker pool subscribes to ONE capability subject suffix
        (default ``"base"``). The durable name embeds the capability
        so each pool gets its own cursor and JetStream's subject
        filter ensures a pod only ever pulls messages tagged for its
        pool. Replaces the previous shared-consumer design that
        forced every worker to NAK messages from other pools — that
        NAK loop burned the per-message delivery budget and surfaced
        as ``worker exceeded 3 delivery attempts`` errors on perfectly
        valid jobs (see the internal notes).

        Idempotent: ``pull_subscribe`` matches an existing durable by
        name if the config is compatible, so multiple pods in the
        same pool share one cursor.
        """
        cap = (capability or self.DEFAULT_CAPABILITY).strip().lower()
        filter_subject = f"{self._cfg.subject}.{cap}"
        durable = f"{self._cfg.durable}-{cap}"
        return await self._js.pull_subscribe(
            subject=filter_subject,
            durable=durable,
            config=ConsumerConfig(
                ack_wait=self._ACK_WAIT_SECONDS,
                filter_subject=filter_subject,
            ),
        )


__all__ = [
    "Job",
    "JobQueue",
    "QueueDisabled",
    "capability_token",
    "MAX_CAPABILITY_TOKEN_LEN",
    "JOB_STATUS_QUEUED",
    "JOB_STATUS_RUNNING",
    "JOB_STATUS_DONE",
    "JOB_STATUS_ERROR",
]
