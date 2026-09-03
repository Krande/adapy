"""Conversion worker: pulls jobs from NATS JetStream, runs the
converter in a threadpool, writes the derived GLB to storage, and
updates the job's status in KV.

Run as `python -m ada.comms.rest.worker`. Reads the same env vars as
the API service so a single image can be deployed twice (api + worker)
with the same config map.

Crash semantics: a job message is acked only after the derived blob
is uploaded and the KV entry is marked done. If the worker dies
mid-conversion the message is redelivered after `ack_wait`; the next
worker reconverts (deterministic output, so this is safe).
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import datetime
import functools
import io
import logging
import os
import pathlib
import shutil
import signal
import tempfile
import threading
import time
import traceback as tb_module
from concurrent.futures import (  # noqa: F401 — kept for the legacy _process_one signature
    ThreadPoolExecutor,
)
from typing import Awaitable, Callable

import asyncpg

from ada.config import logger
from ada.core.file_system import new_temp_path

from . import db as db_module
from . import source_cache
from .config import load_settings
from .converter import LEGACY_CONVERT_EXTS, ConverterRegistry, convert
from .qualification import CAPABILITY_REQUIREMENTS_KEY, evaluate
from .queue import (
    JOB_STATUS_DONE,
    JOB_STATUS_ERROR,
    JOB_STATUS_RUNNING,
    Job,
    JobQueue,
    capability_token,
)
from .scope import Scope
from .storage import Storage
from .subprocess_convert import (
    ConvertSample,
    IsolatedConvertResult,
    run_isolated_convert,
)


def _scope_of(job: Job) -> Scope:
    """Reconstruct the Scope a job's source/derived blobs live under.
    Defaults to ``shared`` for jobs serialized before scope_kind existed.
    """
    if job.scope_kind == "project" and job.scope_id:
        return Scope.project(job.scope_id)
    if job.scope_kind == "user" and job.scope_id:
        return Scope.user(job.scope_id)
    if job.scope_kind == "corpus" and job.scope_id:
        return Scope.corpus(job.scope_id)
    return Scope.shared()


_LIBC_MALLOC_TRIM: object = None  # cached glibc malloc_trim, or False when unavailable


def _trim_parent_memory() -> None:
    """Return glibc's freed-but-retained arena memory to the OS in the long-lived worker PARENT
    after each job. The parent forks per conversion; the freed per-job allocations it makes itself
    (reading the child's captured log, parsing the marker JSON / cpp profile, building convert_meta,
    the metrics-sample lists) pile up in glibc's arena free-lists rather than returning to the OS, so
    parent RSS creeps up across a run (measured on a 23h prod worker: 218 MB fresh -> 540-840 MB idle,
    higher mid-run). Because every conversion is a fork, the child INHERITS the parent's address space
    (COW, counted in the child's RSS), so a bloated parent lifts EVERY conversion's baseline — enough
    to push a big-model fork (469826) over the per-job memory watchdog, and the parent+child sum over
    the 6 GiB pod limit (the run-90 pod OOM / Exit 137). Trimming after each job keeps the parent flat.
    glibc-only; a best-effort no-op elsewhere. Disable with ADA_WORKER_NO_MALLOC_TRIM."""
    global _LIBC_MALLOC_TRIM
    if _LIBC_MALLOC_TRIM is None:
        if os.environ.get("ADA_WORKER_NO_MALLOC_TRIM"):
            _LIBC_MALLOC_TRIM = False
        else:
            try:
                _LIBC_MALLOC_TRIM = ctypes.CDLL("libc.so.6").malloc_trim
            except (OSError, AttributeError):
                _LIBC_MALLOC_TRIM = False
    if _LIBC_MALLOC_TRIM:
        try:
            _LIBC_MALLOC_TRIM(0)
        except Exception:  # noqa: BLE001 — memory hygiene must never fail a job
            pass


# How long the pull-subscriber waits per fetch round before re-issuing.
# Short enough to react to shutdown; long enough not to spam NATS.
FETCH_TIMEOUT = 5.0
# Workers fetch one message at a time — conversions are heavy and we
# don't want to hold a batch of acks open during a long-running job.
FETCH_BATCH = 1
# Maximum delivery attempts per job before we permanently mark it
# error and ack. Catches "poison pill" jobs whose conversion crashes
# the worker process (OS-level malloc / segfault) — the message gets
# redelivered each time without ever being acked, infinite-looping.
# After this many tries the worker stops attempting and acks so the
# message leaves the stream.
MAX_DELIVERIES = 3
# How many jobs in a row one pool may serve before the round-robin cursor moves
# on regardless.
#
# WHY THIS EXISTS. The pools are polled one at a time with a blocking fetch, so
# a worker serving N capabilities walks N-1 empty pools between consecutive jobs
# from the one pool that is busy — at ``_per_fetch_timeout`` each. On a combined
# worker (6 capabilities) that is ~2.1s of dead time per job: measured on a
# 907-cell sweep it was 20 minutes of a 69-minute run, and the run had been 40
# minutes on a single-pool image. Cell durations were unchanged; the pool was
# simply idle 39% of the time.
#
# Staying on a pool that just produced work removes that walk. The cap is what
# keeps it fair: without it a permanently-busy pool would starve every other
# capability this worker advertises, which is worse than the latency it fixes.
# At the default, another pool waits at most this many jobs plus one cycle.
#
# 0 or 1 restores the strict round-robin.
POOL_STREAK_LIMIT = max(1, int(os.environ.get("ADA_WORKER_POOL_STREAK_LIMIT", "8") or 8))

# While a job runs we refresh the JetStream ack deadline with
# ``msg.in_progress()`` on this cadence. A live worker keeps extending its
# lease; the moment it dies (OOM-killed pod, node failure, crash) the refreshes
# stop and JetStream redelivers within ~one ack_wait (see queue._ACK_WAIT_SECONDS)
# instead of the old fixed 30 min — so a poison/OOM job is detected and
# dead-lettered (MAX_DELIVERIES) in minutes, not ~80. Must be comfortably shorter
# than ack_wait; the conversion runs in a child process so the parent event loop
# stays free to fire these.
IN_PROGRESS_REFRESH_SECONDS = 30

# How often the worker re-publishes its registration. Also, incidentally, the
# only round-trip to the bus an IDLE worker makes: a pull fetch that times out
# with no messages is indistinguishable from a healthy quiet queue, so the
# heartbeat is the one thing whose failure means something.
BUS_HEARTBEAT_SECONDS = 15.0

# Consecutive heartbeat failures after which the worker exits non-zero instead
# of continuing to look alive.
#
# WHY THIS EXISTS. A worker whose connection is gone should either recover or
# stop; the state worth designing against is the third one, where it does
# neither. The client library is not guaranteed to fail loudly — a read loop can
# stop without raising, leaving ``is_connected`` true and every subsequent
# request timing out — and nothing else here would notice. The liveness file is
# touched by the poll loop, which keeps iterating happily against a dead
# connection, so it goes on reporting health. The registry row goes stale and
# the worker vanishes from the admin view, but the process does not care.
#
# So jobs route to a pool that no longer consumes, and sit. That is the failure
# this bounds: not the disconnect, which is ordinary, but the silence after it.
#
# The number is a compromise between the two ways of being wrong. Too low and a
# transient blip restarts a worker mid-job, losing work that would have
# recovered on its own. Too high and jobs queue against a dead pool for as long
# as it takes. At the cadence above this is a full minute of consecutive
# failures — far longer than a reconnect after a routine bus restart, short
# enough that nothing waits on it for long.
BUS_HEARTBEAT_FAILURE_LIMIT = 4


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _worker_id() -> str:
    """This worker's registry key.

    Self-asserted today: whatever ``$HOSTNAME`` says, which in k8s is the
    pod name and off-cluster is whatever the operator exports. Once NATS
    credentials pin the registry KV key (``__meta_worker__<id>``) the
    server enforces the match and this becomes an authenticated identity
    rather than a claim — see deploy/worker-trust.md.
    """
    return os.environ.get("HOSTNAME", "").strip() or f"local-{os.getpid()}"


def _declared_capabilities() -> list[str]:
    """What this worker advertises, after subtracting any disabled by env.

    ``ADA_WORKER_CAPABILITIES`` is the positive list, and normally comes from
    the IMAGE rather than from a deployment: the image is what actually carries
    the packages behind each capability, so it is the only place that can state
    the set correctly. A deployment repeating the list is a second copy of a
    fact it does not own, and it drifts -- one such copy sat a capability behind
    its image for weeks, advertising a pool that the image had and the manifest
    did not mention, with no error anywhere because a job for a pool nobody
    subscribes to is accepted and then simply never runs.

    ``ADA_WORKER_DISABLED_CAPABILITIES`` is the subtractive half, and is what a
    deployment SHOULD reach for. It is for one job: taking a misbehaving pool
    out of service without rebuilding or rolling back an image, which would
    revert every other capability and the adapy version along with it. Normally
    absent.

    Subtraction happens BEFORE the capability is advertised, not only before it
    is subscribed. Advertising a pool this worker will not serve recreates
    exactly the failure above -- the API routes to it and the job waits out its
    timeout -- so the two must never disagree.

    Compared through :func:`capability_token`, so ``Abaqus`` disables
    ``abaqus``: an operator typing a capability under incident pressure should
    not have to match case.

    Disabling everything falls back to ``base`` with a warning rather than
    leaving a worker that advertises nothing: a worker subscribed to nothing is
    not a configuration anyone wants, and scaling the deployment to zero is how
    you idle a pool.
    """
    declared = [c.strip() for c in os.environ.get("ADA_WORKER_CAPABILITIES", "base").split(",") if c.strip()]
    # An explicitly blank ADA_WORKER_CAPABILITIES means "unset", not "serve
    # nothing". Normalised here so that an empty list further down the pipeline
    # can only mean a deliberate verdict -- disabled here, or withheld by
    # qualification -- and never a variable somebody left empty.
    if not declared:
        declared = ["base"]
    raw_disabled = [c.strip() for c in os.environ.get("ADA_WORKER_DISABLED_CAPABILITIES", "").split(",") if c.strip()]
    if not raw_disabled:
        return declared

    disabled = {t for t in (capability_token(c) for c in raw_disabled) if t}
    kept = [c for c in declared if capability_token(c) not in disabled]

    dropped = [c for c in declared if capability_token(c) in disabled]
    if dropped:
        # WARNING, not info: this is a deliberate reduction in service, and the
        # symptom of forgetting to remove it later is jobs that queue forever.
        logger.warning(
            "worker: capabilities disabled by ADA_WORKER_DISABLED_CAPABILITIES: %s",
            ",".join(dropped),
        )
    # A capability may be SHARDED: one plugin addressing several pools by
    # suffixing the capability with an option value (`cad`, `cad-alpha`), so a
    # worker can hold both. Those are distinct tokens, so disabling `cad` leaves
    # `cad-alpha` serving -- and the "names nothing this worker advertises"
    # warning below does not fire, because `cad` did match. The operator gets a
    # line confirming a capability was disabled and reasonably believes the pool
    # is out of service while half of it still pulls jobs.
    #
    # Prefix-matching by default would be worse (`web3d` must not vanish because
    # somebody disabled `web`), so the shards are named instead and left for the
    # operator to disable deliberately.
    for token in sorted(disabled):
        siblings = sorted(c for c in kept if capability_token(c).startswith(f"{token}-"))
        if siblings:
            logger.warning(
                "worker: disabled %s, but %s %s still advertised — disable %s separately",
                token,
                ",".join(siblings),
                "is" if len(siblings) == 1 else "are",
                "it" if len(siblings) == 1 else "them",
            )

    unmatched = sorted(disabled - {capability_token(c) for c in declared})
    if unmatched:
        # Names nothing this worker has. Usually a typo, and a typo here is
        # silent -- the pool it was meant to stop stays up.
        logger.warning(
            "worker: ADA_WORKER_DISABLED_CAPABILITIES names %s, which this worker does not advertise",
            ",".join(unmatched),
        )
    if not kept:
        # Falling back to `base` rather than serving nothing is right for the
        # deployments this exists for -- but only if `base` was ever this
        # worker's to serve. A worker that never declared it must not ACQUIRE
        # it by subtraction: an off-cluster machine joining for one capability
        # would start pulling ordinary conversion jobs from the cluster's
        # queue on an independently-installed adapy, which is the hazard
        # ADA_WORKER_BASE_CONVERSIONS exists to prevent. Reaching it through an
        # incident switch would be a particularly unpleasant route to it.
        if any(capability_token(c) == "base" for c in declared):
            logger.warning(
                "worker: every advertised capability was disabled; falling back to 'base'. "
                "Scale the deployment to zero to idle a pool instead."
            )
            return ["base"]
        logger.warning(
            "worker: every advertised capability was disabled and this worker does not serve "
            "'base', so it will subscribe to nothing. It stays up and keeps reporting, because a "
            "worker that exits is indistinguishable from one that was never started. Scale the "
            "deployment to zero to idle a pool instead."
        )
        return []
    return kept


def _pool_capabilities(capabilities: list[str]) -> list[str]:
    """Capability pools this worker should subscribe to.

    Normalised through :func:`capability_token` and de-duplicated while
    preserving order, so a repeated or differently-cased entry in
    ``ADA_WORKER_CAPABILITIES`` cannot open two consumers on the same subject.
    Falls back to ``["base"]`` so a worker is never left subscribed to nothing.

    Deliberately the same normaliser the API uses to build the subject it
    PUBLISHES on. A sharded capability like ``cad-Site A`` has to reduce to an
    identical token on both sides, or the job is published to a subject nothing
    is subscribed to and sits in the stream looking merely slow.
    """
    pools = [t for t in (capability_token(c) for c in capabilities) if t]
    return list(dict.fromkeys(pools)) or ["base"]


def _per_fetch_timeout(n_pools: int) -> float:
    """Per-pool fetch timeout so one full round-robin cycle takes ~FETCH_TIMEOUT.

    The pools are polled one at a time (see the poll loop for why), so without
    dividing the timeout a worker's pickup latency would grow linearly with the
    number of capabilities it serves. Floored so many pools cannot degenerate
    into a busy-loop of near-instant fetches.
    """
    return max(0.5, FETCH_TIMEOUT / max(1, n_pools))


def _advance_pool_cursor(rr: int, streak: int, produced: bool, limit: int = POOL_STREAK_LIMIT) -> tuple[int, int]:
    """Where the round-robin cursor goes after one fetch — ``(rr, streak)``.

    Split out from the poll loop because it is the whole scheduling policy and
    the loop around it is untestable: everything else there needs a live
    JetStream consumer.

    An empty pool advances immediately, as it always did. A pool that produced
    work is kept, so a saturated pool re-fetches instead of walking every other
    (empty) pool first — until it has served ``limit`` jobs in a row, at which
    point it yields whether or not it still has work. That bound is what stops a
    busy pool starving the other capabilities the worker advertises.
    """
    if not produced:
        return rr + 1, 0
    streak += 1
    if streak >= max(1, limit):
        return rr + 1, 0
    return rr, streak


# Liveness heartbeat. The worker touches this file whenever its JetStream pull loop iterates
# (idle / between jobs) or an in-flight conversion reports progress. A k8s livenessProbe checks the
# file's mtime is fresh: if the pull loop stalls — e.g. the durable consumer wedged after a NATS
# restart, leaving the pod "Running" but no longer fetching (num_waiting=0) — the file goes stale and
# k8s restarts the pod, instead of it sitting silently broken while jobs pile up unconsumed.
WORKER_LIVENESS_FILE = os.environ.get("WORKER_LIVENESS_FILE", "/tmp/worker-alive")


def _touch_liveness() -> None:
    try:
        with open(WORKER_LIVENESS_FILE, "w") as fh:
            fh.write(str(time.time()))
    except OSError:
        logger.debug("worker: liveness touch failed", exc_info=True)


# Per-source-suffix sidecar files that the worker co-downloads next to
# the main payload so format-specific readers find them by basename.
# Keep this conservative — a 404 on an absent sibling is silent, but
# we still pay one S3 HEAD per attempt. Add entries here as readers
# grow new sidecar needs; ``.adapy_fem.json`` is the code_aster
# lineage + per-element tessellation companion.
_SIDECAR_SIBLINGS: dict[str, tuple[str, ...]] = {
    ".rmed": (".adapy_fem.json",),
}


# Set once at worker startup from ``ADA_IMAGE_TAG`` (helm chart
# stamps the build SHA into that env var). Read here without
# threading through every call site so the audit row gets the same
# attribution we publish on the workers KV registry without
# touching the per-job code paths.
_WORKER_IMAGE_TAG: str | None = None

# The worker's graceful-shutdown event, published module-level by ``run_worker``
# so long-running poll loops inside a handler (e.g. the chained procedural_detail
# stage waiting on the structural build) can wake early on SIGTERM/SIGINT instead
# of blocking the pod's shutdown for the full wait budget. ``None`` until the
# worker loop wires it (a handler running in a unit test just sees no stop event
# and polls to its timeout).
_WORKER_STOP: "asyncio.Event | None" = None

# Chained procedural_detail waits for the upstream structural build (a DIFFERENT
# pool, no ordering guarantee) to write the neutral artifact before it runs. Poll
# storage for the artifact up to this total budget, sleeping this interval between
# checks (interruptible on shutdown). 120 s comfortably covers a realistic
# structural compile; 3 s keeps the poll cheap without busy-spinning.
STRUCTURAL_ARTIFACT_WAIT_BUDGET_S = 120.0
STRUCTURAL_ARTIFACT_WAIT_INTERVAL_S = 3.0
# Once the (required) IFC artifact exists the sections sidecar — written moments
# later by the same build — should appear almost immediately; give it a short
# grace before degrading to an empty sidecar.
STRUCTURAL_SECTIONS_WAIT_BUDGET_S = 15.0


async def _wait_for_blob(
    storage: "Storage",
    scope,
    key: str,
    *,
    queue: "JobQueue",
    job_id: str,
    budget_s: float,
    interval_s: float | None = None,
    waiting_stage: str | None = None,
) -> bool:
    """Poll object storage until ``key`` exists, up to ``budget_s`` (sleeping
    ``interval_s`` between checks — defaulting to the module poll interval, read at
    call time so it stays tunable). Returns ``True`` as soon as the blob is present,
    ``False`` on timeout or a graceful worker shutdown.

    Interruptible + non-busy-spinning: the between-checks wait blocks on the
    module-level shutdown event (``_WORKER_STOP``) via ``asyncio.wait_for`` so a
    SIGTERM wakes it immediately, mirroring the keep-alive/heartbeat loops. When no
    stop event is wired (unit tests) it falls back to a plain ``asyncio.sleep``."""
    interval_s = STRUCTURAL_ARTIFACT_WAIT_INTERVAL_S if interval_s is None else interval_s
    deadline = time.monotonic() + budget_s
    stop = _WORKER_STOP
    announced = False
    while True:
        try:
            if await storage.exists(scope, key):
                return True
        except Exception:
            # A transient storage error shouldn't abort the wait — retry next tick.
            logger.debug("worker: exists() check failed for %s (retrying)", key)
        if stop is not None and stop.is_set():
            return False
        if time.monotonic() >= deadline:
            return False
        if waiting_stage and not announced:
            try:
                await queue.update(job_id, stage=waiting_stage, progress=0.10)
            except Exception:
                logger.debug("worker: could not update stage while waiting for %s", key)
            announced = True
        if stop is not None:
            try:
                # Wake early when the worker is asked to shut down.
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
                return False
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(interval_s)


async def _audit_done(
    db_pool: asyncpg.Pool | None,
    job_id: str,
    status: str,
    error: str | None,
    started_at: float,
    traceback: str | None = None,
    metrics: dict | None = None,
) -> None:
    """Patch the audit_log row for this job with its final outcome.
    Best-effort: a DB hiccup must never break job processing."""
    if db_pool is None:
        return
    metrics = metrics or {}
    try:
        await db_module.update_audit_by_job(
            db_pool,
            job_id=job_id,
            status=status,
            error=error,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            traceback=traceback,
            cpu_user_ms=metrics.get("cpu_user_ms"),
            cpu_sys_ms=metrics.get("cpu_sys_ms"),
            peak_rss_kb=metrics.get("peak_rss_kb"),
            read_bytes=metrics.get("read_bytes"),
            write_bytes=metrics.get("write_bytes"),
            profile_key=metrics.get("profile_key"),
            log_key=metrics.get("log_key"),
            worker_image_tag=_WORKER_IMAGE_TAG,
            convert_meta=metrics.get("convert_meta"),
        )
    except Exception:
        logger.exception("worker: audit update failed for job %s", job_id)


def _gate_enum_option(opt: dict | None, allowed: set[str] | frozenset[str]) -> None:
    """Filter one enum option's values to ``allowed`` and repair its default in place.

    An option that would filter to EMPTY is left untouched: a stale-but-selectable dropdown beats an
    empty one, and an empty enum reads to the API's union as "this pool advertises nothing" rather
    than "this pool couldn't tell".
    """
    if not opt or not isinstance(opt.get("enum"), list):
        return
    kept = [e for e in opt["enum"] if e in allowed]
    if not kept:
        return
    opt["enum"] = kept
    if opt.get("default") not in kept:
        opt["default"] = kept[0]


def _gate_serializer_axis(ser: dict | None, tess: dict | None, allowed: frozenset[str]) -> None:
    """Gate the serializer × tessellator pair to the kernels this worker has, in place.

    CLIENT serializers pass through ungated. ``wasm`` executes in the user's browser, so this
    worker lacking adacpp says nothing about whether the browser can run it; filtering it here
    would make the SPA lose the in-browser option because a *server* pool was thin. That is why the
    runtime map — not a name check — decides: :func:`converter.available_tess_tokens` deliberately
    omits the client tokens, so gating them against it would drop every one of them.

    A server serializer whose kernels all vanish is dropped entirely (with its label/runtime entry),
    and the tessellator enum is rebuilt as the union of what survived. B-rep rows ride the same
    path: their second axis is a WRITER, but "can this process run it" is the same question about
    the same backends, so the same token set answers it.
    """
    from .converter import _GLB_CLIENT_SERIALIZERS

    if not ser or not tess or not isinstance(tess.get("enum_by"), dict):
        return
    enum_by: dict[str, list[str]] = {}
    for s, toks in tess["enum_by"].items():
        if not isinstance(toks, list):
            continue
        if s in _GLB_CLIENT_SERIALIZERS:
            enum_by[s] = list(toks)
            continue
        kept = [t for t in toks if t in allowed]
        if kept:
            enum_by[s] = kept
    if not enum_by:
        return
    tess["enum_by"] = enum_by

    if isinstance(ser.get("enum"), list):
        ser["enum"] = [s for s in ser["enum"] if s in enum_by]
        if ser.get("default") not in ser["enum"] and ser["enum"]:
            ser["default"] = ser["enum"][0]
        for key in ("labels", "runtime"):
            if isinstance(ser.get(key), dict):
                ser[key] = {s: v for s, v in ser[key].items() if s in enum_by}

    order = [s for s in (ser.get("enum") or []) if s in enum_by] or list(enum_by)
    toks: list[str] = []
    for s in order:
        for t in enum_by[s]:
            if t not in toks:
                toks.append(t)
    tess["enum"] = toks
    # Mirror _glb_serializer_options' own rule (the default tessellator is the default serializer's
    # first) so a serializer dropped above takes its default with it.
    default_ser = ser.get("default")
    if default_ser in enum_by and enum_by[default_ser]:
        tess["default"] = enum_by[default_ser][0]
    elif toks and tess.get("default") not in toks:
        tess["default"] = toks[0]
    for key in ("labels", "descriptions"):
        if isinstance(tess.get(key), dict):
            tess[key] = {t: v for t, v in tess[key].items() if t in toks}


def _gate_advertised_engines(conversions: list[dict]) -> list[dict]:
    """Restrict every advertised engine/kernel enum to what this worker can actually run, so the
    API's per-worker union and its engine routing reflect real capability.
    Deep-copies so the shared ConverterRegistry option dicts aren't mutated.

    Gates THREE axes, not one. Gating only ``step_glb_pipeline`` (as this did) left the
    ``serializer``/``tessellator`` dropdowns — the ones the SPA actually renders — advertising every
    kernel the registry knows, including ones this pool has no build of. A job then routed here on
    the strength of that advert and silently fell back, so the user picked a track and got a
    different one. ``glb_tess_engine`` carries the same engine vocabulary for non-STEP sources and
    was equally ungated.

    The runnable sets come from ``available_step_glb_pipelines()`` / ``available_tess_tokens()``,
    which gate the adacpp engines on find_spec presence (overlay-robust) rather than an
    import-based native probe — see the note there.
    """
    import copy

    from .converter import available_step_glb_pipelines, available_tess_tokens

    engines = set(available_step_glb_pipelines())
    tokens = available_tess_tokens()
    gated = copy.deepcopy(conversions)
    for row in gated:
        for opts in (row.get("options") or {}).values():
            if not isinstance(opts, list):
                continue
            by_name = {o.get("name"): o for o in opts if isinstance(o, dict)}
            for name in ("step_glb_pipeline", "glb_tess_engine"):
                _gate_enum_option(by_name.get(name), engines)
            _gate_serializer_axis(by_name.get("serializer"), by_name.get("tessellator"), tokens)
    return gated


def _convert_meta_for(job: "Job", env_overrides: dict | None) -> dict | None:
    """Provenance for a conversion's audit row: which tessellator/engine actually
    ran (resolved here the same way the convert subprocess resolves it — adacpp
    availability is identical in this shared env — so a libtess2→occ-builtin
    fallback is recorded accurately) plus the effective toggle options."""
    suffix = pathlib.PurePosixPath(job.source_key).suffix.lower()
    meta: dict = {}
    # The effective non-default toggles applied to the child (settings + per-job).
    if env_overrides:
        meta["options"] = dict(env_overrides)
    if job.target_format == "glb" and suffix in {".step", ".stp"}:
        try:
            from ada.comms.rest.converter import (
                _STEP_GLB_PIPELINE_ADACPP_NATIVE,
                _STEP_GLB_PIPELINE_OCC,
                _cad_config_for_pipeline,
                _resolve_step_glb_pipeline,
            )

            requested = _resolve_step_glb_pipeline((env_overrides or {}).get("ADAPY_STEP_GLB_PIPELINE"))
            meta["step_glb_pipeline"] = requested
            if requested == _STEP_GLB_PIPELINE_ADACPP_NATIVE:
                # Fully in-process C++ reader + tessellate + GLB writer — no CadBackend config, so
                # _cad_config_for_pipeline() is None for it (don't mislabel that as an occ fallback).
                meta["tessellator"] = "adacpp:native"
            elif requested == _STEP_GLB_PIPELINE_OCC:
                meta["tessellator"] = "occ-builtin"
            else:
                cfg = _cad_config_for_pipeline(requested)
                if cfg is not None:
                    meta["tessellator"] = cfg.path.value  # e.g. "adacpp:libtess2"
                else:
                    meta["tessellator"] = f"occ-builtin (fallback from {requested})"
            meta["glb_compression"] = (env_overrides or {}).get("ADA_GLB_COMPRESSION") or "meshopt"
            meta["stream_workers"] = (env_overrides or {}).get("ADA_STEP_STREAM_WORKERS")
        except Exception:
            logger.exception("worker: convert_meta tessellator resolution failed for %s", job.source_key)
    return meta or None


def _attach_cpp_profiles(convert_meta: dict | None, log_bytes: bytes | None) -> None:
    """Parse the adacpp pipeline profiler's machine-readable summaries out of the
    captured child output into ``convert_meta["cpp_profile"]``.

    When the ``profile_conversions`` toggle is on, the child runs with
    ``ADACPP_STEP_PROFILE=1`` and each instrumented C++ pipeline prints ONE
    ``[STEPPROF-JSON] {...}`` line at teardown (phase wall/RSS, VmHWM peak,
    per-solid stats, parallelism/IO pressure, per-thread utilisation). Attaching
    them to convert_meta puts the C++ side in the audit Metrics panel with the
    same visibility as the Python timings. No-op when profiling was off (no
    marker lines) or the log is empty."""
    if not isinstance(convert_meta, dict) or not log_bytes:
        return
    import json

    marker = b"[STEPPROF-JSON] "
    profiles: list[dict] = []
    for line in log_bytes.splitlines():
        i = line.find(marker)
        if i < 0:
            continue
        try:
            profiles.append(json.loads(line[i + len(marker) :].decode("utf-8", "replace")))
        except (ValueError, UnicodeDecodeError):
            continue  # a torn/interleaved line must not fail the job
    if profiles:
        convert_meta["cpp_profile"] = profiles

    # Per-conversion quality flags emitted by the child (subprocess_convert). Currently the
    # NGEOM(libtess2/adacpp)->OCC fallback tally — a conversion that silently completed on OCC
    # instead of the selected stream kernel. Surfaced as a cell flag in the audit grid.
    fb_marker = b"[TESSFALLBACK-JSON] "
    for line in log_bytes.splitlines():
        i = line.find(fb_marker)
        if i < 0:
            continue
        try:
            fb = json.loads(line[i + len(fb_marker) :].decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(fb, dict) and fb.get("count"):
            convert_meta["occ_fallback"] = fb  # {count, reasons, geoms}
        break

    mh_marker = b"[MESHHEALTH-JSON] "
    for line in log_bytes.splitlines():
        i = line.find(mh_marker)
        if i < 0:
            continue
        try:
            mh = json.loads(line[i + len(mh_marker) :].decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(mh, dict) and mh.get("distorted_tris"):
            convert_meta["mesh_flags"] = mh  # {n_tris, distorted_tris, distorted_frac}
        break

    # Triangle tally — total output triangles (+ primitive/solid counts when known). The primary
    # run-to-run regression signal: a tessellation-density change or a dropped solid moves n_tris.
    ts_marker = b"[TRISTATS-JSON] "
    for line in log_bytes.splitlines():
        i = line.find(ts_marker)
        if i < 0:
            continue
        try:
            ts = json.loads(line[i + len(ts_marker) :].decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(ts, dict) and ts.get("n_tris"):
            convert_meta["tri_stats"] = ts  # {n_tris, engine?, n_primitives?, n_solids?, ...}
        break

    # Geometry health — faces with a real trim boundary that tessellated to zero triangles (silently
    # dropped geometry, e.g. the SURFACE_OF_LINEAR_EXTRUSION drops). Flagged in the audit grid so this
    # class of bug is caught without visual inspection.
    gh_marker = b"[GEOMHEALTH-JSON] "
    for line in log_bytes.splitlines():
        i = line.find(gh_marker)
        if i < 0:
            continue
        try:
            gh = json.loads(line[i + len(gh_marker) :].decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(gh, dict) and gh.get("dropped_faces"):
            convert_meta["geom_health"] = gh  # {dropped_faces, total_faces}
        break


def _capture_worker_packages() -> list[dict]:
    """Snapshot the worker env's installed packages — the conda-meta manifest
    (authoritative for occt / pythonocc-core / ada-cpp / ifcopenshell / numpy …)
    plus any pip-only dists. Best-effort; a parse failure just drops that entry."""
    import glob
    import importlib.metadata as _im
    import json as _json
    import sys

    pkgs: dict[str, dict] = {}
    try:
        for f in glob.glob(os.path.join(sys.prefix, "conda-meta", "*.json")):
            try:
                with open(f) as fh:
                    d = _json.load(fh)
                name = d.get("name")
                if name:
                    pkgs[name.lower()] = {
                        "name": name,
                        "version": d.get("version"),
                        "build": d.get("build"),
                        "channel": d.get("channel"),
                    }
            except Exception:
                continue
    except Exception:
        pass
    try:
        for dist in _im.distributions():
            name = (dist.metadata.get("Name") or "").strip()
            if name and name.lower() not in pkgs:
                pkgs[name.lower()] = {"name": name, "version": dist.version, "build": None, "channel": "pypi"}
    except Exception:
        pass
    return sorted(pkgs.values(), key=lambda p: (p.get("name") or "").lower())


async def _run_fea_artefact_bake(
    *,
    job: Job,
    src_path: pathlib.Path,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    _on_progress: Callable[[str, float], Awaitable[None]],
) -> None:
    """Bake the streaming-viewer artefact tree for ``job.source_key``.

    Source has already been streamed to ``src_path``. Produces:

    * ``_derived/<src>.fea/fea.mesh.glb``
    * ``_derived/<src>.fea/fea.manifest.json`` (gzip)
    * ``_derived/<src>.fea/fea.<field>.bin`` × N (identity — HTTP-Range-able)

    Updates the queue + audit row to mirror the convert flow's
    end-of-job semantics so the existing ``/convert/{job_id}`` poll
    loop works unchanged.
    """

    job_id = job.job_id

    # Defer the heavy imports until we actually have a job to bake —
    # the worker boots faster and a pure-convert worker doesn't pay
    # the import-time cost.
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    await _on_progress("parsing", 0.10)

    # Admin "Stream SIN FEA bake" toggle (app_settings ``fea_sin_streamer``).
    # The bake runs in-process on an executor thread, so we drive the
    # reader choice through the same ADA_* env-var seam the convert path
    # uses; _make_sin_reader reads it. Default (unset/empty) keeps adapy's
    # full-materialise reader. Set fresh per job so toggling takes effect
    # without a worker restart.
    if db_pool is not None:
        try:
            sin_stream = await db_module.get_setting(db_pool, "fea_sin_streamer")
        except Exception:
            logger.exception("worker: failed to read fea_sin_streamer setting")
            sin_stream = None
        if sin_stream is not None and sin_stream.strip() != "":
            os.environ["ADA_FEA_SIN_STREAMER"] = sin_stream
        else:
            os.environ.pop("ADA_FEA_SIN_STREAMER", None)

    bake_dir = pathlib.Path(tempfile.mkdtemp(prefix="fea-bake-"))
    try:
        loop = asyncio.get_running_loop()
        # Heartbeat task — the bake runs on an executor thread and has
        # no progress callback of its own. Without an external ping
        # the queue's ``updated_at`` (and ``msg.in_progress`` on the
        # JetStream side once we plumb it) sit frozen at the last
        # progress milestone for the duration of the bake; the SPA's
        # "stuck at 10%" symptom is just the toast displaying the
        # last write. Re-emit progress every ``HEARTBEAT_SECONDS``
        # with a slow incremental tick (0.10 → 0.80, never reaching
        # the real 0.85 "uploading" milestone) so the user can tell
        # the worker is still alive.
        HEARTBEAT_SECONDS = 15
        HEARTBEAT_INC = 0.003
        HEARTBEAT_MAX = 0.80
        heartbeat_stop = asyncio.Event()
        heartbeat_progress = {"value": 0.10}

        async def _heartbeat() -> None:
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    heartbeat_progress["value"] = min(HEARTBEAT_MAX, heartbeat_progress["value"] + HEARTBEAT_INC)
                    try:
                        await _on_progress("baking", heartbeat_progress["value"])
                    except Exception:
                        logger.exception("worker: heartbeat update failed")
                else:
                    return

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            bake = await loop.run_in_executor(
                None,
                functools.partial(
                    bake_fea_artefacts_from_source,
                    src_path,
                    bake_dir,
                    src_key=job.source_key,
                ),
            )
        except Exception as exc:
            logger.exception("worker: fea bake failed for %s", job.source_key)
            trace = tb_module.format_exc()
            heartbeat_stop.set()
            await heartbeat_task
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc))
            await _audit_done(
                db_pool,
                job_id,
                "error",
                str(exc),
                started_at,
                traceback=trace,
            )
            return
        finally:
            heartbeat_stop.set()
            if not heartbeat_task.done():
                try:
                    await heartbeat_task
                except Exception:
                    pass

        await _on_progress("uploading", 0.85)
        prefix = f"_derived/{job.source_key}.fea/"
        try:
            for produced in sorted(bake.out_dir.iterdir()):
                if not produced.is_file():
                    continue
                target_key = prefix + produced.name
                # Compression policy mirrors the API-side endpoint: gzip
                # only the manifest JSON. Field/edge/element ``.bin`` blobs
                # are stored *identity* — float32/int payloads barely
                # compress, and keeping them uncompressed lets the viewer
                # HTTP-Range a single step out of a multi-step field blob
                # (see the blobs route) instead of pulling every step.
                content_encoding = "gzip" if produced.suffix.lower() == ".json" else None
                await storage.put_bytes(
                    scope,
                    target_key,
                    produced.read_bytes(),
                    content_encoding=content_encoding,
                )
        except Exception as exc:
            logger.exception("worker: fea artefact upload failed for %s", job.source_key)
            trace = tb_module.format_exc()
            await queue.update(
                job_id,
                status=JOB_STATUS_ERROR,
                stage="upload",
                error=str(exc),
            )
            await _audit_done(
                db_pool,
                job_id,
                "error",
                str(exc),
                started_at,
                traceback=trace,
            )
            return

        await queue.update(
            job_id,
            status=JOB_STATUS_DONE,
            stage="ready",
            progress=1.0,
            error=None,
        )
        await _audit_done(db_pool, job_id, "done", None, started_at)
    finally:
        try:
            shutil.rmtree(bake_dir, ignore_errors=True)
        except Exception:
            pass


async def _run_fea_meta_compute(
    *,
    job: Job,
    src_path: pathlib.Path,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    _on_progress: Callable[[str, float], Awaitable[None]],
) -> None:
    """Compute the legacy FieldPickerModal step/field inventory.

    Sibling to the convert path; produces a small JSON that gets
    cached under ``_derived/<src>.meta.json`` (`fea_meta_key_for`).
    Source has already been streamed to ``src_path``. compute_fea_meta
    parses the SIF deck on a thread (the parse can be 30 s+ on a
    multi-hundred-MB deck).
    """

    job_id = job.job_id

    from .converter import compute_fea_meta

    await _on_progress("parsing", 0.20)
    loop = asyncio.get_running_loop()
    try:
        meta = await loop.run_in_executor(None, compute_fea_meta, src_path)
    except Exception as exc:
        logger.exception("worker: fea_meta compute failed for %s", job.source_key)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc))
        await _audit_done(
            db_pool,
            job_id,
            "error",
            str(exc),
            started_at,
            traceback=trace,
        )
        return

    await _on_progress("uploading", 0.90)
    import json as _json

    try:
        await storage.put_bytes(
            scope,
            job.derived_key,
            _json.dumps(meta).encode("utf-8"),
            content_encoding="gzip",
        )
    except Exception as exc:
        logger.exception("worker: fea_meta upload failed for %s", job.source_key)
        trace = tb_module.format_exc()
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="upload",
            error=str(exc),
        )
        await _audit_done(
            db_pool,
            job_id,
            "error",
            str(exc),
            started_at,
            traceback=trace,
        )
        return

    await queue.update(
        job_id,
        status=JOB_STATUS_DONE,
        stage="ready",
        progress=1.0,
        error=None,
    )
    await _audit_done(db_pool, job_id, "done", None, started_at)


def _parity_child(src_path, source_key, target_format, on_progress, *, produced=None):
    """``convert_fn``-shaped wrapper that runs the cross-format parity check and
    returns its result as JSON bytes.

    ``produced`` maps each compared format (step/ifc/xml/glb) to the local path of
    the blob the audit ALREADY produced+uploaded with the production strategy (or
    None when that conversion failed/was skipped). The check reads those blobs and
    compares a format-agnostic geometry invariant — it re-derives nothing, so it
    validates exactly what ships. It still tessellates step/ifc/xml to measure them;
    running through ``run_isolated_convert`` (this function in the forked child)
    means an OOM there is SIGKILLed by the per-job memory watchdog and fails the
    cell, rather than taking the whole worker pod down.

    Falls back to the offline re-derive path (``parity_for_source_file``) only when
    no produced blobs were passed — never the case on the audit worker."""
    import json as _json
    import pathlib as _pl

    from ada.cadit.visual_parity import (
        parity_for_source_file,
        parity_from_produced_files,
        parity_gxml_from_produced_files,
    )

    on_progress("parity", 0.2)
    if produced:
        pmap = {fmt: (_pl.Path(p) if p else None) for fmt, p in produced.items()}
        if str(source_key).lower().endswith(".xml"):
            # Genie-XML: cheap per-format COUNT comparison over the produced blobs (the
            # historical gxml invariant — catches a leg silently dropping N of M objects,
            # which a bbox gate can miss) — zero re-derivation, zero tessellation.
            res = parity_gxml_from_produced_files(source_key, pmap)
        else:
            res = parity_from_produced_files(source_key, pmap)
    else:
        res = parity_for_source_file(_pl.Path(src_path))
    on_progress("ready", 1.0)
    return _json.dumps(
        {
            "counts": res.counts,
            "expected": res.expected,
            "consistent": res.consistent,
            "mismatches": res.mismatches,
            "errors": res.errors,
            "skipped": res.skipped,
            "summary": res.summary(),
        }
    ).encode("utf-8")


async def _run_parity_validation(
    *,
    job: Job,
    src_path: pathlib.Path,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    _on_progress: Callable[[str, float], Awaitable[None]],
    timeout_s: float | None = None,
) -> None:
    """Cross-format visual-parity validation for one source (target_format=='parity').

    Reads the source's ALREADY-PRODUCED output blobs (step/ifc/xml/glb, converted +
    uploaded earlier in the run with the production strategy) and compares a
    format-agnostic GEOMETRY INVARIANT — surface area + bbox extent (see
    ada.cadit.visual_parity.parity_from_produced_files). It re-derives nothing, so
    it validates exactly what ships and does zero extra conversion; the parity cells
    are only enqueued after every conversion cell for the source has landed, so the
    blobs already exist. A format whose conversion failed/was skipped is recorded
    (its blob is absent), never re-derived. Produces no derived blob: the structured
    per-format result goes to the ``audit_parity`` table and the cell is audited
    done/error (a mismatch maps to ``error`` so it surfaces in the run's failed
    cells). Never raises.

    Runs in the same memory-capped forked child the convert path uses
    (``run_isolated_convert``): tessellating step/ifc/xml to measure them can spike
    RAM, and a blow-up must die in isolation (cell fails as OOM) rather than
    OOM-killing the worker pod.
    """
    import json

    from ada.cadit.visual_parity import PARITY_GEOMETRY_FORMATS

    from .converter import derived_key_for

    job_id = job.job_id
    suffix = pathlib.PurePosixPath(job.source_key).suffix.lower()

    async def _cancel_check() -> bool:
        if db_pool is None:
            return False
        try:
            return await db_module.audit_is_cancelled(db_pool, job_id)
        except Exception:
            return False

    # FEM sources take the produced-files geometry-invariant path: fetch each already-
    # produced output blob to a worker-local tempfile BEFORE forking (the child can't
    # reach async storage; the fork shares the filesystem, so the child reads these
    # paths). A missing blob (conversion failed/skipped) maps to None — recorded by
    # parity_from_produced_files, never re-derived. This is the fix: it validates what
    # actually ships (the analytic cylinder model) and does zero re-conversion, so it
    # no longer stalls on nvme write-contention writing ~1 GB of temp files.
    #
    # Genie-XML sources take a produced-files COUNT path (parity_gxml_from_produced_files):
    # the old re-derive (load + export via parity's own Python writers + reload) tripled
    # once curved shells thicken by default and dominated the sweep; every produced format
    # has a cheap counter instead (xml structure scan, ifc SPF line scan, native C++ step
    # stream index) so the whole check is seconds and validates exactly what shipped.
    #
    # Non-gxml CAD (STEP/IFC/SAT) sources keep the streaming/whole-model re-derive path
    # (produced left empty -> the child calls parity_for_source_file). Those were never
    # the hang; and their Genie-XML output is legitimately empty for a raw-solid source
    # (no Beam/Plate concept), which parity_for_source_file correctly SKIPS rather than
    # flagging as dropped geometry.
    _FEM_PARITY_SUFFIXES = (".fem", ".inp", ".sif", ".sin")
    _PRODUCED_PARITY_SUFFIXES = _FEM_PARITY_SUFFIXES + (".xml",)
    produced_dir = pathlib.Path(tempfile.mkdtemp(prefix="adapy-parity-"))
    produced: dict[str, str | None] = {}
    if suffix in _PRODUCED_PARITY_SUFFIXES:
        targets = set(ConverterRegistry.targets_for(suffix))
        compare_formats = tuple(f for f in PARITY_GEOMETRY_FORMATS if f in targets)
        for fmt in compare_formats:
            try:
                dkey = derived_key_for(job.source_key, fmt)
            except Exception:
                produced[fmt] = None
                continue
            dpath = produced_dir / f"produced.{fmt}"
            try:
                await storage.stream_to_path(scope, dkey, dpath)
                produced[fmt] = str(dpath)
            except FileNotFoundError:
                produced[fmt] = None
            except Exception:
                logger.exception("worker: parity fetch of produced %s failed for %s", fmt, job.source_key)
                produced[fmt] = None

    try:
        iresult: IsolatedConvertResult = await run_isolated_convert(
            _parity_child,
            src_path,
            job.source_key,
            "parity",
            convert_kwargs={"produced": produced},
            on_progress=_on_progress,
            timeout_s=timeout_s,
            cancel_check=_cancel_check,
        )
    except Exception as exc:
        logger.exception("worker: parity subprocess wrapper failed for %s", job.source_key)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="parity", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
        return
    finally:
        # The forked child has read the produced blobs by the time the call returns
        # (or raises); drop the fetched copies either way.
        shutil.rmtree(produced_dir, ignore_errors=True)

    metrics = dict(iresult.final_metrics)

    # User cancellation: the watchdog reaped the child; the row is already
    # 'cancelled' (set by the cancel endpoint) — don't flip it to error.
    if iresult.signal_name == "CANCELLED":
        try:
            await queue.update(job_id, status="cancelled", stage="parity", error="cancelled by user")
        except Exception:
            pass
        iresult.cleanup_output()
        return

    # OOM / timeout / crash / no-output: the child died (memory watchdog
    # SIGKILL, timeout, SIGSEGV) — surface as an error cell, pod intact.
    if iresult.exit_code != 0 or iresult.out_path is None:
        err = iresult.error or "parity subprocess produced no output"
        if iresult.signal_name:
            logger.warning("worker: parity child for %s ended via %s", job.source_key, iresult.signal_name)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="parity", error=err)
        await _audit_done(db_pool, job_id, "error", err, started_at, traceback=iresult.traceback, metrics=metrics)
        iresult.cleanup_output()
        return

    try:
        payload = json.loads(iresult.out_path.read_text())
    except Exception as exc:
        logger.exception("worker: parity result decode failed for %s", job.source_key)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="parity", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, metrics=metrics)
        iresult.cleanup_output()
        return
    iresult.cleanup_output()

    if db_pool is not None:
        try:
            await db_module.insert_audit_parity(
                db_pool,
                job_id=job_id,
                source_key=job.source_key,
                baseline=payload["expected"],
                counts=payload["counts"],
                consistent=payload["consistent"],
                mismatches=payload["mismatches"],
                errors=payload["errors"],
            )
        except Exception:
            logger.exception("worker: insert_audit_parity failed for %s", job.source_key)

    if payload["consistent"]:
        await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(db_pool, job_id, "done", None, started_at, metrics=metrics)
    else:
        msg = payload.get("summary") or "parity mismatch"
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="ready", progress=1.0, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, metrics=metrics)


# Cap the persisted compile log so a runaway (per-cell) warning storm can't
# balloon the blob; keep the TAIL (the end usually carries the failure).
_COMPILE_LOG_CAP_BYTES = 256 * 1024


class _CompileLogCapture(logging.Handler):
    """In-memory logging handler that buffers records emitted DURING a procedural
    compile so the worker can persist them as an inspectable ``.log`` blob.

    Thread-safe: the compile runs in an executor thread while the event loop keeps
    logging heartbeats on the main thread, so both may ``emit`` concurrently."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock:
            self._lines.append(line)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._lines)


@contextlib.contextmanager
def _capture_compile_logs():
    """Attach a :class:`_CompileLogCapture` to the ``ada`` logger (where the
    compile emits — it has ``propagate=False``) and the root logger (where an
    external engine's own logger propagates), forcing INFO level for the duration
    so INFO+ records are captured, then restoring everything on exit."""
    handler = _CompileLogCapture()
    ada_logger = logging.getLogger("ada")
    root_logger = logging.getLogger()
    targets = [ada_logger, root_logger]
    prev_levels = [(lg, lg.level) for lg in targets]
    for lg in targets:
        lg.addHandler(handler)
        # A logger only calls handlers for records at/above its effective level;
        # WARNING-defaulted loggers would drop the INFO messages we want.
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        for lg in targets:
            lg.removeHandler(handler)
        for lg, level in prev_levels:
            lg.setLevel(level)


def _assemble_compile_log(handler: _CompileLogCapture, stdout_buf: io.StringIO, extra: str | None) -> str:
    """Merge captured logging records, any compile stdout, and an optional extra
    section (a traceback on failure) into one bounded text blob (tail-capped)."""
    text = "\n".join(handler.snapshot())
    out = stdout_buf.getvalue().strip()
    if out:
        text = f"{text}\n" if text else ""
        text += f"{'-' * 8} stdout {'-' * 8}\n{out}"
    if extra:
        prefix = f"{text}\n" if text else ""
        text = f"{prefix}{'-' * 8} traceback {'-' * 8}\n{extra.strip()}"
    data = text.encode("utf-8")
    if len(data) > _COMPILE_LOG_CAP_BYTES:
        tail = data[-_COMPILE_LOG_CAP_BYTES:].decode("utf-8", errors="ignore")
        text = f"…[log truncated to last {_COMPILE_LOG_CAP_BYTES // 1024} KB]…\n{tail}"
    return text


def _compile_run_header(
    *,
    run_id: str,
    model_id: str | None,
    revision: object,
    engine: str | None,
    lod: str,
    detailing: str | None,
    is_preview: bool,
    status: str,
) -> str:
    """The banner every compile-run log opens with.

    It is what makes a run log SELF-IDENTIFYING: reading one you can tell which
    run produced it, what it compiled and how it ended — so a log that IS stale
    (an artifact served from cache, whose log belongs to the run that built it)
    announces itself instead of masquerading as the run you just triggered."""
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    what = "preview" if is_preview else f"r{revision}"
    bits = [
        f"run {run_id}",
        f"model {model_id}",
        what,
        f"lod={lod}",
        f"engine={engine or 'adapy-default'}",
    ]
    if detailing and detailing != "none":
        bits.append(f"detailing={detailing}")
    return f"=== compile {status} · {when} · " + " · ".join(bits) + " ==="


async def _put_run_log(storage: "Storage", scope, model_id: str | None, run_id: str, text: str) -> str | None:
    """Persist ONE compile run's log at its run-stamped key, returning that key.

    ALWAYS writes, even when the engine emitted nothing: an empty run still gets a
    blob carrying its banner. The old code skipped the write on empty text, which
    is precisely how a clean recompile left the previous run's failure sitting at
    the shared artifact-derived key for the panel to show. Best-effort — a log
    that fails to upload must not fail an otherwise-good compile."""
    if not model_id:
        return None
    try:
        from .procedural import procedural_run_log_key

        key = procedural_run_log_key(model_id, run_id)
    except ValueError:
        logger.warning("worker: refusing to write a compile log for unsafe run id %r", run_id)
        return None
    try:
        await storage.put_bytes(scope, key, text.encode("utf-8"), content_encoding="gzip")
        return key
    except Exception:
        logger.exception("worker: procedural compile-run log upload failed for %s", model_id)
        return None


async def _put_run_pointer(storage: "Storage", scope, derived_key: str, run_id: str) -> None:
    """Point an artifact key at the run that most recently targeted it (a ``.run``
    sibling — see procedural.procedural_run_pointer_key).

    Written when the run STARTS, so it is already in place for a run that fails
    before producing bytes; that failure's log is then what the viewer finds for
    the artifact, rather than the last SUCCESS's log. Best-effort: without the
    pointer the log lookup simply falls back to the legacy sibling."""
    try:
        from .procedural import procedural_run_pointer_key

        await storage.put_bytes(scope, procedural_run_pointer_key(derived_key), run_id.encode("utf-8"))
    except Exception:
        logger.warning("worker: failed to write run-pointer sidecar for %s", derived_key)


async def _prune_run_logs(storage: "Storage", scope, model_id: str | None, keep_key: str = "") -> None:
    """Drop all but the newest ``RUN_LOG_RETENTION`` run logs for one model.

    Run-keyed logs accumulate where the old artifact-keyed log overwrote itself, so
    the prefix needs a bound. Bounded listing (one model's ``runs/`` prefix only)
    and best-effort throughout: a pruning failure is never worth failing a compile
    over, and losing an old run's log only costs its admin audit row the Log tab."""
    if not model_id:
        return
    try:
        from .procedural import (
            RUN_LOG_RETENTION,
            procedural_run_dir,
            prune_run_log_keys,
        )

        entries = await storage.list_prefix(scope, procedural_run_dir(model_id))
        if len(entries) <= RUN_LOG_RETENTION:
            return
        # Newest first. last_modified is ISO-8601 (lexicographically sortable) when
        # the backend reports one; entries without it sort oldest so they go first.
        ordered = sorted(entries, key=lambda e: (e.last_modified or "", e.key), reverse=True)
        keys = [e.key for e in ordered if e.key != keep_key]
        # The run that just finished heads the list whatever the backend reported
        # for last_modified: the log the caller is about to be handed must survive.
        if keep_key:
            keys.insert(0, keep_key)
        for key in prune_run_log_keys(keys):
            try:
                await storage.delete(scope, key)
            except Exception:
                logger.debug("worker: could not prune stale compile-run log %s", key)
    except Exception:
        logger.warning("worker: compile-run log retention sweep failed for %s", model_id)


async def _write_catalog_fp_sidecar(storage: "Storage", scope, derived_key: str, opts: dict | None) -> None:
    """Record the catalog fingerprint a procedural artifact was built from, as a
    ``.catfp`` sibling of ``derived_key`` (see procedural.procedural_catalog_fp_key).
    The compile/preview/export endpoints read it back to decide whether a cached
    artifact is stale w.r.t. the live equipment/system catalogs. Best-effort and
    only for catalog-dependent models (the endpoint passes ``catalog_fingerprint``
    only when the model places catalog items); a write failure just means the next
    compile treats the cache as stale and rebuilds once."""
    fp = (opts or {}).get("catalog_fingerprint")
    if not fp:
        return
    try:
        from .procedural import procedural_catalog_fp_key

        await storage.put_bytes(scope, procedural_catalog_fp_key(derived_key), str(fp).encode("utf-8"))
    except Exception:
        logger.warning("worker: failed to write catalog-fp sidecar for %s", derived_key)


async def _run_procedural_build(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Compile a procedural cell model (postgres-stored doc) into a GLB.

    ``conversion_options`` carries ``{"model_id": ..., "revision": ...}``; the
    worker reads the doc straight from postgres (single source of truth) and
    errors on a revision mismatch so the revision-stamped derived_key always
    matches its content. The compile runs in-process via
    ``ada.topo_model.compile`` (pure adapy + tessellation)."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    revision = opts.get("revision")
    lod = "detail" if (opts.get("lod") or "sim") == "detail" else "sim"
    # Selected procedural engine (None / "adapy-default" = the built-in compile).
    engine = opts.get("engine")
    # Selected DETAILING engine — a fabrication-detail stage run in-process as
    # stage 2 of this same job, between the structural build and to_glb() (see
    # ada.topo_model.detailing). None/"none" = no detailing (byte-identical to the
    # plain structural build). Only the in-process builtin (adapy-default) is
    # applied here; an external (Tier-B) engine is a chained capability job (Phase 2).
    detailing = opts.get("detailing")
    # Per-joint-type detailing options (the Detailing tab's toggles + field values,
    # keyed by joint slug) threaded into the in-process detail() so a knob change
    # (weld leg, plate thickness, overhang, clearance …) actually alters geometry.
    detailing_options = opts.get("detailing_options") or {}
    # EXTERNAL detailing: when set, this structural stage runs NO in-process
    # detailing but ALSO serializes the compiled ada.Part to a neutral IFC artifact
    # (+ a per-Beam section sidecar) at the given keys, so the chained
    # ``procedural_detail`` job on the detailing engine's capability pool can read it.
    detailing_external = bool(opts.get("detailing_external"))
    structural_ifc_key = opts.get("structural_ifc_key")
    structural_sections_key = opts.get("structural_sections_key")
    # An ephemeral *preview* build carries the current (uncommitted) document
    # inline: compile THAT instead of the DB revision's doc, and skip the
    # revision-match check (a preview isn't tied to a persisted revision). The
    # model row is still loaded for its scope + name + catalog/CAD resolution.
    preview_doc = opts.get("preview_doc")
    is_preview = isinstance(preview_doc, dict)

    # ── This compile's RUN identity ─────────────────────────────────
    # The queue job id IS the run id. It is minted per compile ATTEMPT (where the
    # derived key is content-addressed and therefore shared by every attempt of an
    # unchanged input), it is the id the compile response already hands the
    # viewer, and it is the ``audit_log`` join key — so one id names this run's log
    # blob, the panel's current run, and the admin audit entry for it.
    run_log: dict[str, str] = {"key": "", "body": ""}

    async def _write_run_log(status: str, body: str | None = None) -> None:
        """(Re)write THIS run's log with the given outcome. Called on every exit
        path, so a run always leaves a log behind — including one that fails before
        the engine is ever entered, which used to leave the panel showing whatever
        the previous run wrote."""
        if body is not None:
            run_log["body"] = body
        header = _compile_run_header(
            run_id=job_id,
            model_id=model_id,
            revision=revision,
            engine=engine,
            lod=lod,
            detailing=detailing,
            is_preview=is_preview,
            status=status,
        )
        text = f"{header}\n{run_log['body']}" if run_log["body"] else header
        run_log["key"] = await _put_run_log(storage, scope, model_id, job_id, text) or ""

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        # Rewrite the run log with the failure banner, keeping whatever the engine
        # had already emitted — a failed run's log must stay retrievable and must
        # read as a FAILURE, distinguishable from the success that may follow it.
        await _write_run_log(f"failed at {stage}: {msg}")
        await _audit_done(
            db_pool,
            job_id,
            "error",
            msg,
            started_at,
            traceback=trace,
            metrics={"log_key": run_log["key"]} if run_log["key"] else None,
        )

    if not model_id or not isinstance(revision, int):
        await _fail("build", "conversion_options.model_id and revision are required for procedural_build")
        return
    # Claim the artifact key for this run BEFORE any work: a lookup that has only
    # the derived key to go on (a cache hit, or a run that dies before writing
    # bytes) then resolves to this run rather than to whichever ran last.
    await _put_run_pointer(storage, scope, job.derived_key, job_id)
    if db_pool is None:
        await _fail("build", "procedural build requires DATABASE_URL on the worker")
        return

    from . import db as db_module

    row = await db_module.get_procedural_model(db_pool, model_id)
    if row is None:
        await _fail("build", f"procedural model {model_id} not found")
        return
    if not is_preview and row["revision"] != revision:
        await _fail(
            "build",
            f"procedural model {model_id} is at revision {row['revision']}, job requested r{revision} — "
            "re-trigger compile for the current revision",
        )
        return
    # The document to compile: the inline preview doc, or the DB revision's doc.
    doc = preview_doc if is_preview else row["doc"]

    from ada.topo_model.engines import (
        BUILTIN_ENGINES,
        compile_with_engine,
        is_default_engine,
    )

    # A non-builtin engine selection is a registered (DB) engine: resolve its
    # manifest by slug to get the entrypoint. The engine's package is pre-installed
    # in this worker's capability image (that's why the job was routed here), so
    # no install happens — the entrypoint module is imported like any other.
    external_entrypoint: str | None = None
    if not is_default_engine(engine) and engine not in BUILTIN_ENGINES:
        eng_row = await db_module.get_procedural_engine_by_slug(
            db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"], slug=engine
        )
        eng_doc = ((eng_row or {}).get("doc") if eng_row else None) or _advertised_engine_doc(engine)
        if eng_doc is None:
            await _fail("build", f"procedural engine {engine!r} is neither registered in scope nor advertised here")
            return
        external_entrypoint = eng_doc.get("entrypoint")
        if not external_entrypoint:
            await _fail("build", f"engine {engine!r} manifest has no entrypoint")
            return

    # Full-fidelity source: a model imported from an external workbook carries its
    # original file (source_xlsx_key) so a non-default engine can compile the source
    # directly (all config the topology doc drops). Fetch it for the engine.
    source_xlsx: bytes | None = None
    source_key = doc.get("source_xlsx_key")
    if source_key and not is_default_engine(engine):
        try:
            source_xlsx = await storage.get_bytes(scope, source_key)
        except Exception:
            logger.warning("procedural: source workbook %s unreadable; compiling from the doc", source_key)

    # Resolve placed catalog equipment (by slug) to its per-scope definition.
    catalog = await db_module.get_equipment_docs_by_scope(
        db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
    )

    # When equipment_cad is on, prefetch the linked CAD assets for the catalog
    # slugs the model actually places, so the compiler can splice in real
    # geometry instead of boxes.
    cad_bytes: dict[str, tuple[bytes, str]] = {}
    if doc.get("equipment_cad"):
        used = {(e.get("DESCRIPTION") or "").strip() for e in (doc.get("equipments") or [])}
        cad_keys = await db_module.get_equipment_cad_keys_by_scope(
            db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
        )
        for slug, cad_key in cad_keys.items():
            if slug and slug in used and cad_key:
                try:
                    data = await storage.get_bytes(scope, cad_key)
                    cad_bytes[slug] = (data, pathlib.PurePosixPath(cad_key).suffix.lower())
                except Exception:
                    logger.warning("procedural: CAD asset %s for %r unreadable; using box", cad_key, slug)

    # The quantity take-off computed alongside a DEFAULT-engine compile (the
    # structured model is in-process there); persisted as a ``.stats.json`` sibling
    # of the GLB for the viewer's Stats panel. Non-default engines don't expose an
    # ada.Part here, so their stats stay absent and the panel degrades gracefully.
    takeoff_holder: dict[str, dict] = {}
    # For an EXTERNAL-detailing build the compiled ada.Part is captured here so it
    # can be serialized to the neutral structural artifact after the GLB upload.
    assembly_holder: dict[str, object] = {}

    def _do_compile() -> bytes:
        # A non-default engine gets the raw document through the uniform
        # ``compile(doc, **options)`` contract — catalog/CAD resolution is a
        # default-engine feature (it needs the DB), so it's skipped for others.
        # Built-in slugs (echo) dispatch by slug; a registered engine dispatches
        # via its manifest entrypoint (module:callable, resolved above).
        if not is_default_engine(engine):
            selector = engine if engine in BUILTIN_ENGINES else external_entrypoint
            # source_xlsx (when the model stored its workbook) drives the engine's
            # full-fidelity path; compile_with_engine passes only the kwargs the
            # engine accepts, so a doc-only engine ignores it.
            return compile_with_engine(selector, doc, name=row["name"], lod=lod, source_xlsx=source_xlsx)
        cad_meshes = {}
        for slug, (data, ext) in cad_bytes.items():
            # Honor the type's Z-up assumption so the spliced geometry lands in
            # the same frame the bbox was inferred in (default True = verbatim).
            z_up = bool((catalog.get(slug) or {}).get("cad_z_up", True))
            try:
                cad_meshes[slug] = _load_cad_mesh(data, ext, z_up=z_up)
            except Exception:
                logger.warning("procedural: failed to load CAD mesh for %r; using box", slug)
        # The user-selected structural blueprint rides on the document
        # (``blueprint_name``, out of the whitelisted ``blueprint`` options); an
        # unset/unknown name falls back to ``steel_stru`` for backward compat.
        bp_name = doc.get("blueprint_name")
        blueprint_name = bp_name if bp_name in ("steel_stru", "none") else "steel_stru"
        # The in-process detailing engine runs as stage 2 inside the builder
        # (right where the old girder-joint pass ran, before to_glb()). Only a
        # builtin detailing slug is applied here; None/"none"/external names add
        # nothing (external = a Phase-2 chained capability job).
        from ada.topo_model.detailing_catalog import detailing_engine_specs

        builtin_detailing = {s["slug"] for s in detailing_engine_specs() if s.get("inprocess")}
        detailing_arg = detailing if detailing in builtin_detailing else None
        # An external-detailing build keeps the live ada.Part so it can be
        # serialized to the neutral structural artifact after tessellation.
        if detailing_external:
            from ada.topo_model.compile import compile_procedural_doc_with_assembly

            glb_bytes, stats, assembly = compile_procedural_doc_with_assembly(
                doc,
                name=row["name"],
                blueprint_name=blueprint_name,
                equipment_resolver=catalog.get,
                cad_scene_resolver=cad_meshes.get,
                lod=lod,
                detailing=None,
            )
            takeoff_holder["stats"] = stats
            assembly_holder["assembly"] = assembly
            return glb_bytes
        from ada.topo_model.compile import compile_procedural_doc_with_takeoff

        glb_bytes, stats = compile_procedural_doc_with_takeoff(
            doc,
            name=row["name"],
            blueprint_name=blueprint_name,
            equipment_resolver=catalog.get,
            cad_scene_resolver=cad_meshes.get,
            lod=lod,
            detailing=detailing_arg,
            detailing_options=detailing_options,
        )
        takeoff_holder["stats"] = stats
        return glb_bytes

    loop = asyncio.get_running_loop()
    # Capture the engine's logging (and stdout) DURING the compile so the messages
    # are inspectable from the viewer — persisted under THIS RUN's key
    # (procedural_run_log_key) on BOTH success and failure so errors stay
    # diagnosable and no run can ever be handed another run's output.
    stdout_buf = io.StringIO()

    def _do_compile_captured() -> bytes:
        with contextlib.redirect_stdout(stdout_buf):
            return _do_compile()

    with _capture_compile_logs() as log_handler:
        try:
            await queue.update(job_id, stage="build", progress=0.40)
            glb_bytes = await loop.run_in_executor(None, _do_compile_captured)
        except Exception as exc:
            logger.exception("worker: procedural_build failed for %s", model_id)
            await _write_run_log(
                "failed at build", _assemble_compile_log(log_handler, stdout_buf, tb_module.format_exc())
            )
            await _fail("build", str(exc), tb_module.format_exc())
            return
        # Unconditional: an engine that logged nothing still gets a blob (its banner
        # alone), so the next reader sees THIS run and not a leftover from an older one.
        await _write_run_log("ok", _assemble_compile_log(log_handler, stdout_buf, None))

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, glb_bytes, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: procedural_build upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    # Bind this artifact to the catalog state it was built from, so a later catalog
    # edit invalidates the (revision/doc-hash-stamped) cache and forces a recompile.
    await _write_catalog_fp_sidecar(storage, scope, job.derived_key, job.conversion_options)

    # Take-off stats sidecar (default-engine builds only): a gzip-at-rest
    # ``.stats.json`` sibling of the GLB (procedural_stats_key). Best-effort — a
    # failure here must not fail an otherwise-good compile; the panel degrades.
    stats = takeoff_holder.get("stats")
    if stats is not None:
        try:
            import json as _json

            from .procedural import procedural_stats_key

            await storage.put_bytes(
                scope,
                procedural_stats_key(job.derived_key),
                _json.dumps(stats).encode("utf-8"),
                content_encoding="gzip",
            )
        except Exception:
            logger.exception("worker: procedural_build stats sidecar upload failed for %s", model_id)

    # EXTERNAL detailing: serialize the compiled ada.Part to the neutral structural
    # artifact (IFC bytes) + a per-Beam section sidecar the chained procedural_detail
    # job reads. A hard failure here (unlike the best-effort stats sidecar): the
    # external pipeline can't proceed without the artifact, so surface it.
    assembly = assembly_holder.get("assembly")
    if detailing_external and assembly is not None and structural_ifc_key and structural_sections_key:
        try:
            import json as _json

            await queue.update(job_id, stage="artifact", progress=0.95)
            ifc_bytes, sections = await loop.run_in_executor(None, _serialize_structural_artifact, assembly)
            await storage.put_bytes(scope, structural_ifc_key, ifc_bytes, content_encoding="gzip")
            await storage.put_bytes(
                scope, structural_sections_key, _json.dumps(sections).encode("utf-8"), content_encoding="gzip"
            )
        except Exception as exc:
            logger.exception("worker: procedural_build structural artifact failed for %s", model_id)
            await _fail("artifact", str(exc), tb_module.format_exc())
            return

    await _prune_run_logs(storage, scope, model_id, run_log["key"])
    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    # Hand the run's log to the audit row, so the admin panel's existing per-row
    # "Log" tab (GET /admin/audit/{id}/log) serves a compile exactly as it serves
    # a conversion — no parallel surface for procedural runs.
    await _audit_done(
        db_pool,
        job_id,
        "done",
        None,
        started_at,
        metrics={"log_key": run_log["key"]} if run_log["key"] else None,
    )


def _serialize_structural_artifact(assembly) -> "tuple[bytes, dict]":
    """Serialize a compiled structural ``ada.Part`` to the NEUTRAL artifact an
    external (Tier-B) detailing engine consumes: IFC bytes + a per-Beam section
    sidecar ``{member_name: {"section_type": <BOX/…>, "section_props": {...}}}``.

    The sidecar is authoritative for section-type detection (a consumer matches on
    ``section.type.value.upper()``) so a consumer never has to re-derive
    it from a potentially lossy IFC round-trip. ``section_props`` carries the
    numeric geometry (``h``/``w_top``/``t_w``/``r``/``wt``/…) present on the section."""
    import ada
    from ada.api.beams import Beam

    sections: dict[str, dict] = {}
    for bm in assembly.get_all_physical_objects(by_type=Beam):
        sec = bm.section
        props = {
            "name": sec.name,
            "h": sec.h,
            "w_top": sec.w_top,
            "w_btn": sec.w_btn,
            "t_w": sec.t_w,
            "t_ftop": sec.t_ftop,
            "t_fbtn": sec.t_fbtn,
            "r": sec.r,
            "wt": sec.wt,
        }
        sections[bm.name] = {
            "section_type": sec.type.value,
            "section_props": {k: v for k, v in props.items() if v is not None},
        }

    fd, tmp_name = tempfile.mkstemp(suffix=".ifc")
    os.close(fd)
    tmp_path = pathlib.Path(tmp_name)
    try:
        # In-memory ifcopenshell writer (no OCC): the freshly built concept objects
        # emit analytic profiles/solids straight to SPF. file_obj_only would keep it
        # in RAM but we need bytes on disk to read back uniformly.
        if not isinstance(assembly, ada.Assembly):
            assembly = ada.Assembly("StructuralArtifact") / assembly
        assembly.to_ifc(tmp_path, file_obj_only=False)
        return tmp_path.read_bytes(), sections
    finally:
        tmp_path.unlink(missing_ok=True)


async def _run_procedural_detail(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Chained EXTERNAL (Tier-B) detailing stage — the sibling of
    :func:`_run_procedural_build` that runs on a foreign capability pool.

    Reads the neutral structural artifact (IFC bytes) + its section sidecar the
    structural build wrote, resolves the external detailing engine's ``module:callable``
    entrypoint via :func:`ada.topo_model.engines.load_entrypoint` (the same mechanism
    the procedural engines use), and calls ``entrypoint(model_bytes, options)`` where
    ``options`` carries ``{"sections": <sidecar dict>, ...joint options}``. The returned
    detailing-layer GLB is written to ``job.derived_key`` gzip-at-rest."""
    import json as _json

    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    entrypoint = opts.get("detailing_entrypoint")
    structural_ifc_key = opts.get("structural_ifc_key")
    structural_sections_key = opts.get("structural_sections_key")
    lod = "detail" if (opts.get("lod") or "sim") == "detail" else "sim"

    # This stage owns ``derived_key``, so it is the run the viewer's log lookup for
    # that artifact must find — same run identity (the job id) as the structural
    # stage, just a different key.
    run_log: dict[str, str] = {"key": "", "body": ""}

    async def _write_run_log(status: str, body: str | None = None) -> None:
        if body is not None:
            run_log["body"] = body
        header = _compile_run_header(
            run_id=job_id,
            model_id=model_id,
            revision=opts.get("revision"),
            engine=opts.get("engine"),
            lod=lod,
            detailing=opts.get("detailing"),
            is_preview=False,
            status=status,
        )
        text = f"{header}\n{run_log['body']}" if run_log["body"] else header
        run_log["key"] = await _put_run_log(storage, scope, model_id, job_id, text) or ""

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _write_run_log(f"failed at {stage}: {msg}")
        await _audit_done(
            db_pool,
            job_id,
            "error",
            msg,
            started_at,
            traceback=trace,
            metrics={"log_key": run_log["key"]} if run_log["key"] else None,
        )

    await _put_run_pointer(storage, scope, job.derived_key, job_id)

    if not entrypoint or ":" not in str(entrypoint):
        await _fail(
            "detail", "conversion_options.detailing_entrypoint (module:callable) is required for procedural_detail"
        )
        return
    if not structural_ifc_key:
        await _fail("detail", "conversion_options.structural_ifc_key is required for procedural_detail")
        return

    # The structural build runs on a DIFFERENT pool with NO ordering guarantee
    # relative to this job — this pool can claim procedural_detail before the
    # structural stage has written the neutral artifact. So WAIT (bounded) for the
    # IFC artifact to appear rather than failing on the first miss; only error if
    # it never shows within the budget (a genuinely failed / absent structural build).
    if not await _wait_for_blob(
        storage,
        scope,
        structural_ifc_key,
        queue=queue,
        job_id=job_id,
        budget_s=STRUCTURAL_ARTIFACT_WAIT_BUDGET_S,
        waiting_stage="waiting for structural build…",
    ):
        # A shutdown mid-wait leaves the job for redelivery (not a hard error);
        # a real timeout is a failure the operator should see.
        if _WORKER_STOP is not None and _WORKER_STOP.is_set():
            logger.info("worker: procedural_detail %s interrupted by shutdown while waiting", job_id)
            return
        await _fail(
            "fetch",
            f"structural artifact {structural_ifc_key!r} did not appear within "
            f"{STRUCTURAL_ARTIFACT_WAIT_BUDGET_S:.0f}s — the structural build may have failed",
        )
        return

    try:
        await queue.update(job_id, stage="fetch", progress=0.20)
        model_bytes = await storage.get_bytes(scope, structural_ifc_key)
    except Exception as exc:
        await _fail("fetch", f"structural artifact {structural_ifc_key!r} unreadable: {exc}")
        return

    sections: dict = {}
    if structural_sections_key:
        # The sidecar is written by the same build moments after the IFC; give it a
        # short grace to appear, then degrade to an empty sidecar (best-effort).
        await _wait_for_blob(
            storage,
            scope,
            structural_sections_key,
            queue=queue,
            job_id=job_id,
            budget_s=STRUCTURAL_SECTIONS_WAIT_BUDGET_S,
        )
        try:
            sections = _json.loads(await storage.get_bytes(scope, structural_sections_key))
        except Exception:
            logger.warning("procedural_detail: section sidecar %s unreadable; passing empty", structural_sections_key)

    from ada.topo_model.engines import load_entrypoint

    # Per-joint options selected in the UI ride on the job; the section sidecar is
    # merged in under a reserved key so the engine can guarantee section detection.
    options = {"sections": sections, "lod": lod}
    for k, v in (opts.get("detailing_options") or {}).items():
        options[k] = v

    loop = asyncio.get_running_loop()
    stdout_buf = io.StringIO()

    def _do_detail() -> bytes:
        fn = load_entrypoint(entrypoint)
        with contextlib.redirect_stdout(stdout_buf):
            return fn(model_bytes, options)

    with _capture_compile_logs() as log_handler:
        try:
            await queue.update(job_id, stage="detail", progress=0.50)
            glb_bytes = await loop.run_in_executor(None, _do_detail)
        except Exception as exc:
            logger.exception("worker: procedural_detail failed for %s", model_id)
            await _write_run_log(
                "failed at detail", _assemble_compile_log(log_handler, stdout_buf, tb_module.format_exc())
            )
            await _fail("detail", str(exc), tb_module.format_exc())
            return
        await _write_run_log("ok", _assemble_compile_log(log_handler, stdout_buf, None))

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, glb_bytes, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: procedural_detail upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    # The external detail output rides on the same catalog state as its structural
    # stage; stamp its own sidecar so the endpoint's staleness check on derived_key works.
    await _write_catalog_fp_sidecar(storage, scope, job.derived_key, job.conversion_options)

    await _prune_run_logs(storage, scope, model_id, run_log["key"])
    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(
        db_pool,
        job_id,
        "done",
        None,
        started_at,
        metrics={"log_key": run_log["key"]} if run_log["key"] else None,
    )


async def _run_procedural_relocations(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Propose the minimum set of equipment relocations that make a procedural
    model's runs route cleanly (see :func:`ada.topo_model.relocate.propose_relocations`).

    A synthetic sibling of :func:`_run_procedural_build`: it reads the same
    postgres-stored doc (resolving placed catalog equipment by slug the same way)
    but produces a JSON *proposal* document rather than a GLB. The result is
    stored gzip-at-rest under the model's ``relocations.json`` derived key so the
    frontend can poll the job then fetch the blob. Relocations are proposals only —
    the worker never mutates the model."""
    import json

    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    revision = opts.get("revision")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not model_id or not isinstance(revision, int):
        await _fail("relocate", "conversion_options.model_id and revision are required for procedural_relocations")
        return
    if db_pool is None:
        await _fail("relocate", "procedural relocations require DATABASE_URL on the worker")
        return

    from . import db as db_module

    row = await db_module.get_procedural_model(db_pool, model_id)
    if row is None:
        await _fail("relocate", f"procedural model {model_id} not found")
        return
    if row["revision"] != revision:
        await _fail(
            "relocate",
            f"procedural model {model_id} is at revision {row['revision']}, job requested r{revision} — "
            "re-trigger propose-relocations for the current revision",
        )
        return

    from ada.topo_model.relocate import propose_relocations

    # Resolve placed catalog equipment (by slug) to its per-scope definition, so a
    # candidate move keeps the equipment's real bbox/ports (matching the compile).
    catalog = await db_module.get_equipment_docs_by_scope(
        db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
    )

    def _do_propose() -> bytes:
        result = propose_relocations(row["doc"], equipment_resolver=catalog.get)
        return json.dumps(result).encode("utf-8")

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="relocate", progress=0.40)
        payload = await loop.run_in_executor(None, _do_propose)
    except Exception as exc:
        logger.exception("worker: procedural_relocations failed for %s", model_id)
        await _fail("relocate", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, payload, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: procedural_relocations upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


def _advertised_engine_doc(engine: str | None) -> dict | None:
    """A manifest-shaped doc for an engine THIS worker advertises itself.

    A self-advertising engine deliberately has no database row -- that is the
    whole point of advertising -- so its manifest has to come from the registry
    the engine populated at import (see ADA_WORKER_PRELOAD and
    ``register_procedural_engine_capabilities``). Returns the same
    ``{kind, entrypoint, worker_capability}`` shape a row's ``doc`` carries, so
    every caller needs nothing beyond the fallback itself.

    Only offerable specs qualify: a spec carrying capability flags alone
    describes an engine the viewer already knows about, not one this worker can
    dispatch to on its own.
    """
    if not engine:
        return None
    from ada.topo_model.engine_catalog import is_offerable, procedural_engine_specs

    for spec in procedural_engine_specs():
        if spec.get("slug") == engine and is_offerable(spec):
            doc: dict = {"kind": "server", "entrypoint": spec["entrypoint"]}
            if spec.get("worker_capability"):
                doc["worker_capability"] = spec["worker_capability"]
            return doc
    return None


async def _resolve_engine_manifest(db_pool: "asyncpg.Pool", row: dict, engine: str | None) -> dict | None:
    """The registry manifest doc for a NON-default, non-builtin engine (its
    ``entrypoint``/``worker_capability``/xlsx-sibling fields), resolved by slug in
    the model's scope. ``None`` for the default/built-in engines."""
    from ada.topo_model.engines import BUILTIN_ENGINES, is_default_engine

    if is_default_engine(engine) or engine in BUILTIN_ENGINES:
        return None
    eng_row = await db_module.get_procedural_engine_by_slug(
        db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"], slug=engine
    )
    # A row wins when present -- it is an explicit admin registration and may
    # pin a different entrypoint than whatever this pod happens to run.
    return ((eng_row or {}).get("doc") if eng_row else None) or _advertised_engine_doc(engine)


async def _run_procedural_export_xlsx(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Export a procedural model (postgres-stored doc) to its engine's Excel
    workbook (bytes), stamped with the ``_ADA_META`` sheet, and store it at
    ``job.derived_key`` (an ``.xlsx`` blob the frontend downloads). A synthetic
    sibling of :func:`_run_procedural_build`, routed to the engine's capability."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    revision = opts.get("revision")
    engine = opts.get("engine")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not model_id or not isinstance(revision, int):
        await _fail("export", "conversion_options.model_id and revision are required for procedural_export_xlsx")
        return
    if db_pool is None:
        await _fail("export", "procedural export requires DATABASE_URL on the worker")
        return

    row = await db_module.get_procedural_model(db_pool, model_id)
    if row is None:
        await _fail("export", f"procedural model {model_id} not found")
        return
    if row["revision"] != revision:
        await _fail(
            "export",
            f"procedural model {model_id} is at revision {row['revision']}, job requested r{revision}",
        )
        return

    manifest_doc = await _resolve_engine_manifest(db_pool, row, engine)
    doc = row["doc"]

    from ada.topo_model.engines import EngineHasNoExcelFormat, export_doc_to_xlsx

    def _do_export() -> bytes:
        return export_doc_to_xlsx(engine, doc, name=row["name"], manifest_doc=manifest_doc)

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="export", progress=0.40)
        xlsx_bytes = await loop.run_in_executor(None, _do_export)
    except EngineHasNoExcelFormat as exc:
        await _fail("export", str(exc))
        return
    except Exception as exc:
        logger.exception("worker: procedural_export_xlsx failed for %s", model_id)
        await _fail("export", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # An xlsx is an already-compressed zip — store identity (no gzip re-encode),
        # so the presigned/blob GET hands the browser a valid .xlsx download.
        await storage.put_bytes(scope, job.derived_key, xlsx_bytes)
    except Exception as exc:
        logger.exception("worker: procedural_export_xlsx upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


async def _run_procedural_export_model(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Export a procedural model to a downloadable CAD/analysis file: ``ifc`` (the
    DETAIL model — the clash cuts ride along as IfcRelVoidsElement voids, equipment
    as IfcPump/IfcTank/…) or ``gxml`` (the SIMULATION model as a Genie concept XML).

    Compiles the postgres-stored doc to an in-process adapy assembly (built-in
    engine only) at the format's LOD, serializes it, and stores the bytes at
    ``job.derived_key``. A synthetic sibling of :func:`_run_procedural_export_xlsx`."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    revision = opts.get("revision")
    export_format = (opts.get("export_format") or "").lower()
    lod = "detail" if (opts.get("lod") or "sim") == "detail" else "sim"
    detailing = opts.get("detailing")
    # IFC only: splice real catalog CAD geometry for equipment (default on). The
    # Genie export keeps equipment as its concept type (prism_shape), so it never
    # splices CAD — equipment there stays an ada.Equipment carrying mass/footprint.
    cad_equipment = export_format == "ifc" and bool(opts.get("cad_equipment", True))

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not model_id or not isinstance(revision, int):
        await _fail("export", "conversion_options.model_id and revision are required for procedural_export_model")
        return
    if export_format not in ("ifc", "gxml"):
        await _fail("export", f"unsupported export_format {export_format!r} (expected ifc or gxml)")
        return
    if db_pool is None:
        await _fail("export", "procedural export requires DATABASE_URL on the worker")
        return

    row = await db_module.get_procedural_model(db_pool, model_id)
    if row is None:
        await _fail("export", f"procedural model {model_id} not found")
        return
    if row["revision"] != revision:
        await _fail(
            "export", f"procedural model {model_id} is at revision {row['revision']}, job requested r{revision}"
        )
        return

    doc = row["doc"]
    name = row["name"]

    # Resolve placed catalog equipment (by slug) to its per-scope definition so the
    # equipment is faithful — an ada.Equipment with the catalog's bbox/mass/ports/IFC
    # class (IfcPump/IfcTank/…) and a Genie prism_shape — instead of an anonymous box.
    catalog = await db_module.get_equipment_docs_by_scope(
        db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
    )
    # IFC + CAD-on: prefetch the linked CAD assets for the placed slugs so the
    # compiler can splice real geometry in place of the placeholder box body.
    cad_bytes: dict[str, tuple[bytes, str, bool]] = {}
    if cad_equipment:
        used = {(e.get("DESCRIPTION") or "").strip() for e in (doc.get("equipments") or [])}
        cad_keys = await db_module.get_equipment_cad_keys_by_scope(
            db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
        )
        for slug, cad_key in cad_keys.items():
            if slug and slug in used and cad_key:
                try:
                    data = await storage.get_bytes(scope, cad_key)
                    z_up = bool((catalog.get(slug) or {}).get("cad_z_up", True))
                    cad_bytes[slug] = (data, pathlib.PurePosixPath(cad_key).suffix.lower(), z_up)
                except Exception:
                    logger.warning("procedural export: CAD asset %s for %r unreadable; using box", cad_key, slug)

    def _do_export() -> bytes:
        import os
        import tempfile

        from ada.topo_model.compile import build_procedural_assembly

        # Splice CAD only when asked (IFC). ``equipment_cad`` on the doc drives the
        # compiler's box-vs-CAD choice; force it to match this export's option so a
        # download reflects the toggle rather than the model's stored preference.
        export_doc = {**doc, "equipment_cad": bool(cad_equipment and cad_bytes)}
        cad_meshes: dict[str, object] = {}
        for slug, (data, ext, z_up) in cad_bytes.items():
            try:
                cad_meshes[slug] = _load_cad_mesh(data, ext, z_up=z_up)
            except Exception:
                logger.warning("procedural export: failed to load CAD mesh for %r; using box", slug)

        # Built-in engine only: build the in-process ada.Assembly the IFC / Genie
        # writers need (no GLB — this path never tessellates). The equipment resolver
        # makes catalog equipment faithful; cad_as_objects materialises resolved CAD
        # equipment as real assembly geometry (IfcTriangulatedFaceSet) rather than a
        # GLB-only splice, so it serializes into the IFC.
        asm = build_procedural_assembly(
            export_doc,
            name=name,
            lod=lod,
            detailing=detailing if export_format == "ifc" else None,
            equipment_resolver=catalog.get,
            cad_scene_resolver=cad_meshes.get if cad_meshes else None,
            cad_as_objects=bool(cad_meshes),
        )
        with tempfile.TemporaryDirectory() as d:
            if export_format == "ifc":
                p = os.path.join(d, "model.ifc")
                asm.to_ifc(p, file_obj_only=False)
            else:
                p = os.path.join(d, "model.gxml")
                # Equipment defaults to AS_IS (which the Genie writer skips); promote
                # each to FOOTPRINT_MASS so it exports as a Genie equipment concept
                # (prism_shape + placed load) rather than being dropped.
                from ada.api.spatial.eq_types import EquipRepr
                from ada.api.spatial.equipment import Equipment

                for part in asm.get_all_parts_in_assembly(include_self=True):
                    if isinstance(part, Equipment) and part.eq_repr == EquipRepr.AS_IS:
                        part.eq_repr = EquipRepr.FOOTPRINT_MASS
                # embed_sat=False keeps the export CAD-backend-independent (plates as
                # polygons; Genie rebuilds the ACIS on import).
                asm.to_genie_xml(p, embed_sat=False)
            with open(p, "rb") as fh:
                return fh.read()

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="export", progress=0.40)
        data = await loop.run_in_executor(None, _do_export)
    except Exception as exc:
        logger.exception("worker: procedural_export_model (%s) failed for %s", export_format, model_id)
        await _fail("export", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # Store identity (not gzip-at-rest) so the presigned/blob GET hands the
        # browser a directly-usable .ifc / .gxml text file.
        await storage.put_bytes(scope, job.derived_key, data)
    except Exception as exc:
        logger.exception("worker: procedural_export_model upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    # Bind the export to the catalog state it resolved equipment from.
    await _write_catalog_fp_sidecar(storage, scope, job.derived_key, job.conversion_options)

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


async def _run_procedural_import_xlsx(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Import an uploaded Excel workbook into a NEW procedural model.

    ``conversion_options`` carries ``{source_key, engine, name, created_by}``; the
    engine (chosen from the ``_ADA_META`` sheet or the user's prompt) parses the
    workbook into a procedural document, which is committed as a fresh model. The
    original workbook is kept as the model's ``source_xlsx_key`` (full-fidelity
    source). A small JSON result ``{model_id, name, engine, revision}`` is written
    to ``job.derived_key`` so the frontend can open the new model."""
    import json

    job_id = job.job_id
    opts = job.conversion_options or {}
    source_key = opts.get("source_key")
    engine = opts.get("engine")
    name = (opts.get("name") or "").strip() or "Imported model"
    created_by = opts.get("created_by")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not source_key:
        await _fail("import", "conversion_options.source_key is required for procedural_import_xlsx")
        return
    if db_pool is None:
        await _fail("import", "procedural import requires DATABASE_URL on the worker")
        return

    try:
        xlsx_bytes = await storage.get_bytes(scope, source_key)
    except Exception as exc:
        await _fail("import", f"uploaded workbook {source_key!r} unreadable: {exc}")
        return

    # A NON-default, non-builtin engine's manifest is resolved by slug in this
    # scope (mirrors export/build) — needed to locate its import entrypoint.
    from ada.topo_model.engines import BUILTIN_ENGINES, is_default_engine

    manifest_doc = None
    if not is_default_engine(engine) and engine not in BUILTIN_ENGINES:
        eng_row = await db_module.get_procedural_engine_by_slug(
            db_pool, scope_kind=scope.kind, scope_id=scope.id, slug=engine
        )
        manifest_doc = ((eng_row or {}).get("doc") if eng_row else None) or _advertised_engine_doc(engine)
        if manifest_doc is None:
            await _fail("import", f"procedural engine {engine!r} is neither registered in scope nor advertised here")
            return

    from ada.comms.rest.procedural import procedural_source_key, validate_doc
    from ada.topo_model.engines import (
        DEFAULT_ENGINE_SLUG,
        EngineHasNoExcelFormat,
        import_xlsx_to_doc,
    )

    def _do_import() -> dict:
        parsed = import_xlsx_to_doc(engine, xlsx_bytes, manifest_doc=manifest_doc)
        # Stamp the routing header so a subsequent compile auto-routes back to this
        # engine, then validate/normalize through the same path the commit uses.
        parsed["engine"] = engine or DEFAULT_ENGINE_SLUG
        return validate_doc(parsed)

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="import", progress=0.40)
        doc = await loop.run_in_executor(None, _do_import)
    except EngineHasNoExcelFormat as exc:
        await _fail("import", str(exc))
        return
    except Exception as exc:
        logger.exception("worker: procedural_import_xlsx parse failed for %s", source_key)
        await _fail("import", str(exc), tb_module.format_exc())
        return

    # Create the model row, then stash the original workbook as its full-fidelity
    # source and commit the parsed doc (revision 0 -> 1).
    model_row = await db_module.create_procedural_model(
        db_pool, scope_kind=scope.kind, scope_id=scope.id, name=name, created_by=created_by
    )
    if model_row is None:
        await _fail("import", f"a procedural model named {name!r} already exists in this scope")
        return
    model_id = model_row["id"]

    src_key = procedural_source_key(model_id)
    try:
        await storage.put_bytes(scope, src_key, xlsx_bytes)
        doc["source_xlsx_key"] = src_key
    except Exception:
        logger.warning("worker: import could not stash source workbook for %s", model_id)

    new_rev = await db_module.update_procedural_model_doc(db_pool, model_id, doc, model_row["revision"])
    if new_rev is None:
        await _fail("import", f"failed to commit imported doc for model {model_id}")
        return

    payload = json.dumps({"model_id": model_id, "name": name, "engine": doc.get("engine"), "revision": new_rev}).encode(
        "utf-8"
    )
    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, payload, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: procedural_import_xlsx result upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


def _infer_equipment_geometry(data: bytes, ext: str, z_up: bool = True) -> tuple[dict, bytes]:
    """Read a CAD/mesh asset, returning its axis-aligned bounding-box extents
    ``{lx, ly, lz}`` (in metres) and a preview GLB for the sidecar viewer. Mesh
    formats load via trimesh; CAD formats via the matching ada reader.

    ``z_up=True`` (default) takes the asset as authored in adapy's **Z-up**
    convention (ada readers and ada-exported GLBs are Z-up): ``lz`` = the Z extent
    = height and the mesh is NOT re-oriented — measuring/previewing it verbatim
    keeps lz == the CAD's real vertical extent. ``z_up=False`` treats a mesh asset
    (.glb/.gltf/.stl/.obj) as glTF-spec **Y-up** and re-orients it Y-up→Z-up
    (rotate +90° about X) before measuring and previewing, so the inferred bbox
    and the preview GLB are both in adapy's Z-up frame. ``z_up`` is ignored for
    ada-reader formats (already Z-up)."""
    import pathlib as _pl
    import tempfile as _tf

    ext = ext.lower()
    with _tf.TemporaryDirectory(prefix="eqbbox_") as tmp:
        src = _pl.Path(tmp) / f"source{ext}"
        src.write_bytes(data)
        if ext in (".glb", ".gltf", ".stl", ".obj"):
            import numpy as np
            import trimesh

            scene = trimesh.load(src, force="scene")
            if not z_up:
                # Re-orient a Y-up authored mesh into adapy's Z-up frame; both the
                # measured bounds and the exported preview then match Z-up.
                scene.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
            bounds = scene.bounds
            if z_up and ext in (".glb", ".gltf"):
                preview = data
            else:
                preview = scene.export(file_type="glb")
        else:
            import ada

            readers = {
                ".step": ada.from_step,
                ".stp": ada.from_step,
                ".ifc": ada.from_ifc,
                ".sat": ada.from_acis,
                ".xml": ada.from_genie_xml,
            }
            reader = readers.get(ext)
            if reader is None:
                raise ValueError(f"unsupported CAD extension {ext!r} for bbox inference")
            a = reader(src)
            bounds = a.to_trimesh_scene().bounds
            out = _pl.Path(tmp) / "preview.glb"
            a.to_gltf(out)
            preview = out.read_bytes()

    if bounds is None:
        raise ValueError("could not determine geometry bounds (empty model?)")
    lo, hi = bounds[0], bounds[1]
    bbox = {"lx": float(hi[0] - lo[0]), "ly": float(hi[1] - lo[1]), "lz": float(hi[2] - lo[2])}
    return bbox, preview


def _load_cad_mesh(data: bytes, ext: str, z_up: bool = True):
    """Load a CAD/mesh asset into a single concatenated trimesh (graph
    transforms baked). Used to splice real equipment geometry into a compiled
    procedural model.

    ``z_up=True`` (default) takes the asset verbatim (adapy Z-up convention).
    ``z_up=False`` re-orients a mesh asset (.glb/.gltf/.stl/.obj) from glTF-spec
    Y-up into Z-up (rotate +90° about X) before baking, so the spliced geometry
    lands in the same frame as the inferred bbox. Ignored for ada-reader formats
    (already Z-up)."""
    import pathlib as _pl
    import tempfile as _tf

    import numpy as np
    import trimesh

    ext = ext.lower()
    with _tf.TemporaryDirectory(prefix="eqcad_") as tmp:
        src = _pl.Path(tmp) / f"source{ext}"
        src.write_bytes(data)
        if ext in (".glb", ".gltf", ".stl", ".obj"):
            scene = trimesh.load(src, force="scene")
            if not z_up:
                scene.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        else:
            import ada

            readers = {
                ".step": ada.from_step,
                ".stp": ada.from_step,
                ".ifc": ada.from_ifc,
                ".sat": ada.from_acis,
                ".xml": ada.from_genie_xml,
            }
            reader = readers.get(ext)
            if reader is None:
                raise ValueError(f"unsupported CAD extension {ext!r} for geometry splice")
            scene = reader(src).to_trimesh_scene()
    return scene.dump(concatenate=True)


async def _run_equipment_bbox(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Infer an equipment type's bounding box from its linked CAD asset and
    render a preview GLB. ``conversion_options`` carries ``{"type_id", "cad_key"}``;
    the inferred bbox is merged into the equipment doc (no revision bump) and the
    preview lands at ``job.derived_key`` (``_equipment/{id}/preview.glb``)."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    type_id = opts.get("type_id")
    cad_key = opts.get("cad_key")
    # Whether the CAD asset is authored Z-up (adapy convention). Default True =
    # verbatim; False re-orients a Y-up mesh into Z-up before measuring.
    cad_z_up = bool(opts.get("cad_z_up", True))

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not type_id or not cad_key:
        await _fail("build", "conversion_options.type_id and cad_key are required for equipment_bbox")
        return
    if db_pool is None:
        await _fail("build", "equipment bbox inference requires DATABASE_URL on the worker")
        return

    from . import db as db_module

    try:
        data = await storage.get_bytes(scope, cad_key)
    except Exception as exc:
        await _fail("read", f"CAD asset {cad_key} not readable: {exc}")
        return

    ext = pathlib.PurePosixPath(cad_key).suffix.lower()
    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="build", progress=0.40)
        bbox, preview = await loop.run_in_executor(None, lambda: _infer_equipment_geometry(data, ext, z_up=cad_z_up))
    except Exception as exc:
        logger.exception("worker: equipment_bbox failed for %s", type_id)
        await _fail("build", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, preview, content_encoding="gzip")
        await db_module.apply_inferred_bbox(db_pool, type_id, bbox)
    except Exception as exc:
        logger.exception("worker: equipment_bbox upload failed for %s", type_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


def _engine_deploy_key_path(secret_name: str | None) -> str | None:
    """Filesystem path of the SSH deploy key for an external engine, or None for a
    public repo. The manifest names a secret; the deployment mounts that key file
    and points ``ENGINE_DEPLOY_KEY_<SECRET>`` at it (secret name uppercased,
    non-alnum -> ``_``). None when unset — the clone then runs without a key."""
    if not secret_name:
        return None
    env = "ENGINE_DEPLOY_KEY_" + "".join(c.upper() if c.isalnum() else "_" for c in secret_name)
    return os.environ.get(env)


async def _run_procedural_engine_build(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Build a ``kind:wheel`` procedural engine's wheel from its git repo and
    store it under the hidden ``_engines/`` prefix.

    ``conversion_options`` carries ``{"engine_id"}``; the manifest (repo_url/ref/
    deploy_key_secret) is read from postgres. The wheel is a pure-python
    (``py3-none-any``) build the browser micropip-installs. The built wheel's key
    is recorded in the engine doc (``wheel_key``, no revision bump), mirroring
    :func:`_run_equipment_bbox`."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    engine_id = opts.get("engine_id")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not engine_id:
        await _fail("build", "conversion_options.engine_id is required for procedural_engine_build")
        return
    if db_pool is None:
        await _fail("build", "engine build requires DATABASE_URL on the worker")
        return

    from . import db as db_module

    row = await db_module.get_procedural_engine(db_pool, engine_id)
    if row is None:
        await _fail("build", f"procedural engine {engine_id} not found")
        return
    doc = row.get("doc") or {}
    if doc.get("kind") != "wheel":
        await _fail("build", f"engine {engine_id} is not kind=wheel (got {doc.get('kind')!r})")
        return
    repo_url = doc.get("repo_url")
    ref = doc.get("ref") or "main"
    if not repo_url:
        await _fail("build", "engine manifest is missing repo_url")
        return
    ssh_key_path = _engine_deploy_key_path(doc.get("deploy_key_secret"))

    from .engine_build import build_engine_wheel
    from .procedural import engine_wheel_key

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="build", progress=0.30)
        filename, wheel_bytes = await loop.run_in_executor(
            None, lambda: build_engine_wheel(repo_url, ref, ssh_key_path=ssh_key_path)
        )
    except Exception as exc:
        logger.exception("worker: procedural_engine_build failed for %s", engine_id)
        await _fail("build", str(exc), tb_module.format_exc())
        return

    key = engine_wheel_key(engine_id, filename)
    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # A wheel is an already-compressed zip — store as-is (no gzip re-encode).
        await storage.put_bytes(scope, key, wheel_bytes)
        await db_module.set_procedural_engine_wheel(db_pool, engine_id, key)
    except Exception as exc:
        logger.exception("worker: procedural_engine_build upload failed for %s", engine_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


async def _run_component_build(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Build a Connection GLB from a registered ConnectionSpec + inputs.

    Inputs are carried in ``job.conversion_options`` as
    ``{"spec_name": ..., "inputs": ..., "name": ..., "extra_handler_kwargs": {...}}``.
    The GLB lands at ``job.derived_key`` (typically
    ``_derived/component_builds/<job_id>.glb``); the frontend then
    fetches it via the standard blob GET. Runs in-process (pure Python
    via adapy + the registered handler) since handler imports happen
    at module load time in the worker process.
    """
    job_id = job.job_id
    opts = job.conversion_options or {}
    spec_name = opts.get("spec_name")
    inputs = opts.get("inputs") or {}
    component_name = opts.get("name")
    extra_kwargs = opts.get("extra_handler_kwargs") or {}

    if not spec_name:
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="build",
            error="conversion_options.spec_name is required for component_build",
        )
        await _audit_done(db_pool, job_id, "error", "missing spec_name", started_at)
        return

    from ada.api.connections import build_component

    loop = asyncio.get_running_loop()

    def _build_and_serialize() -> bytes:
        conn = build_component(
            spec_name=spec_name,
            inputs=inputs,
            name=component_name,
            **extra_kwargs,
        )
        glb_path = new_temp_path(suffix=".glb")
        try:
            conn.to_gltf(glb_path)
            return glb_path.read_bytes()
        finally:
            glb_path.unlink(missing_ok=True)

    try:
        await queue.update(job_id, stage="build", progress=0.40)
        glb_bytes = await loop.run_in_executor(None, _build_and_serialize)
    except Exception as exc:
        logger.exception("worker: component_build failed for %s", spec_name)
        trace = tb_module.format_exc()
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="build",
            error=str(exc),
        )
        await _audit_done(
            db_pool,
            job_id,
            "error",
            str(exc),
            started_at,
            traceback=trace,
        )
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # gzip-at-rest (see the conversion path) so the presigned GET serves it
        # Content-Encoding: gzip and the browser decompresses on the fly.
        await storage.put_bytes(scope, job.derived_key, glb_bytes, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: component_build upload failed for %s", spec_name)
        trace = tb_module.format_exc()
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="upload",
            error=str(exc),
        )
        await _audit_done(
            db_pool,
            job_id,
            "error",
            str(exc),
            started_at,
            traceback=trace,
        )
        return

    await queue.update(
        job_id,
        status=JOB_STATUS_DONE,
        stage="ready",
        progress=1.0,
        error=None,
    )
    await _audit_done(db_pool, job_id, "done", None, started_at)


class _SyncStorageFacade:
    """Synchronous view of the async :class:`Storage`, scoped to one job.

    A utility handler runs in a worker thread (sync) but needs to read/write
    blobs (fetch a compare-ref build, upload an overlay GLB). This bridges each
    call back onto the worker's event loop via ``run_coroutine_threadsafe`` so
    the handler stays simple, synchronous code.
    """

    def __init__(self, storage, scope, loop):
        self._s, self._scope, self._loop = storage, scope, loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def list_keys(self, prefix: str = "") -> list[str]:
        entries = self._run(self._s.list(self._scope))
        return [e.key for e in entries if e.key.startswith(prefix)]

    def fetch_to_path(self, key: str, dest):
        self._run(self._s.stream_to_path(self._scope, key, pathlib.Path(dest)))
        return dest

    def get_bytes(self, key: str) -> bytes:
        return self._run(self._s.get_bytes(self._scope, key))

    def put_bytes(self, key: str, data: bytes, content_encoding: "str | None" = None) -> None:
        self._run(self._s.put_bytes(self._scope, key, data, content_encoding=content_encoding))


async def _run_utility_job(
    *,
    job: Job,
    src_path: pathlib.Path,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    _on_progress,
) -> None:
    """Run a worker @utility against the loaded scene GLB.

    ``conversion_options`` carries ``{"utility_name": ..., "kwargs": {...}}``. The
    handler returns a viewer-ops dict stored as JSON at ``job.derived_key`` (a
    ``*.viewops.json`` key the API set at enqueue). The handler may also write
    auxiliary blobs (e.g. an overlay GLB) via the sync storage facade and
    reference them by key in the payload.
    """
    import json

    from .utility import run_utility

    job_id = job.job_id
    opts = job.conversion_options or {}
    uname = opts.get("utility_name")
    ukwargs = opts.get("kwargs") or {}
    if not uname:
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="utility",
            error="conversion_options.utility_name is required for a utility job",
        )
        await _audit_done(db_pool, job_id, "error", "missing utility_name", started_at)
        return

    loop = asyncio.get_running_loop()
    sync_storage = _SyncStorageFacade(storage, scope, loop)

    # run_utility calls on_progress SYNCHRONOUSLY from the executor thread, but _on_progress
    # is an async coroutine (it writes the KV queue). Schedule it onto the loop so utility
    # stage/progress updates actually land in the job row — the same channel model loading /
    # conversions drive the global toast from. Fire-and-forget: we don't block the utility.
    def _sync_on_progress(stage: str, frac: float) -> None:
        try:
            asyncio.run_coroutine_threadsafe(_on_progress(stage, frac), loop)
        except Exception:  # noqa: BLE001 — a progress hiccup must never sink the utility
            pass

    def _invoke() -> dict:
        return run_utility(
            uname,
            src_path,
            storage=sync_storage,
            scope=scope,
            on_progress=_sync_on_progress,
            source_key=job.source_key,  # real model key — src_path is a random temp name
            kwargs=ukwargs,
        )

    try:
        await queue.update(job_id, stage="utility", progress=0.30)
        payload = await loop.run_in_executor(None, _invoke)
    except Exception as exc:
        logger.exception("worker: utility %s failed for job %s", uname, job_id)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="utility", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=trace)
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, json.dumps(payload).encode("utf-8"))
    except Exception as exc:
        logger.exception("worker: utility %s upload failed for job %s", uname, job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


def _read_self_proc_io() -> tuple[int, int]:
    """Return ``(read_bytes, write_bytes)`` for THIS process from
    ``/proc/self/io``. Best-effort: returns ``(0, 0)`` off Linux or when the
    file is unreadable, so a missing counter never breaks the harness."""
    read_bytes = 0
    write_bytes = 0
    try:
        for line in pathlib.Path("/proc/self/io").read_text().splitlines():
            if line.startswith("read_bytes:"):
                read_bytes = int(line.split()[1])
            elif line.startswith("write_bytes:"):
                write_bytes = int(line.split()[1])
    except (FileNotFoundError, PermissionError, ValueError, IndexError):
        pass
    return read_bytes, write_bytes


def _read_self_vmhwm_kb() -> int:
    """Return this process's peak resident set (VmHWM, kB) from
    ``/proc/self/status``; ``0`` when unavailable (non-Linux)."""
    try:
        for line in pathlib.Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except (FileNotFoundError, ValueError, IndexError):
        pass
    return 0


def _read_self_rusage() -> "tuple[float, float, int] | None":
    """``(user_seconds, sys_seconds, max_rss_kb)`` for this process and its
    children, or ``None`` where the counters cannot be read.

    ``resource`` is POSIX-only and does not exist on Windows. That matters
    because the plugin-job profiling harness runs IN-PROCESS — unlike the
    convert path, which does its accounting in a forked child that only ever
    exists on POSIX — so an unguarded ``import resource`` there would fail the
    JOB, not just the measurement, on any Windows worker with profiling on.
    Best-effort, like the ``/proc`` readers above: a counter we cannot read is
    a poorer audit row, never a failed conversion.

    Whole-process by construction: the executor model cannot isolate one
    thread's counters, so the numbers include any concurrent work on this
    worker.
    """
    try:
        import resource
    except ModuleNotFoundError:  # Windows
        return None
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (
        me.ru_utime + kids.ru_utime,
        me.ru_stime + kids.ru_stime,
        int(max(me.ru_maxrss, kids.ru_maxrss)),
    )


async def _run_plugin_job(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Run a plugin's on-demand backend job — the generic dispatch (core names no
    plugin). Synthetic: no source file; the plugin fetches whatever it needs via
    the scope-bound storage facade.

    ``conversion_options`` carries ``{"plugin_id": str, "options": dict,
    "derived_prefix": str | None}``. The worker resolves the plugin's advertised
    ``job_entrypoint`` (``"module:callable"``) from the backend registry the pool
    preloaded (``ADA_WORKER_PRELOAD`` / ``ada.plugins``), calls it in an executor
    with the sync storage facade + a progress bridge + the derived-blob prefix,
    and stores the returned summary dict (JSON, gzipped) at ``job.derived_key``.
    The plugin owns writing its own sidecar bundle under its reserved prefix.
    """
    import importlib
    import json

    from ada.plugins import plugin_backend_spec

    job_id = job.job_id
    opts = job.conversion_options or {}
    plugin_id = opts.get("plugin_id")
    if not plugin_id:
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="plugin",
            error="conversion_options.plugin_id is required for a plugin_job",
        )
        await _audit_done(db_pool, job_id, "error", "missing plugin_id", started_at)
        return

    spec = plugin_backend_spec(plugin_id)
    entry = (spec or {}).get("job_entrypoint")
    if not spec or not entry:
        msg = (
            f"plugin {plugin_id!r} is not registered on this worker or advertises no "
            f"job_entrypoint — is its backend on ADA_WORKER_PRELOAD / an ada.plugins entry point?"
        )
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="plugin", error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return

    try:
        mod_name, _, attr = entry.partition(":")
        fn = getattr(importlib.import_module(mod_name), attr)
    except Exception as exc:
        logger.exception("worker: plugin_job entrypoint %s import failed", entry)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="plugin", error=f"entrypoint import failed: {exc}")
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
        return

    loop = asyncio.get_running_loop()
    sync_storage = _SyncStorageFacade(storage, scope, loop)

    async def _aprog(stage: str, frac: float) -> None:
        await queue.update(job_id, stage=stage, progress=max(0.1, min(0.95, float(frac))))

    def _sync_on_progress(stage: str, frac: float) -> None:
        # The plugin calls this synchronously from the executor thread; hop it
        # onto the loop so stage/progress land in the job row. A hiccup here must
        # never sink the job.
        try:
            asyncio.run_coroutine_threadsafe(_aprog(stage, frac), loop)
        except Exception:  # noqa: BLE001
            pass

    # --- Mid-run cooperative cancellation -----------------------------------
    # The plugin entrypoint runs in a worker THREAD (run_in_executor), not the
    # SIGKILL-watchdog subprocess the convert path uses, so a running plugin job
    # can't be reaped by killing a child. Instead we poll the audit row (the
    # cancel endpoint's source of truth) and set a threading.Event the plugin can
    # observe cooperatively between units of work. A pure-CPU plugin that ignores
    # the event runs to completion unchanged (fully backward-compatible).
    cancel_event = threading.Event()

    async def _cancel_poller() -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                if await db_module.audit_is_cancelled(db_pool, job_id):
                    logger.info("worker: plugin_job %s cancel requested mid-run", job_id)
                    cancel_event.set()
                    return
            except Exception:
                logger.debug("worker: plugin_job cancel poll failed for %s", job_id, exc_info=True)

    poller: "asyncio.Task | None" = None
    if db_pool is not None:
        poller = asyncio.create_task(_cancel_poller())

    # Only pass cancel_event to plugins that actually accept it, so an older
    # entrypoint whose signature predates the kwarg keeps working untouched.
    import inspect

    try:
        _sig = inspect.signature(fn)
        _accepts_cancel = "cancel_event" in _sig.parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in _sig.parameters.values()
        )
    except (TypeError, ValueError):
        _accepts_cancel = False

    # --- In-process profiling harness (toggle + per-task filter gated) ------
    # Read the same admin toggle the convert path reads (`profile_conversions`)
    # plus the per-task filter (`profile_task_types`, comma-separated list of
    # target_format values; empty = all tasks). A plugin_job runs in an executor
    # THREAD and spawns its own trace subprocesses, so the fork-child cProfile /
    # rusage harness can't reach it — we run an in-process harness around the
    # call instead. NOTE: resource.getrusage(RUSAGE_SELF) + /proc/self are
    # WHOLE-PROCESS (the executor model can't isolate a single thread's counters),
    # so metrics include any concurrent work on this worker.
    profile_enabled = False
    if db_pool is not None:
        try:
            _pv = await db_module.get_setting(db_pool, "profile_conversions")
            _profile_on = (_pv or "").strip().lower() in {"1", "true", "yes", "on"}
            _tt = await db_module.get_setting(db_pool, "profile_task_types")
            _allowed_types = {t.strip() for t in (_tt or "").split(",") if t.strip()}
            profile_enabled = _profile_on and (not _allowed_types or "plugin_job" in _allowed_types)
        except Exception:
            logger.exception("worker: failed to read profile settings for plugin_job %s", job_id)

    prof_holder: dict[str, object] = {}

    def _invoke() -> dict:
        kwargs: dict = dict(
            storage=sync_storage,
            scope=scope,
            on_progress=_sync_on_progress,
            derived_prefix=opts.get("derived_prefix"),
        )
        if _accepts_cancel:
            kwargs["cancel_event"] = cancel_event
        if not profile_enabled:
            return fn(opts.get("options") or {}, **kwargs)

        # Harness: cProfile + rusage/VmHWM/proc-io deltas around the call. All
        # readings are whole-process (see note above), and every one of them is
        # best-effort — cProfile is the only part that works everywhere, and a
        # counter this platform cannot produce must cost a measurement, not the
        # job that was being measured.
        import cProfile

        prof = cProfile.Profile()
        ru0 = _read_self_rusage()
        rd0, wr0 = _read_self_proc_io()
        prof.enable()
        try:
            result = fn(opts.get("options") or {}, **kwargs)
        finally:
            prof.disable()
            ru1 = _read_self_rusage()
            rd1, wr1 = _read_self_proc_io()
            metrics: dict = {
                "read_bytes": max(0, rd1 - rd0),
                "write_bytes": max(0, wr1 - wr0),
            }
            # VmHWM is a monotonic high-water mark; ru_maxrss (kB on Linux) is
            # the fallback when /proc is unavailable.
            peak_rss_kb = _read_self_vmhwm_kb() or (ru1[2] if ru1 else 0)
            if peak_rss_kb:
                metrics["peak_rss_kb"] = peak_rss_kb
            if ru0 is not None and ru1 is not None:
                # Omitted rather than zeroed where rusage is unavailable. A
                # zero would be indistinguishable from a job that genuinely
                # burned no CPU, and the audit panel reads exactly that ratio
                # to decide a task is "mostly waiting on IO".
                metrics["cpu_user_ms"] = int((ru1[0] - ru0[0]) * 1000)
                metrics["cpu_sys_ms"] = int((ru1[1] - ru0[1]) * 1000)
            prof_bytes: bytes | None = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".prof", delete=False) as tf:
                    _prof_path = tf.name
                prof.dump_stats(_prof_path)
                prof_bytes = pathlib.Path(_prof_path).read_bytes()
                os.unlink(_prof_path)
            except Exception:
                logger.debug("worker: plugin_job profile dump failed for %s", job_id, exc_info=True)
            prof_holder["metrics"] = metrics
            prof_holder["profile_bytes"] = prof_bytes
        return result

    async def _plugin_metrics() -> dict:
        """Assemble the audit metrics dict from the harness output, uploading the
        .prof via the same helper the convert path uses. No-op when profiling off."""
        metrics = dict(prof_holder.get("metrics") or {})  # type: ignore[arg-type]
        prof_bytes = prof_holder.get("profile_bytes")
        if prof_bytes:
            try:
                profile_key = f"_derived/{job.source_key}.{job_id}.prof"
                await storage.put_bytes(scope, profile_key, prof_bytes)  # type: ignore[arg-type]
                metrics["profile_key"] = profile_key
            except Exception:
                logger.exception("worker: plugin_job profile upload failed for %s", job_id)
        return metrics

    try:
        await queue.update(job_id, stage="plugin", progress=0.10)
        summary = await loop.run_in_executor(None, _invoke)
    except Exception as exc:
        # Distinguish a user-cancel (poller tripped the event, plugin bailed
        # cooperatively) from a genuine error: on cancel, mark cancelled (NOT
        # error) and return without the error path — mirrors the convert path's
        # CANCELLED branch.
        if cancel_event.is_set():
            logger.info("worker: plugin_job %s cancelled by user mid-run", job_id)
            try:
                await queue.update(job_id, status="cancelled", stage="cancelled", progress=1.0, error=None)
            except Exception:
                pass
            await _audit_done(
                db_pool, job_id, "cancelled", "cancelled by user", started_at, metrics=await _plugin_metrics()
            )
            return
        logger.exception("worker: plugin_job %s failed for job %s", plugin_id, job_id)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="plugin", error=str(exc))
        await _audit_done(
            db_pool, job_id, "error", str(exc), started_at, traceback=trace, metrics=await _plugin_metrics()
        )
        return
    finally:
        # Stop the cancel poller in all paths (cancelling an already-finished
        # task is harmless).
        if poller is not None:
            poller.cancel()

    try:
        await queue.update(job_id, stage="upload", progress=0.95)
        payload = summary if isinstance(summary, dict) else {"result": summary}
        await storage.put_bytes(scope, job.derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: plugin_job %s summary upload failed for job %s", plugin_id, job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at, metrics=await _plugin_metrics())


async def _try_reduced_sif_source(
    storage: Storage,
    scope: Scope,
    source_key: str,
    step: int | None,
    src_path: pathlib.Path,
) -> bool:
    """Range-fetch just one result step of a SIF deck instead of the whole file.

    When a byte-offset index sidecar exists (built by a prior conversion), the
    bytes of every *other* step are skipped: only the target step's RV records
    plus the step-invariant mesh / RDPOINTS / control rows are fetched and
    concatenated into ``src_path`` — a smaller, still-valid SIF the normal
    reader parses. A 969 MB deck becomes a ~340 MB read, and re-picking a mode
    in the viewer stops re-downloading the whole file.

    Returns True on success; False (with ``src_path`` untouched) to fall back
    to the full streaming download. Skipped when the source is gzip-stored —
    range offsets address the *uncompressed* file.
    """
    from ada.fem.formats.sesam.results.sif_index import SifStepIndex

    from .converter import sif_index_key_for

    index_key = sif_index_key_for(source_key)
    try:
        idx_bytes = await storage.get_bytes(scope, index_key)
    except FileNotFoundError:
        return False
    except Exception:
        logger.exception("worker: reading SIF index %s failed (non-fatal)", index_key)
        return False

    try:
        idx = SifStepIndex.from_json(idx_bytes)
    except Exception:
        logger.warning("worker: SIF index %s unreadable; full download", index_key)
        return False

    try:
        if await storage.is_gzip_stored(scope, source_key):
            return False
    except Exception:
        return False

    target = step if step is not None else idx.default_step()
    if target not in idx.steps:
        return False

    ranges = idx.include_ranges(target)
    try:
        with open(src_path, "wb") as fo:
            for s, e in ranges:
                fo.write(await storage.get_range(scope, source_key, s, e - s))
    except Exception:
        logger.exception("worker: SIF range-fetch for %s failed; full download", source_key)
        return False

    fetched = sum(e - s for s, e in ranges)
    logger.info(
        "worker: SIF reduced read %s step %s — %d/%d bytes (%.0f%%)",
        source_key,
        target,
        fetched,
        idx.size,
        100.0 * fetched / max(idx.size, 1),
    )
    return True


async def _try_sin_stream_uri(storage: Storage, scope: Scope, source_key: str) -> str | None:
    """Presigned GET URL for reading a ``.sin`` deck straight from object storage.

    The SIN reader (:func:`ada.fem.formats.sesam.results.sin_reader.open_sin`)
    range-fetches through a paged byte source, so a conversion touches only the
    pointer tables plus the target step's records — no multi-GB full download,
    and resident bytes stay capped by the reader's page cache. Returns None
    (caller falls back to the full streaming download) when the store can't
    presign (LocalStore), the blob is gzip-at-rest (range offsets address the
    uncompressed file), or the source is missing (so the download path raises
    the proper FileNotFoundError instead of the child 404ing mid-read).
    """
    try:
        if not storage.supports_presigned_uploads:
            return None
        if await storage.is_gzip_stored(scope, source_key):
            return None
        if not await storage.exists(scope, source_key):
            return None
        # TTL must outlive the conversion — the child fetches pages throughout
        # its run, not just at open. 4 h covers the longest bakes.
        return await storage.presigned_get_url(scope, source_key, expires_in_seconds=4 * 3600, internal=True)
    except Exception:
        logger.exception("worker: presigning SIN source %s failed (non-fatal); full download", source_key)
        return None


async def _ensure_sif_index(storage: Storage, scope: Scope, source_key: str, src_path: pathlib.Path) -> None:
    """Build + upload the SIF byte-offset index sidecar if absent.

    One-time cheap byte scan (no float parsing) of the full local deck so later
    picks of other steps range-fetch a reduced file. Best-effort: a failure
    here never fails the job — it just means the next pick scans the whole file
    again."""
    from ada.fem.formats.sesam.results.sif_index import build_sif_index

    from .converter import sif_index_key_for

    index_key = sif_index_key_for(source_key)
    try:
        if await storage.exists(scope, index_key):
            return
        idx = await asyncio.to_thread(build_sif_index, src_path)
        await storage.put_bytes(scope, index_key, idx.to_json())
        logger.info("worker: built SIF index for %s (%d steps)", source_key, len(idx.steps))
    except Exception:
        logger.exception("worker: building SIF index for %s failed (non-fatal)", source_key)


async def _process_one(
    job_id: str,
    queue: JobQueue,
    storage: Storage,
    pool: ThreadPoolExecutor | None,
    db_pool: asyncpg.Pool | None,
    delivery_count: int = 1,
) -> None:
    # ``pool`` is unused since the convert call moved into a forked
    # subprocess (see subprocess_convert.run_isolated_convert). The
    # parameter stays so the caller signature is unchanged for now;
    # remove once we're sure no tests reach in for the executor handle.
    del pool
    started_at = time.monotonic()
    job = await queue.get(job_id)
    if job is None:
        logger.warning("worker: job %s not found in KV; skipping", job_id)
        return

    scope = _scope_of(job)

    # Pre-download cancel skip: a cell whose (audit) run was cancelled is acked +
    # skipped here, BEFORE any source download or convert — so a cancelled run's
    # queued backlog costs ~nothing and can't wedge the worker on a doomed job.
    # (A *deleted* run's rows are gone, so audit_is_cancelled can't catch those —
    # the run cancel/delete endpoints purge those messages from the stream up front.)
    if db_pool is not None:
        try:
            if await db_module.audit_is_cancelled(db_pool, job_id):
                logger.info("worker: job %s cancelled; skipping before download", job_id)
                await queue.update(job_id, status="cancelled", stage="cancelled", progress=1.0, error=None)
                return
        except Exception:
            logger.exception("worker: pre-download cancel check failed for job %s", job_id)

    # Poison-pill guard: if NATS has redelivered this message past
    # the cap, the previous attempts crashed the worker before they
    # could ack. Stop trying — record the error, ack the message,
    # and let the queue drain so legitimate jobs aren't blocked.
    if delivery_count > MAX_DELIVERIES:
        msg = (
            f"worker exceeded {MAX_DELIVERIES} delivery attempts on this job "
            f"(prior runs likely crashed the worker process)."
        )
        logger.warning("worker: job %s gave up after %d attempts", job_id, delivery_count)
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="aborted",
            progress=0.0,
            error=msg,
        )
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return

    # Skip if a previous run already produced the derived blob. This is
    # the cheap safety net for redelivered messages.
    #
    # ``force_rebuild`` (set by the admin audit dispatcher when the
    # operator picks the cache-bypass option) makes us re-run even
    # if the blob exists — otherwise an audit measurement run would
    # see every cell short-circuit at ~5 ms each and the
    # ``duration_ms`` numbers would lie about actual conversion
    # cost. Regular convert jobs leave this False so the
    # redelivery safety-net still works.
    # equipment_bbox is EXEMPT: its real output is the inferred bbox merged into
    # the equipment doc (a DB side-effect), NOT the derived preview.glb blob. The
    # preview key isn't revision-stamped, so a cached preview would short-circuit
    # every re-infer and the bbox would never be (re)applied — leaving it stuck at
    # the archetype default. Always run the handler for it.
    if (
        not getattr(job, "force_rebuild", False)
        and job.target_format != "equipment_bbox"
        and await storage.exists(scope, job.derived_key)
    ):
        await queue.update(
            job_id,
            status=JOB_STATUS_DONE,
            stage="cached",
            progress=1.0,
            error=None,
        )
        await _audit_done(db_pool, job_id, "done", None, started_at)
        return

    await queue.update(job_id, status=JOB_STATUS_RUNNING, stage="loading", progress=0.05)
    # Mark the audit_log row matching this job as ``running`` (best-
    # effort). Without this the admin "current cell" toast can't
    # tell which queued row the worker is actually on, and the
    # display sticks to the same cell for the whole sweep.
    if db_pool is not None:
        try:
            await db_module.mark_audit_running(
                db_pool,
                job_id=job_id,
                worker_image_tag=_WORKER_IMAGE_TAG,
            )
        except Exception:
            logger.exception("worker: audit running-mark failed for job %s", job_id)

    # component_build has no source file — it synthesizes geometry from
    # a registered ConnectionSpec + user inputs carried in
    # conversion_options. Short-circuit before the source-streaming
    # path; the build runs in-process (pure Python via adapy + the
    # registered handler).
    if job.target_format == "component_build":
        await _run_component_build(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # procedural_build is synthetic too: the model doc lives in postgres (the
    # single source of truth) and is compiled in-process via ada.topo_model.
    if job.target_format == "procedural_build":
        await _run_procedural_build(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # plugin_job is synthetic too: a plugin's on-demand backend job. Core names
    # no plugin — the worker resolves the registered plugin's job_entrypoint from
    # the backend registry the pool preloaded (ADA_WORKER_PRELOAD / ada.plugins).
    if job.target_format == "plugin_job":
        await _run_plugin_job(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # procedural_detail is synthetic too: the chained EXTERNAL (Tier-B) detailing
    # stage. Routed to the detailing engine's capability pool (target_capability),
    # it reads the neutral structural artifact + section sidecar the structural
    # build wrote and produces the detailing-layer GLB.
    if job.target_format == "procedural_detail":
        await _run_procedural_detail(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # procedural_relocations is synthetic too: read the same postgres-stored doc
    # and produce a JSON proposal document (minimum equipment moves that make the
    # runs route cleanly) instead of a GLB.
    if job.target_format == "procedural_relocations":
        await _run_procedural_relocations(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # procedural_export_xlsx / procedural_import_xlsx are synthetic too: the
    # engine that owns the model's Excel format serializes the doc to a workbook
    # (export) or parses an uploaded workbook into a new model (import).
    if job.target_format == "procedural_export_xlsx":
        await _run_procedural_export_xlsx(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # procedural_export_model: compile the doc to an ada assembly and serialize it
    # to a downloadable IFC (detail) / Genie XML (sim) file.
    if job.target_format == "procedural_export_model":
        await _run_procedural_export_model(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    if job.target_format == "procedural_import_xlsx":
        await _run_procedural_import_xlsx(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # equipment_bbox is synthetic too: read the equipment type's linked CAD
    # asset, infer its bbox into the doc and render a preview GLB.
    if job.target_format == "equipment_bbox":
        await _run_equipment_bbox(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # procedural_engine_build is synthetic too: clone a kind:wheel engine's repo,
    # build its wheel and store it under _engines/ for the browser to micropip-install.
    if job.target_format == "procedural_engine_build":
        await _run_procedural_engine_build(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
        )
        return

    # Stream source to a worker-local tempfile rather than buffering
    # the whole payload in RAM. Big result decks (Sesam SIF can be
    # 950 MB+) blow up the worker pod otherwise; smaller sources still
    # benefit from skipping the bytes/path round-trip.
    src_suffix = pathlib.PurePosixPath(job.source_key).suffix or ""
    src_fd, src_name = tempfile.mkstemp(suffix=src_suffix)
    os.close(src_fd)
    src_path = pathlib.Path(src_name)
    # A SIF deck with a cached byte-offset index range-fetches only the target
    # step (reduced, still-valid SIF) instead of the whole ~1 GB file. Falls
    # back to the full stream when there's no index / it's gzip-stored / fetch
    # fails. ``sif_reduced`` gates the post-convert index build below.
    sif_reduced = False
    # A ``.sin`` result deck is read straight from object storage via a
    # presigned URL — the reader's paged range-fetch touches only the pointer
    # tables + one step's records, so the multi-GB download is skipped
    # entirely. glb is the only registry target for ``.sin`` (the FEA-result
    # route); None falls back to the full stream below.
    sin_source_uri: str | None = None
    # How the source landed on disk: "cache-hit" / "cache-miss" / "direct"
    # (None for the SIF-reduced / SIN-stream special paths). Recorded in
    # convert_meta so audit timing analysis can see the cache working —
    # fetch_ms drops to ~0 on hits.
    source_fetch_mode: str | None = None
    fetch_t0 = time.monotonic()
    try:
        try:
            if src_suffix.lower() == ".sif":
                sif_reduced = await _try_reduced_sif_source(storage, scope, job.source_key, job.step, src_path)
            elif src_suffix.lower() == ".sin" and job.target_format == "glb":
                sin_source_uri = await _try_sin_stream_uri(storage, scope, job.source_key)
            if not sif_reduced and sin_source_uri is None:
                # Cross-job source cache: an audit sweep converts the same
                # source to many targets, and re-downloading a multi-hundred-
                # MB source per target costs 30-60 s each time. Falls back to
                # a plain stream on any cache error (never fails the job) and
                # still raises FileNotFoundError for a missing source.
                source_fetch_mode = await source_cache.default_cache().fetch(storage, scope, job.source_key, src_path)
        except FileNotFoundError as exc:
            logger.warning("worker: source %s missing for job %s", job.source_key, job_id)
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="loading", error=str(exc))
            await _audit_done(db_pool, job_id, "error", str(exc), started_at)
            return

        # Co-download known sibling sidecars so format-specific
        # readers find them next to the source in the worker's
        # tempdir. The code_aster ``.rmed`` reader, for instance,
        # looks for ``<basename>.adapy_fem.json`` (lineage + per-
        # line-element section / orientation) by basename via
        # ``rmed_path.with_suffix(...)``. Sidecars are optional —
        # a 404 just means a third-party source without one, in
        # which case the reader falls back to its no-sidecar path.
        sibling_suffixes = _SIDECAR_SIBLINGS.get(src_suffix.lower(), ())
        for sib_suffix in sibling_suffixes:
            sib_key = job.source_key[: -len(src_suffix)] + sib_suffix
            sib_path = src_path.with_suffix(sib_suffix)
            try:
                await storage.stream_to_path(scope, sib_key, sib_path)
            except FileNotFoundError:
                pass  # optional sibling, OK to be missing
            except Exception:
                logger.exception(
                    "worker: failed fetching sibling %s for job %s (non-fatal)",
                    sib_key,
                    job_id,
                )

        # Source (+ sidecar) download is done — snapshot the slice so the audit
        # can attribute it. ``convert_ms`` spans started_at → post-convert, so a
        # slow object-storage stream (a multi-GB SIN deck takes 50–100 s) would
        # otherwise read as a slow conversion.
        fetch_ms = round((time.monotonic() - fetch_t0) * 1000)
        try:
            fetch_bytes = src_path.stat().st_size
        except OSError:
            fetch_bytes = None

        # Conversion settings flip via the admin panel and are read
        # fresh per job — admins can flip one on, send a
        # representative job, and flip it off without a worker
        # restart. No cache: one DB round-trip per setting is
        # negligible next to a tessellation pass.
        #
        # `profile_conversions` toggles cProfile inside the fork-child
        # and is consumed directly. The other four are mapped to
        # ADA_* env vars and applied inside the child fork only, so
        # sibling jobs / the parent worker keep their pristine env.
        profile_enabled = False
        env_overrides: dict[str, str] = {}
        # Initialised here (not only inside the ``db_pool is not None`` block below) so a worker
        # that came up without a DB pool — e.g. it raced Postgres during a restart — still converts
        # with code defaults instead of crashing every job with UnboundLocalError on ``timeout_s``.
        timeout_s: float | None = None
        if db_pool is not None:

            async def _read_bool_setting(key: str) -> str | None:
                try:
                    return await db_module.get_setting(db_pool, key)
                except Exception:
                    logger.exception("worker: failed to read %s setting", key)
                    return None

            v = await _read_bool_setting("profile_conversions")
            profile_enabled = (v or "").strip().lower() in {"1", "true", "yes", "on"}
            # Per-task profiling filter (admin key `profile_task_types`, a
            # comma-separated target_format list; empty = all tasks). Gates the
            # toggle so an admin can profile only e.g. `glb` or `plugin_job`
            # without a per-job override. Same key the plugin_job harness reads.
            _ptt = await _read_bool_setting("profile_task_types")
            _allowed_types = {t.strip() for t in (_ptt or "").split(",") if t.strip()}
            profile_enabled = profile_enabled and (not _allowed_types or job.target_format in _allowed_types)
            if profile_enabled:
                # The C++ sibling of the cProfile artefact: adacpp's env-gated
                # [STEPPROF] pipeline profiler (phase wall times, RSS at phase
                # boundaries, VmHWM peak, per-solid stats, parallelism/IO
                # pressure) prints to stderr, which the captured job Log keeps.
                # Applied inside the child fork only, so sibling jobs and the
                # parent worker keep their pristine env.
                env_overrides["ADACPP_STEP_PROFILE"] = "1"

            # Optional per-job wall-clock budget. Empty / 0 / non-
            # numeric leaves the watchdog off so legitimately-long
            # bakes (a multi-GiB FEA result sweep can take 20+ min)
            # aren't artificially killed. Set as a positive minutes
            # value to enable; the parent process then SIGTERMs the
            # convert subprocess after the deadline and SIGKILLs
            # 30 s later if it's still alive.
            timeout_minutes_raw = await _read_bool_setting("conversion_timeout_minutes")
            try:
                tm = float((timeout_minutes_raw or "").strip())
                if tm > 0:
                    timeout_s = tm * 60.0
            except (TypeError, ValueError):
                timeout_s = None

            # setting key → env var name. Worker passes the raw
            # truthy/falsy text through; surfaces.py /
            # converter.py do the same parsing they always have, so
            # the env-driven and admin-driven paths agree on edge
            # cases (e.g. "yes" / "no").
            _env_map = {
                "use_sat_pcurves": "ADA_USE_SAT_PCURVES",
                "skip_shapefix": "ADA_SKIP_SHAPEFIX",
                "merge_meshes": "ADA_GLB_MERGE_MESHES",
                # Reuse a parsed source across export targets: parse once, pickle (content-hashed,
                # local), reuse for every other target instead of re-reading the file. Big win for
                # audit runs (one source → many targets); harmless when a source converts once.
                "assembly_cache": "ADA_ASSEMBLY_CACHE",
                # STEP→GLB tessellation engine (libtess2 / occ-builtin / step2glb /
                # adacpp-{occ,cgal,hybrid}); enum string, read by _resolve_step_glb_pipeline.
                "step_glb_pipeline": "ADAPY_STEP_GLB_PIPELINE",
                # STEP→GLB streaming defaults (large-file OOM guard).
                "step_streamer_auto": "ADA_STEP_STREAMER_AUTO",
                "step_streamer_threshold_mb": "ADA_STEP_STREAMER_THRESHOLD_MB",
                # Per-solid tessellation budget; a solid that overruns it (OCC hang) is
                # killed and skipped so one bad solid can't freeze the whole conversion.
                "step_stream_solid_timeout_s": "ADA_STEP_STREAM_SOLID_TIMEOUT_S",
                # STEP→GLB tessellation pool memory bound: worker count cap + per-worker
                # soft/hard RSS caps (a worker over soft respawns between solids; over hard
                # mid-solid is killed + the solid requeued once). Sizes peak conversion RSS.
                "step_stream_workers": "ADA_STEP_STREAM_WORKERS",
                "step_stream_worker_soft_mem_mb": "ADA_STEP_STREAM_WORKER_SOFT_MEM_MB",
                "step_stream_worker_hard_mem_mb": "ADA_STEP_STREAM_WORKER_HARD_MEM_MB",
                # FEM→IFC memory-bounded writer. Default on (converter treats
                # unset as on); set falsy to revert to the in-memory writer.
                "ifc_streaming": "ADA_IFC_STREAMING",
                # Curved-surface tessellation quality (0 = lean relative default).
                "tess_linear_deflection": "ADA_OCC_TESS_LINEAR_DEFLECTION",
                "tess_angular_deg": "ADA_OCC_TESS_ANGULAR_DEG",
                "tess_relative": "ADA_OCC_TESS_RELATIVE",
                # Conversion log verbosity (DEBUG/INFO/WARNING/ERROR), set from the admin Conversion
                # panel. Unset keeps the quiet WARNING default; INFO surfaces per-stage progress + the
                # native engine summary in the captured audit Log. Read by the convert subprocess.
                "convert_log_level": "ADA_CONVERT_LOG_LEVEL",
            }
            for skey, env_name in _env_map.items():
                raw = await _read_bool_setting(skey)
                if raw is not None and raw.strip() != "":
                    env_overrides[env_name] = raw

            # Per-source-type tessellation engine. STEP→glb and the scene path
            # (gxml/ifc/sat→glb via to_gltf's BatchTessellator) use different engine envs;
            # resolve the one for THIS source's type from its own setting so e.g. gxml can run
            # libtess2 while ifc runs occ. Unset → the converter's adacpp-aware default (the scene
            # path defaults to libtess2 when adacpp is present; OCC's prism tessellation of curved
            # B-spline plates is non-manifold and drops the viewer's edge outlines). A per-type
            # setting here supersedes the legacy single ``step_glb_pipeline`` for STEP sources.
            _tess_engine_by_ext = {
                ".step": ("tess_engine_step", "ADAPY_STEP_GLB_PIPELINE"),
                ".stp": ("tess_engine_step", "ADAPY_STEP_GLB_PIPELINE"),
                ".xml": ("tess_engine_gxml", "ADAPY_GLB_TESS_ENGINE"),
                ".ifc": ("tess_engine_ifc", "ADAPY_GLB_TESS_ENGINE"),
                ".sat": ("tess_engine_sat", "ADAPY_GLB_TESS_ENGINE"),
                ".acis": ("tess_engine_sat", "ADAPY_GLB_TESS_ENGINE"),
            }
            _src_ext = os.path.splitext(job.source_key)[1].lower()
            _eng = _tess_engine_by_ext.get(_src_ext)
            if _eng is not None:
                _skey, _engine_env = _eng
                _raw_engine = await _read_bool_setting(_skey)
                if _raw_engine is not None and _raw_engine.strip() != "":
                    env_overrides[_engine_env] = _raw_engine.strip()

        # Per-job overrides win over global settings. ``None`` clears
        # an env var, allowing a job to ask "ignore the global
        # toggle, run with adapy's code default" without restarting.
        per_job = getattr(job, "conversion_options", None) or {}
        if per_job:
            _env_map_full = {
                "use_sat_pcurves": "ADA_USE_SAT_PCURVES",
                "skip_shapefix": "ADA_SKIP_SHAPEFIX",
                "merge_meshes": "ADA_GLB_MERGE_MESHES",
                "assembly_cache": "ADA_ASSEMBLY_CACHE",
                "step_glb_pipeline": "ADAPY_STEP_GLB_PIPELINE",
                "step_streamer": "ADA_STEP_STREAMER",
                "ifc_streaming": "ADA_IFC_STREAMING",
                "tess_linear_deflection": "ADA_OCC_TESS_LINEAR_DEFLECTION",
                "tess_angular_deg": "ADA_OCC_TESS_ANGULAR_DEG",
                "tess_relative": "ADA_OCC_TESS_RELATIVE",
                # Per-face pick regions in the GLB (face_ranges_node in scene extras). Per-JOB
                # only, deliberately: it enlarges the GLB and forces serial face tessellation, so
                # it is a debugging ask for one conversion, never a deployment-wide setting.
                # Honoured by the native STEP->GLB path alone — which is why the API advertises it
                # with supported_by=[cpp] rather than offering it against every serializer.
                "face_regions": "ADA_STREAM_TESS_FACE_REGIONS",
            }
            for k, v in per_job.items():
                env_name = _env_map_full.get(k)
                if env_name is None:
                    continue
                if v is None:
                    env_overrides.pop(env_name, None)
                else:
                    env_overrides[env_name] = str(v)
            # profile is passed as a kwarg to run_isolated_convert
            # rather than as an env var.
            if "profile_conversions" in per_job and per_job["profile_conversions"] is not None:
                profile_enabled = str(per_job["profile_conversions"]).strip().lower() in {"1", "true", "yes", "on"}

        # Forward progress from the converter to the KV-backed queue,
        # throttled so a chatty stage doesn't spam writes.
        last_kv_write = 0.0

        async def _on_progress(stage: str, frac: float) -> None:
            nonlocal last_kv_write
            _touch_liveness()  # a long conversion blocks the pull loop; progress keeps liveness fresh
            now = time.monotonic()
            if now - last_kv_write < 0.25 and frac < 1.0:
                return
            last_kv_write = now
            try:
                await queue.update(job_id, stage=stage, progress=frac)
            except Exception:
                logger.debug("queue.update from progress callback failed", exc_info=True)

        # Stream heartbeat samples to the audit row as they arrive,
        # so a hard crash (SIGSEGV/SIGABRT) leaves the partial timeline
        # behind for post-mortem instead of an empty metrics_samples
        # column.
        async def _on_sample(sample: ConvertSample) -> None:
            _touch_liveness()  # fires every ~2s during a subprocess conversion
            if db_pool is None:
                return
            try:
                await db_module.append_metrics_sample_by_job(
                    db_pool,
                    job_id=job_id,
                    sample={
                        "ts": sample.ts,
                        "elapsed_s": sample.elapsed_s,
                        "cpu_user_ms": sample.cpu_user_ms,
                        "cpu_sys_ms": sample.cpu_sys_ms,
                        "rss_kb": sample.rss_kb,
                        "peak_rss_kb": sample.peak_rss_kb,
                        "read_bytes": sample.read_bytes,
                        "write_bytes": sample.write_bytes,
                        "per_thread_cpu_ms": sample.per_thread_cpu_ms,
                    },
                )
            except Exception:
                logger.debug("metrics-sample append failed", exc_info=True)

        async def _maybe_upload_profile_bytes(prof_bytes: bytes | None) -> str | None:
            """Upload the cProfile bytes returned by the child process.
            Best-effort: errors are logged and return None so the audit
            row still records the rest of the metrics."""
            if not prof_bytes:
                return None
            try:
                profile_key = f"_derived/{job.source_key}.{job_id}.prof"
                await storage.put_bytes(scope, profile_key, prof_bytes)
                return profile_key
            except Exception:
                logger.exception("worker: profile upload failed for job %s", job_id)
                return None

        async def _maybe_upload_log_bytes(log_bytes: bytes | None) -> str | None:
            """Upload the captured child stdout/stderr so a conversion's output (incl. silently
            swallowed library warnings) is recoverable via the audit log. Best-effort + gzip-at-rest."""
            if not log_bytes:
                return None
            try:
                log_key = f"_derived/{job.source_key}.{job_id}.log"
                await storage.put_bytes(scope, log_key, log_bytes, content_encoding="gzip")
                return log_key
            except Exception:
                logger.exception("worker: log upload failed for job %s", job_id)
                return None

        # FEA streaming-viewer artefact bake — sibling code path to
        # the convert pipeline. The bake produces multiple files (mesh
        # GLB + manifest + per-field blobs) under
        # `_derived/<src>.fea/`, which doesn't fit the convert
        # contract of "one bytes blob per derived_key". Runs in-process
        # in a thread executor; the bake is pure Python (h5py + trimesh)
        # without the native-crash exposure that justifies fork
        # isolation for the convert path.
        # Worker utility against the loaded scene GLB (e.g. diff). Returns a
        # viewer-ops payload stored as JSON at the derived key, not a new file.
        if job.target_format == "utility":
            await _run_utility_job(
                job=job,
                src_path=src_path,
                scope=scope,
                storage=storage,
                queue=queue,
                db_pool=db_pool,
                started_at=started_at,
                _on_progress=_on_progress,
            )
            return

        if job.target_format == "fea_artefacts":
            await _run_fea_artefact_bake(
                job=job,
                src_path=src_path,
                scope=scope,
                storage=storage,
                queue=queue,
                db_pool=db_pool,
                started_at=started_at,
                _on_progress=_on_progress,
            )
            return

        # FEA legacy-picker meta cache (steps/fields inventory used by
        # FieldPickerModal). compute_fea_meta imports
        # ada.fem.formats.sesam.results.read_sif which the slim API
        # container can't import — this branch is the worker-side
        # half so the legacy picker actually works in deployed envs.
        if job.target_format == "fea_meta":
            await _run_fea_meta_compute(
                job=job,
                src_path=src_path,
                scope=scope,
                storage=storage,
                queue=queue,
                db_pool=db_pool,
                started_at=started_at,
                _on_progress=_on_progress,
            )
            return

        # Cross-format visual-parity validation — re-derives the source to the
        # structure-preserving formats and compares visualized-element counts.
        # Produces no derived blob; writes a row to audit_parity and audits the
        # cell done/error (mismatch -> error, so it shows in the run's failures).
        if job.target_format == "parity":
            await _run_parity_validation(
                job=job,
                src_path=src_path,
                scope=scope,
                storage=storage,
                queue=queue,
                db_pool=db_pool,
                started_at=started_at,
                _on_progress=_on_progress,
                timeout_s=timeout_s,
            )
            return

        # Build the kwargs convert() receives in the child process.
        # ``step`` / ``field`` are SIF/SIN-specific; ``options`` is
        # the registry-driven per-job knob dict (e.g.
        # ``{"merge_meshes": False}``) declared at
        # ``@converter(options=...)`` sites. Pass-through is uniform —
        # convert() forwards the dict to the matched handler and the
        # handler unpacks the knobs it understands; unknown keys are
        # ignored harmlessly.
        #
        # Legacy env-var-driven options (use_sat_pcurves /
        # skip_shapefix) still flow via env vars
        # on the child fork (see ``env_overrides`` below) because
        # their consuming code lives in deep OCC paths that haven't
        # been migrated to take these as function parameters yet.
        # The same option name can ride both rails — the kwarg wins
        # at the handler call site; the env var is the fallback for
        # adapy internals that haven't learned the kwarg path.
        convert_options: dict = {}
        if per_job:
            for k, v in per_job.items():
                if k == "profile_conversions":
                    continue  # already consumed as a meta kwarg above
                if v is None:
                    continue  # tri-state "clear"; nothing to forward
                convert_options[k] = v

        # Engine + options provenance for the audit row (which tessellator ran,
        # incl. an adacpp→occ-builtin fallback, and the effective toggles).
        convert_meta = dict(_convert_meta_for(job, env_overrides) or {})
        convert_meta["fetch_ms"] = fetch_ms
        if fetch_bytes is not None:
            convert_meta["fetch_bytes"] = fetch_bytes
        if source_fetch_mode is not None:
            # "cache-hit" explains a ~0 fetch_ms; "direct" marks a cache
            # bypass (disabled or fell back after a cache error).
            convert_meta["source_fetch"] = source_fetch_mode
        if sin_source_uri is not None:
            # No local copy — the child range-fetches pages on demand, so the
            # download cost shows up inside convert_ms, not fetch_ms.
            convert_meta["fetch_mode"] = "sin-range-stream"

        # Record the pod's CPU allotment (cgroup quota, else host cores) so the metrics chart can
        # render CPU as % utilization across all cores instead of the cumulative-time ramp.
        try:
            from ada.visit.scene_handling.scene_from_step_stream import (
                _cgroup_cpu_quota,
            )

            _cores = _cgroup_cpu_quota() or os.cpu_count()
            if _cores:
                convert_meta = dict(convert_meta or {})
                convert_meta["cpu_cores"] = int(_cores)
        except Exception:
            logger.debug("convert_meta: cpu_cores detection failed", exc_info=True)

        # Poll the audit_log (cancel endpoint's source of truth) so a user
        # cancellation actually reaps the running conversion subprocess.
        async def _cancel_check() -> bool:
            if db_pool is None:
                return False
            try:
                return await db_module.audit_is_cancelled(db_pool, job_id)
            except Exception:
                return False

        # Run convert() in a forked child. Crash isolation + rusage on
        # exit + per-/proc heartbeat sampling all in one. See
        # subprocess_convert.run_isolated_convert for the rationale.
        try:
            iresult: IsolatedConvertResult = await run_isolated_convert(
                convert,
                src_path,
                job.source_key,
                job.target_format,
                convert_kwargs={
                    "step": job.step,
                    "field": job.field,
                    "options": convert_options or None,
                    "source_uri": sin_source_uri,
                },
                on_progress=_on_progress,
                on_sample=_on_sample,
                profile_in_child=profile_enabled,
                env_overrides=env_overrides or None,
                timeout_s=timeout_s,
                cancel_check=_cancel_check,
            )
        except Exception as exc:
            # Failure in the parent-side machinery (fork, /proc reads,
            # asyncio plumbing). The child either never started or we
            # lost track of it; treat as a worker error.
            logger.exception("worker: subprocess wrapper failed for %s", job_id)
            trace = tb_module.format_exc()
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc))
            await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=trace)
            return

        # User cancellation: the watchdog reaped the child. The audit_log row is
        # already 'cancelled' (set by the cancel endpoint) — don't flip it to error.
        if iresult.signal_name == "CANCELLED":
            logger.info("worker: conversion for %s cancelled by user; child reaped", job.source_key)
            try:
                await queue.update(job_id, status="cancelled", stage="convert", error="cancelled by user")
            except Exception:
                pass
            return

        # Map the isolated result back to the existing audit/error flow.
        if iresult.exit_code != 0 or iresult.out_path is None:
            err_msg = iresult.error or "convert subprocess produced no output"
            trace = iresult.traceback
            # Recognize BundleError by name in the error message rather
            # than by type — the exception was raised in the child and
            # only the formatted message survives.
            log_lvl_info = err_msg.startswith("BundleError:")
            if log_lvl_info:
                logger.info("worker: bundle rejected for %s: %s", job.source_key, err_msg)
            elif iresult.signal_name:
                logger.warning(
                    "worker: convert child for %s killed by %s",
                    job.source_key,
                    iresult.signal_name,
                )
            else:
                logger.error("worker: conversion failed for %s -> %s: %s", job.source_key, job.target_format, err_msg)
            await queue.update(
                job_id,
                status=JOB_STATUS_ERROR,
                stage="convert",
                error=err_msg,
            )
            metrics = dict(iresult.final_metrics)
            metrics["profile_key"] = await _maybe_upload_profile_bytes(iresult.profile_bytes)
            metrics["log_key"] = await _maybe_upload_log_bytes(iresult.log_bytes)
            _attach_cpp_profiles(convert_meta, iresult.log_bytes)
            metrics["convert_meta"] = convert_meta
            await _audit_done(
                db_pool,
                job_id,
                "error",
                err_msg,
                started_at,
                traceback=trace,
                metrics=metrics,
            )
            return

        await queue.update(job_id, stage="uploading", progress=0.95)
        # NOTE: the native STEP->ifc/step/mesh paths used here come from the adacpp overlay
        # (deploy/Dockerfile.worker bakes the ADACPP_BRANCH HEAD at image-build time — it is NOT
        # live-tracked), so a worker fix in adacpp needs a fresh full worker build to ship.
        # Gzip text-format outputs (IFC, Genie XML); GLB is binary geometry
        # that doesn't compress meaningfully and is what the in-browser
        # viewer fetches on the hot path.
        # gzip-at-rest so the object carries Content-Encoding: gzip and the
        # browser auto-decompresses. GLBs are included: since the viewer switched
        # to a presigned GET straight from object storage (no API relay), the
        # stored bytes go over the wire as-is — a raw float32 GLB is ~2-3x larger
        # than its gzip, which is brutal on mobile/cellular. (The on-disk GLB is
        # still uncompressed; this is transport compression, transparent to the
        # GLTF loader. Whole-file load only — gzip-at-rest is not Range-safe.)
        # OBJ / STL / STEP exports are also gzip-at-rest: the native STEP→mesh writer
        # emits unsimplified geometry (the reference assembly's obj is ~7.7 GB raw, 73 M tris), and
        # storing it raw made the UPLOAD ~57% of that job's wall time. obj/step are
        # ASCII (compress dramatically); binary STL less so but still a net win. These
        # are whole-file export downloads (never Range-fetched, unlike FEA field blobs
        # which must stay raw — see fea_field_blob_range), so gzip-at-rest is safe.
        derived_encoding = (
            "gzip" if job.target_format in {"ifc", "xml", "glb", "gltf", "obj", "stl", "step", "stp"} else None
        )
        # Optional GLB compression (gltfpack / meshopt + quantization), gated
        # by the per-job glb_compression option (or the ADA_GLB_COMPRESSION
        # global default). Post-process step so it covers every GLB-producing
        # path from one place; fully guarded — any failure / missing binary
        # uploads the original GLB unchanged. The compressed file is a
        # separate path we unlink after upload.
        upload_path = iresult.out_path
        compressed_path = None
        # The audit's duration_ms is whole-job wall-clock (started_at → done), so a
        # meshopt encode reads as a slower *conversion*. Split it: convert_ms is the
        # time up to here (conversion proper), compress_ms is just the GLB-compression
        # post-step. Both land on convert_meta so the audit can show the breakdown.
        if isinstance(convert_meta, dict):
            convert_meta["convert_ms"] = round((time.monotonic() - started_at) * 1000)
        if job.target_format == "glb":
            _opts = getattr(job, "conversion_options", None) or {}
            # Default-on: a job that doesn't set glb_compression still gets
            # meshopt (the registry default isn't injected into
            # conversion_options). Per-job value wins; ADA_GLB_COMPRESSION is
            # the global override / kill switch (set to "off" to disable).
            _mode = _opts.get("glb_compression") or os.environ.get("ADA_GLB_COMPRESSION") or "meshopt"
            if _mode and str(_mode).lower() != "off":
                try:
                    from ada.visit.gltf.compress import compress_glb

                    _compress_t0 = time.monotonic()
                    packed = compress_glb(iresult.out_path, str(_mode))
                    if isinstance(convert_meta, dict):
                        convert_meta["compress_ms"] = round((time.monotonic() - _compress_t0) * 1000)
                    if str(packed) != str(iresult.out_path):
                        compressed_path = str(packed)
                        upload_path = compressed_path
                except Exception:
                    logger.exception("worker: glb compression failed; uploading uncompressed")
                    upload_path = iresult.out_path
        try:
            # Stream the output file straight to object storage (multipart) —
            # never reading it into a parent-side bytes buffer. cleanup_output()
            # drops the tmpfile + work dir once the upload settles either way.
            # put_path returns the at-rest gzip vs upload split so the audit can
            # attribute the post-conversion tail (gzip-at-rest was ~85% of the
            # large-assembly STEP->obj wall — see storage._gzip_level).
            put_timing = await storage.put_path(scope, job.derived_key, upload_path, content_encoding=derived_encoding)
            if isinstance(convert_meta, dict) and isinstance(put_timing, dict):
                # Distinct from the GLB meshopt ``compress_ms`` above: ``gzip_ms`` is
                # the at-rest gzip pass, ``upload_ms`` the multipart PUT.
                convert_meta["gzip_ms"] = put_timing.get("compress_ms")
                convert_meta["upload_ms"] = put_timing.get("upload_ms")
                convert_meta["stored_bytes"] = put_timing.get("stored_bytes")
        except Exception as exc:
            logger.exception("worker: upload failed for %s", job.derived_key)
            trace = tb_module.format_exc()
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
            metrics = dict(iresult.final_metrics)
            metrics["profile_key"] = await _maybe_upload_profile_bytes(iresult.profile_bytes)
            metrics["log_key"] = await _maybe_upload_log_bytes(iresult.log_bytes)
            _attach_cpp_profiles(convert_meta, iresult.log_bytes)
            metrics["convert_meta"] = convert_meta
            await _audit_done(
                db_pool,
                job_id,
                "error",
                str(exc),
                started_at,
                traceback=trace,
                metrics=metrics,
            )
            return
        finally:
            # Drop the compressed sibling first — cleanup_output() rmdir's the
            # work dir, which fails (and leaks) if our *.pack.glb is still in it.
            if compressed_path:
                try:
                    os.unlink(compressed_path)
                except OSError:
                    pass
            iresult.cleanup_output()

        # Conversion + upload succeeded — collect metrics and (optionally)
        # the cProfile dump from the child.
        metrics = dict(iresult.final_metrics)
        metrics["profile_key"] = await _maybe_upload_profile_bytes(iresult.profile_bytes)
        metrics["log_key"] = await _maybe_upload_log_bytes(iresult.log_bytes)
        _attach_cpp_profiles(convert_meta, iresult.log_bytes)
        metrics["convert_meta"] = convert_meta

        await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(db_pool, job_id, "done", None, started_at, metrics=metrics)

        # First full conversion of a SIF deck: build + cache the byte-offset
        # index so subsequent step/field picks range-fetch one step instead of
        # the whole file. Skipped when we already read a reduced file (the
        # index existed) or the source isn't a SIF. Best-effort.
        if src_suffix.lower() == ".sif" and not sif_reduced:
            await _ensure_sif_index(storage, scope, job.source_key, src_path)
    finally:
        try:
            src_path.unlink()
        except OSError:
            pass


def _warm_convert_imports() -> None:
    """Pre-import the heavy CAD C-extensions in the worker PARENT so every
    ``os.fork()``ed conversion child (see subprocess_convert) inherits them
    copy-on-write instead of re-importing per job.

    Without this, an IFC child faults in ifcopenshell(.geom) and a SAT child
    faults in OCC.Core — hundreds of MB of ``.so`` pages — on EVERY job; on a
    cold/pressured page cache that measured ~20-40s of near-zero-CPU I/O-wait
    per conversion (STEP's native adacpp reader was unaffected). Pre-importing
    keeps the pages referenced by the long-lived parent and out of every child's
    hot path. Per-module guard: a slim/scoped pool lacking a backend just skips it.

    NOTE: the durable fix is routing IFC/SAT conversions through adacpp + the
    native C++ IFC reader/writer so these deps aren't loaded at all (native STEP
    already is). Until every target format is wired natively, IFC->{stl,obj,xml}
    and SAT still go through ada.from_ifc / ada.from_acis; this removes their
    per-fork re-import tax in the meantime.
    """
    import importlib

    for mod, why in (
        ("ada.cadit.ifc.store", "ifcopenshell + ifcopenshell.geom"),
        ("ada.occ.tessellating", "OCC.Core tessellation + backends"),
    ):
        t0 = time.perf_counter()
        try:
            importlib.import_module(mod)
        except Exception as exc:  # scoped/slim pool without this backend — skip
            logger.info("worker: warm-import skipped %s (%s): %s", mod, why, exc)
            continue
        logger.info("worker: warm-imported %s (%s) in %.2fs", mod, why, time.perf_counter() - t0)


async def _heartbeat_until_stopped(
    *,
    publish: Callable[[], Awaitable[bool]],
    stop: asyncio.Event,
    bus_lost: asyncio.Event,
    interval: float = BUS_HEARTBEAT_SECONDS,
    failure_limit: int = BUS_HEARTBEAT_FAILURE_LIMIT,
) -> None:
    """Re-publish the registration on ``interval``, and watch the bus while doing it.

    Two jobs, because one round-trip answers both. The registration keeps the
    worker visible to the admin view; whether it *arrives* is the only routine
    evidence an idle worker has that the bus is still there. An idle pull fetch
    times out whether the queue is quiet or the connection is dead, so it can
    never be that evidence.

    Returns when ``stop`` is set — either by the caller (a shutdown signal) or
    by this loop after ``failure_limit`` consecutive failures, in which case it
    sets ``bus_lost`` first. The caller is what decides the exit code; see
    BUS_HEARTBEAT_FAILURE_LIMIT for why there is one to decide.

    Consecutive is the point. The counter resets on every success, so a worker
    that heartbeats fine for hours with the occasional blip never trips it; only
    a run of failures with no success between them does.
    """
    failures = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            if await publish():
                failures = 0
                continue
            failures += 1
            if failures < failure_limit:
                continue
            logger.error(
                "worker: %d consecutive heartbeats failed (~%.0fs); the bus is unreachable "
                "and this worker is not serving the pools it advertises. Exiting so it is "
                "restarted rather than left silently idle.",
                failures,
                failures * interval,
            )
            bus_lost.set()
            stop.set()
            return
        else:
            return  # stop set — exit cleanly


async def _run() -> None:
    # Make the worker's own lifecycle logs visible. The "ada" logger otherwise
    # inherits root's WARNING level, which silently drops every worker: INFO
    # line (booting / connected / registered / subscribing / ready) — leaving a
    # healthy idle worker indistinguishable from a hung one in `kubectl logs`.
    # This is the worker process only; the viewer configures its own logging.
    # ADA_WORKER_LOG_LEVEL overrides (e.g. WARNING to quiet a chatty pool).
    from ada.config import configure_logger

    configure_logger()  # attach the stdout StreamHandler (no-op if already attached)
    logger.setLevel(os.environ.get("ADA_WORKER_LOG_LEVEL", "INFO").upper())

    settings = load_settings()
    if settings.queue.url is None:
        raise SystemExit("ADA_VIEWER_NATS_URL not set; nothing for the worker to do")

    logger.info("worker: booting capabilities=%s", os.environ.get("ADA_WORKER_CAPABILITIES", "base"))
    storage = Storage.from_settings(settings)
    queue = JobQueue(settings.queue)
    logger.info("worker: connecting to NATS subject=%s", settings.queue.subject)
    # A worker only ever *uses* the JetStream topology; the API creates
    # it. Not administering it is what lets a worker be issued a
    # credential with no stream-admin rights — the whole point of
    # deploy/worker-trust.md. If the API has not started yet, connect()
    # waits for the KV bucket rather than racing to create it.
    #
    # ADA_WORKER_MANAGE_STREAM=true restores the old self-provisioning
    # behaviour for the one setup that needs it: a worker running against
    # a bare NATS with no API in the picture at all.
    manage = _bool_env("ADA_WORKER_MANAGE_STREAM", default=False)
    await queue.connect(manage=manage, name=f"adapy-worker-{_worker_id()}")
    logger.info("worker: connected to NATS (manage_stream=%s)", manage)

    # Optional importer hook: capability workers built FROM the base
    # image often need to populate the connection-spec registry (or
    # any other adapy import-side-effect registry) with project-
    # specific entries that adapy core doesn't know about. ADA_WORKER_PRELOAD
    # is a comma-separated list of dotted module paths to importlib.import
    # before the worker subscribes to the queue. Errors abort startup
    # — preload failure on a worker that exists *because* of those
    # imports should be loud, not silently degrade to "queued forever".
    preload_env = os.environ.get("ADA_WORKER_PRELOAD", "").strip()
    if preload_env:
        import importlib as _importlib

        for mod_name in (m.strip() for m in preload_env.split(",") if m.strip()):
            logger.info("worker: preloading %s", mod_name)
            _importlib.import_module(mod_name)

    # Entry-point plugin discovery (the ``ada.plugins`` group) — the in-core
    # complement to ADA_WORKER_PRELOAD. Each plugin's register() runs its
    # import-side-effect ``register_plugin_backend`` so the heartbeat below
    # advertises it. Isolated per-plugin (a broken plugin is logged + skipped),
    # unlike the deliberately-fatal preload above.
    try:
        from ada.plugins import discover_plugins

        discover_plugins()
    except Exception:
        logger.exception("worker: ada.plugins discovery failed (non-fatal)")

    # Self-identify so the viewer's /api/config + /api/admin/workers
    # can surface this worker. Two artefacts:
    #
    #   - ``worker_image_tag`` meta slot — single-value, last-writer-wins;
    #     /api/config reads it to show "running image: sha-XXXXXXX" in
    #     the viewer header. Pre-dates the per-worker registry.
    #   - ``__meta_worker__<id>`` per-worker entry — one row per running
    #     pod, refreshed on a heartbeat below; /api/admin/workers reads
    #     the whole set.
    #
    # Best-effort: a KV write failure shouldn't keep the worker from
    # accepting jobs.
    image_tag = os.environ.get("ADA_IMAGE_TAG", "").strip()
    # Stash on the module-level slot so ``_audit_done`` can stamp it
    # onto every audit_log row without threading through callers.
    global _WORKER_IMAGE_TAG
    _WORKER_IMAGE_TAG = image_tag or None
    worker_id = _worker_id()
    capabilities = _declared_capabilities()
    # An extra-capability pool builds FROM / runs an independent adapy and still
    # advertises the full base converter matrix, so it wins base conversion jobs (gxml->glb, ...)
    # it has no business running — and when that image is stale it produces outdated output (e.g.
    # non-manifold meshes). ADA_WORKER_BASE_CONVERSIONS=false makes this worker advertise ZERO base
    # conversions + base source-ext handling, leaving only its capability-routed utilities intact.
    # The clean, version-independent way to scope an extra pool (vs the ADA_WORKER_EXT_ALLOW
    # allowlist, which can only narrow to a positive set of source extensions, not to none).
    base_conversions_enabled = os.environ.get("ADA_WORKER_BASE_CONVERSIONS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    # Source extensions this worker can handle. Pulled from adapy's
    # stream-reader registry — whatever plug-ins ran before this point
    # (e.g. a capability worker's entrypoint that registered an extra
    # format before delegating to ``ada.comms.rest.worker``) has
    # already populated the registry, so we just read what's there.
    # API merges every online worker's list into /api/config so the
    # upload picker stays in sync without anyone having to repeat the
    # suffix list outside the plug-in that owns it.
    from ada.fem.results.artefacts import fea_artefact_extensions

    registered_exts = {e.lower() for e in fea_artefact_extensions()}
    if not base_conversions_enabled:
        # Pool scoped to its own capability only: don't claim any base source-ext
        # handling (FEA bake) — the ext allowlist below is then moot.
        registered_exts = set()
    # Optional per-pod allowlist. Capability (extension-specific) workers
    # build FROM the base image and so inherit its full stream-reader
    # registry — without this gate they'd race the base pool for
    # extensions they don't actually need to handle (e.g. ``.rmed``)
    # and, when running stale code, fail those jobs. The allowlist
    # is comma-separated source suffixes (``.odb,.sqlite``); leading
    # dots optional. Unset → handle everything in the registry, which
    # is the right default for the base worker.
    allow_env = os.environ.get("ADA_WORKER_EXT_ALLOW", "").strip()
    if allow_env:
        ext_allow_set: set[str] | None = {
            ("." + e.strip().lstrip(".")).lower() for e in allow_env.split(",") if e.strip()
        }
        registered_exts &= ext_allow_set
        logger.info(
            "worker: ADA_WORKER_EXT_ALLOW restricts handled exts to %s",
            sorted(ext_allow_set),
        )
    else:
        ext_allow_set = None
    source_exts = sorted(registered_exts)
    # Set form keeps the consume-loop capability check fast — every
    # job lookup needs to hit this; sorting is only for the wire
    # registration above.
    source_ext_set = registered_exts
    started_at = time.time()

    if image_tag:
        try:
            await queue.set_meta("worker_image_tag", image_tag)
            logger.info("worker: published image tag %s", image_tag)
        except Exception:
            logger.exception("worker: failed to publish image tag (non-fatal)")

    # Conversion matrix this worker advertises to the API. Take the
    # full registry (every ``@converter`` registration adapy + any
    # imported plug-in produced) and, if the per-pod allowlist is
    # set, drop entries whose source extension this pod isn't
    # licensed to handle — mirrors the capability gate in the
    # message loop so we don't promise something we'd NAK at
    # delivery time. The API merges every live worker's matrix into
    # ``/api/config["conversionMatrix"]`` for the SPA's /convert page.
    if not base_conversions_enabled:
        # Scoped pool: advertise no base conversions at all (utilities below still register).
        conversions: list[dict] = []
    else:
        full_matrix = ConverterRegistry.matrix()
        if ext_allow_set is not None:
            conversions = [m for m in full_matrix if m["from"] in ext_allow_set]
        else:
            conversions = full_matrix
    # Truthful capability advertisement: restrict the STEP→GLB engine enum to the engines THIS pool
    # can actually run (a slim/adacpp-less pod won't advertise adacpp-native, etc.). The API unions
    # these across pools for the list and routes engine-pinned jobs to a pool that advertises them.
    conversions = _gate_advertised_engines(conversions)

    # Utilities this worker advertises (every ``@utility`` registration adapy +
    # any preloaded plug-in produced). Importing the bundled utilities package
    # registers the built-ins (diff, ...); ADA_WORKER_PRELOAD can add more.
    # Published alongside conversions so the API can merge them into
    # ``/api/config`` for the SPA's Utilities panel.
    try:
        import ada.comms.rest.utilities  # noqa: F401  (registration side-effect)
    except Exception:
        logger.exception("worker: failed to import bundled utilities (non-fatal)")
    from .utility import UtilityRegistry

    utilities = UtilityRegistry.specs()

    # Equipment archetypes + system kinds this worker can compile into
    # procedural models — advertised (with full catalog-shaped specs) so the
    # viewer's cellbuilder can offer typed dropdowns that union code-defined
    # types with the per-scope DB catalog, show each type's origin, and "sync" a
    # code type into the DB catalog.
    try:
        from ada.topo_model.equipment import (
            equipment_archetype_specs,
            list_equipment_types,
        )

        procedural_equipment_types = list_equipment_types()
        procedural_equipment_specs = equipment_archetype_specs()
    except Exception:
        logger.exception("worker: failed to list procedural equipment types (non-fatal)")
        procedural_equipment_types = []
        procedural_equipment_specs = []
    try:
        from ada.api.systems import list_system_types, system_type_specs

        procedural_system_types = list_system_types()
        procedural_system_specs = system_type_specs()
    except Exception:
        logger.exception("worker: failed to list procedural system types (non-fatal)")
        procedural_system_types = []
        procedural_system_specs = []
    try:
        from ada.topo_model import design_ruleset_specs

        procedural_design_rulesets = design_ruleset_specs()
    except Exception:
        logger.exception("worker: failed to list procedural design rulesets (non-fatal)")
        procedural_design_rulesets = []
    # Cell/opening types this worker can place — advertised so the cellbuilder's
    # + Cell / + Opening pickers union the code-defined defaults with any a
    # capability worker's ADA_WORKER_PRELOAD registered (register_procedural_cell_type
    # / register_procedural_opening_type), exactly like the start-from templates.
    try:
        from ada.topo_model import (
            procedural_cell_type_specs,
            procedural_opening_type_specs,
        )

        procedural_cell_specs = procedural_cell_type_specs()
        procedural_opening_specs = procedural_opening_type_specs()
    except Exception:
        logger.exception("worker: failed to list procedural cell/opening types (non-fatal)")
        procedural_cell_specs = []
        procedural_opening_specs = []
    # Structural blueprints this worker can compile, advertised PER ENGINE (each
    # spec carries its ``engine``) so the cellbuilder's Blueprint dropdown unions
    # the code-defined defaults (adapy-default: steel_stru/none) with any a
    # capability worker's ADA_WORKER_PRELOAD registered (register_procedural_blueprint).
    try:
        from ada.topo_model import procedural_blueprint_specs

        procedural_blueprints = procedural_blueprint_specs()
    except Exception:
        logger.exception("worker: failed to list procedural blueprints (non-fatal)")
        procedural_blueprints = []
    # Start-from templates this worker can build, announced so the viewer's
    # "New model from template" dropdown is the union of live workers' demos.
    # The base image carries the adapy-default templates; a capability worker's
    # ADA_WORKER_PRELOAD module registers its own into the same registry before
    # this read (import side-effect), so they ride along here.
    try:
        from ada.topo_model import procedural_template_specs

        procedural_templates = procedural_template_specs()
    except Exception:
        logger.exception("worker: failed to list procedural templates (non-fatal)")
        procedural_templates = []
    # Per-engine capability flags (e.g. ``supports_grouping``), advertised so the
    # viewer's engine summary can gate capability-specific UI (the Groups section).
    # The base image carries the built-in engines' flags (all non-grouping); a
    # capability worker's ADA_WORKER_PRELOAD module registers its own via
    # register_procedural_engine_capabilities before this read (import side-effect).
    try:
        from ada.topo_model import procedural_engine_specs

        procedural_engines = procedural_engine_specs()
    except Exception:
        logger.exception("worker: failed to list procedural engine capabilities (non-fatal)")
        procedural_engines = []
    # Detailing engines this worker offers (a fabrication-detail stage that adds
    # connection joints after the structural build), advertised so the viewer's
    # Compile-settings "Detailing" dropdown unions the built-in adapy-default (+
    # the none sentinel) with any external engine a capability worker's
    # ADA_WORKER_PRELOAD module registered via register_detailing_engine.
    try:
        from ada.topo_model import detailing_engine_specs

        procedural_detailing_engines = detailing_engine_specs()
    except Exception:
        logger.exception("worker: failed to list detailing engines (non-fatal)")
        procedural_detailing_engines = []
    # Backend plugin specs (the viewer plugin system). Advertised so the REST
    # ``/api/plugins`` endpoint unions the static built-ins with any plugin a
    # capability worker's ADA_WORKER_PRELOAD / ``ada.plugins`` entry point
    # registered via register_plugin_backend. Empty until a plugin registers.
    try:
        from ada.plugins import plugin_backend_specs

        plugin_specs = plugin_backend_specs()
    except Exception:
        logger.exception("worker: failed to list backend plugins (non-fatal)")
        plugin_specs = []

    # --- capability qualification ------------------------------------------
    #
    # Advertise a capability only if this environment can be shown to satisfy
    # the requirements declared for it, instead of defending a correctness
    # property with an env var somebody has to remember. See
    # deploy/worker-trust.md §4.
    #
    # EVALUATED ONCE, HERE, and used for BOTH what is advertised and what is
    # subscribed to. Those two must not be able to disagree: an unfit worker
    # that still held a consumer would keep winning jobs with the evidence
    # removed, which is worse than not gating at all.
    #
    # Once at startup, deliberately: a requirement change takes effect when the
    # worker restarts. Re-deciding subscriptions mid-life would mean tearing
    # down consumers under load, and "take this pool out of service NOW" is a
    # different job with a different tool. This gate is for correctness drift,
    # which is a deploy-time property.
    import json as _json

    worker_packages = _capture_worker_packages()
    try:
        _raw_reqs = await queue.get_meta(CAPABILITY_REQUIREMENTS_KEY)
        requirements = _json.loads(_raw_reqs) if _raw_reqs else {}
    except Exception:
        # Being unable to READ the requirements is our plumbing failing, not
        # evidence of unfitness, so it must not take a fleet offline. Fail
        # open — loudly.
        logger.exception("worker: could not read capability requirements; advertising unqualified")
        requirements = {}

    _verdict = evaluate(capabilities, requirements, worker_packages)
    for _w in _verdict.withheld:
        # WARNING: a capability this worker was configured for is not being
        # served. Silence here is the support ticket this design exists to
        # prevent.
        logger.warning("worker: withholding capability %s — %s", _w["capability"], _w["reason"])
    capabilities = _verdict.kept
    withheld = _verdict.withheld

    # Only the packages some requirement actually names. The full manifest is
    # ~24 kB (197 entries, mostly repeated channel URLs) and this row is
    # rewritten on every heartbeat — a recurring cost with no reader. What the
    # row needs to carry is enough for an operator, and the admin panel, to
    # corroborate a withheld reason.
    _named = {
        str(n).lower()
        for e in (requirements or {}).values()
        if isinstance(e, dict)
        for n in list((e.get("requires") or {})) + list((e.get("build_match") or {}))
    }
    reported_packages = [p for p in worker_packages if str(p.get("name") or "").lower() in _named]

    async def _publish_registration() -> bool:
        """Publish the registration; return whether it reached the bus.

        Still non-fatal on its own — one failed heartbeat is a blip, and the
        registry row it writes is decorative. The return value is what lets the
        caller tell a blip from a connection that is never coming back; see
        BUS_HEARTBEAT_FAILURE_LIMIT.
        """
        try:
            await queue.register_worker(
                worker_id,
                {
                    "image_tag": image_tag or None,
                    "capabilities": capabilities,
                    # What this worker declined to serve, and why. Read by the
                    # API so a withheld capability surfaces as unavailable WITH
                    # A REASON rather than simply absent — those two are
                    # indistinguishable to every consumer otherwise.
                    "withheld": withheld,
                    "packages": reported_packages,
                    "source_exts": source_exts,
                    "conversions": conversions,
                    "utilities": utilities,
                    "procedural_equipment_types": procedural_equipment_types,
                    "procedural_equipment_specs": procedural_equipment_specs,
                    "procedural_system_types": procedural_system_types,
                    "procedural_system_specs": procedural_system_specs,
                    "procedural_design_rulesets": procedural_design_rulesets,
                    "procedural_cell_specs": procedural_cell_specs,
                    "procedural_opening_specs": procedural_opening_specs,
                    "procedural_blueprint_specs": procedural_blueprints,
                    "procedural_template_specs": procedural_templates,
                    "procedural_engine_specs": procedural_engines,
                    "procedural_detailing_engine_specs": procedural_detailing_engines,
                    "plugin_specs": plugin_specs,
                    "started_at": started_at,
                    "last_heartbeat": time.time(),
                },
            )
        except Exception:
            logger.exception("worker: register_worker failed (non-fatal)")
            return False
        return True

    await _publish_registration()
    logger.info(
        "worker: registered id=%s capabilities=%s",
        worker_id,
        ",".join(capabilities),
    )

    # DB pool. Audit-log updates (queued -> done/error) degrade gracefully
    # without it, but procedural_build / relocations / engine builds REQUIRE it
    # (they load the model row) — so a one-shot connect failure at startup must
    # not silently disable them. A rollout restarts many pods at once and can
    # trip a transient CoreDNS hiccup ("Name or service not known" on the DB
    # host); retry with backoff so that heals itself instead of stranding the
    # pod without a pool until someone restarts it by hand. Migrations are the
    # API's job — the worker builds a plain pool and trusts the schema is applied.
    db_pool: asyncpg.Pool | None = None
    if settings.database_url:
        for attempt in range(1, 7):
            try:
                db_pool = await asyncpg.create_pool(
                    dsn=settings.database_url,
                    min_size=1,
                    max_size=4,
                    max_inactive_connection_lifetime=600.0,
                )
                logger.info("worker: db pool ready (attempt %d)", attempt)
                break
            except Exception:
                logger.warning("worker: db connect attempt %d/6 failed; retrying", attempt)
                await asyncio.sleep(min(2**attempt, 15))
        if db_pool is None:
            logger.error(
                "worker: db connect failed after retries; audit updates + procedural/engine "
                "builds will fail on this pod until it can reach the DB"
            )
        # Capture this worker image's package manifest once at startup so convert
        # audit rows (stamped with worker_image_tag) can link to the exact
        # toolchain that produced their output.
        elif _WORKER_IMAGE_TAG:
            try:
                await db_module.upsert_worker_packages(
                    db_pool,
                    worker_image_tag=_WORKER_IMAGE_TAG,
                    packages=_capture_worker_packages(),
                )
            except Exception:
                logger.exception("worker: package manifest capture failed")

    # Subscribe to every capability this worker advertises, one durable
    # pull-subscriber each. NATS does the routing, so a worker only ever sees
    # jobs tagged for a pool it actually serves.
    #
    # A consumer per pool -- rather than one consumer over a wildcard subject --
    # is deliberate: see JobQueue.pull_subscribe, which documents why the
    # shared-consumer design was abandoned (workers NAK'd other pools' messages,
    # which burned the per-message delivery budget and surfaced as spurious
    # "exceeded N delivery attempts" failures on valid jobs).
    #
    # Order is preserved but no longer meaningful. Previously only
    # capabilities[0] was subscribed, so an image advertising several pools
    # silently served just the first while the rest looked idle rather than
    # broken. Serving them all is what lets one image cover several pools
    # instead of needing a separate deployment per capability.
    #
    # An EMPTY set here means qualification withheld everything (the env default
    # was normalised above). `_pool_capabilities` falls back to `["base"]` for an
    # unset env var, and letting that fallback apply to a verdict would make the
    # worker subscribe to the very pool it just declared itself unfit for --
    # advertising nothing while quietly still pulling base jobs. That is exactly
    # the disagreement between advertisement and subscription this design exists
    # to prevent, so the fallback is bypassed rather than reached.
    if capabilities:
        pool_capabilities = _pool_capabilities(capabilities)
    else:
        pool_capabilities = []
        logger.error(
            "worker: every capability was withheld; subscribing to nothing. "
            "The registry row records why, per capability. Fix the environment "
            "or the requirements and restart."
        )
    logger.info("worker: subscribing to capability pools %s", pool_capabilities)
    subs = [(cap, await queue.pull_subscribe(cap)) for cap in pool_capabilities]

    # Poll the pools round-robin, one fetch at a time, rather than fetching them
    # all concurrently. The ack keep-alive further down refreshes only the
    # message currently being processed, so holding a second leased message
    # would let its ack_wait lapse while the first job runs, and JetStream would
    # redeliver a job that is not actually stuck. One leased message at a time.
    #
    # The per-fetch timeout is divided across the pools so a full cycle still
    # takes about FETCH_TIMEOUT: pickup latency stays what it was for a
    # single-pool worker instead of growing with the number of capabilities.
    #
    # That reasoning covers an IDLE worker and used to stop there, with "the
    # cost is more idle round-trips, which are cheap". It is not the whole
    # story for a BUSY one: with a single pool saturated, the cursor walked
    # every other (empty) pool between consecutive jobs, so the cost was paid
    # per job rather than per idle cycle — ~2.1s each on a six-capability
    # worker, which cost 20 minutes of a 907-cell sweep. POOL_STREAK_LIMIT is
    # the answer: stay on a pool that is producing, up to a bound that keeps
    # the others from starving.
    per_fetch_timeout = _per_fetch_timeout(len(subs))

    # Warm the heavy CAD imports in this (parent) process before the per-job fork
    # loop below, so forked children inherit them copy-on-write instead of paying
    # a cold re-import per conversion. Base pool only — capability pools
    # run foreign images with their own deps. Run in a thread so
    # a slow cold import (OCC/ifcopenshell off a cold page cache) doesn't stall the
    # event loop's NATS keepalive while the worker is still starting up.
    if "base" in {c.lower() for c in capabilities}:
        await asyncio.get_running_loop().run_in_executor(None, _warm_convert_imports)

    stop = asyncio.Event()
    # Publish module-level so long in-handler poll loops (chained procedural_detail
    # waiting on the structural build) can wake early on shutdown.
    global _WORKER_STOP
    _WORKER_STOP = stop

    def _signal_handler() -> None:
        logger.info("worker: shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows: skip graceful signal wiring.
            pass

    # Set when the heartbeat has failed BUS_HEARTBEAT_FAILURE_LIMIT times running.
    # Distinguishes "we were asked to stop" from "we lost the bus and stopped
    # ourselves", which have to exit with different codes: a supervisor should
    # restart the second and not the first.
    bus_lost = asyncio.Event()

    heartbeat_task = asyncio.create_task(
        _heartbeat_until_stopped(publish=_publish_registration, stop=stop, bus_lost=bus_lost)
    )

    # The previous threadpool ran convert() in-process; that's been
    # replaced by a per-job forked subprocess (see subprocess_convert).
    # Keep the parameter on _process_one for now (callers may still
    # pass it) but no longer create one here.
    logger.info("worker: ready, polling %s", settings.queue.subject)
    _touch_liveness()  # seed the heartbeat before the first fetch so the probe has a fresh mtime
    rr = 0  # round-robin cursor over `subs`
    streak = 0  # consecutive productive fetches on the current pool
    try:
        while not stop.is_set():
            _touch_liveness()  # each pull round — a stalled fetch lets this go stale -> livenessProbe restart
            if not subs:
                # Qualification withheld everything. Stay up and keep
                # heartbeating rather than exiting: the registry row is the only
                # place that says WHY, and a worker that exits is indistinguishable
                # from one that was never started — which is the confusion this
                # whole design is meant to remove. Idle at the same cadence a
                # fetch would have taken, so the liveness file stays fresh.
                await asyncio.sleep(FETCH_TIMEOUT)
                continue
            cap, sub = subs[rr % len(subs)]
            try:
                msgs = await sub.fetch(batch=FETCH_BATCH, timeout=per_fetch_timeout)
            except asyncio.TimeoutError:
                msgs = []
            rr, streak = _advance_pool_cursor(rr, streak, bool(msgs))
            if not msgs:
                continue
            for msg in msgs:
                job_id = msg.data.decode("utf-8")
                # NATS message metadata carries the delivery counter.
                # We only get here if the previous attempt didn't ack
                # (typically: the worker died mid-conversion). Pass
                # the counter into _process_one so it can refuse to
                # retry past MAX_DELIVERIES.
                try:
                    delivery_count = int(msg.metadata.num_delivered)
                except Exception:
                    delivery_count = 1

                # Misrouted-message safety net. Routing is now done at
                # the NATS subject layer (each pool subscribes to its
                # own capability-suffixed subject), so a message
                # arriving here should always be one this pool can
                # handle. If it isn't — bug in routing or a job
                # enqueued before the upgrade — fail it immediately
                # rather than NAK-looping. NAK would burn through the
                # delivery budget and surface as the misleading
                # "worker exceeded N delivery attempts" error; the
                # explicit failure points at the real problem.
                peeked = await queue.get(job_id)
                if peeked is not None:
                    # component_build jobs are synthetic — no source
                    # file, so the extension-based routing guard
                    # doesn't apply. Routing was already pinned by
                    # the build endpoint via target_capability, and
                    # the per-spec handler resolves from the registry
                    # the worker preloaded at startup (ADA_WORKER_PRELOAD).
                    if peeked.target_format in (
                        "component_build",
                        "procedural_build",
                        "procedural_detail",
                        "procedural_relocations",
                        "procedural_export_xlsx",
                        "procedural_export_model",
                        "procedural_import_xlsx",
                        "equipment_bbox",
                        "plugin_job",
                    ):
                        can_handle = True
                        ext = ""
                    else:
                        ext = pathlib.PurePosixPath(peeked.source_key).suffix.lower()
                        legacy_ok = ext in LEGACY_CONVERT_EXTS and (ext_allow_set is None or ext in ext_allow_set)
                        can_handle = ext in source_ext_set or legacy_ok
                    if not can_handle:
                        misroute_msg = (
                            f"misrouted: pool capability {cap!r} "
                            f"can't handle .{ext.lstrip('.')} "
                            f"(supported here: {sorted(source_ext_set) or ['legacy convert']})"
                        )
                        logger.warning(
                            "worker: %s — job %s",
                            misroute_msg,
                            job_id,
                        )
                        try:
                            await queue.update(
                                job_id,
                                status=JOB_STATUS_ERROR,
                                stage="misrouted",
                                progress=0.0,
                                error=misroute_msg,
                            )
                            await _audit_done(
                                db_pool,
                                job_id,
                                "error",
                                misroute_msg,
                                time.monotonic(),
                            )
                        except Exception:
                            logger.exception(
                                "worker: failed to mark misrouted job %s as error",
                                job_id,
                            )
                        await msg.ack()
                        continue

                logger.info(
                    "worker: picked up job %s (delivery %d/%d)",
                    job_id,
                    delivery_count,
                    MAX_DELIVERIES,
                )

                # Hold the JetStream lease while the job runs: refresh the ack
                # deadline periodically so a long but healthy job is never
                # redelivered, while a worker that dies mid-job (OOM-killed pod,
                # crash) stops refreshing and the message is redelivered within
                # ~one short ack_wait — not the previous fixed 30 min window.
                ka_stop = asyncio.Event()

                async def _keep_alive(m=msg, jid=job_id) -> None:
                    while not ka_stop.is_set():
                        try:
                            await asyncio.wait_for(ka_stop.wait(), timeout=IN_PROGRESS_REFRESH_SECONDS)
                        except asyncio.TimeoutError:
                            try:
                                await m.in_progress()
                            except Exception:
                                logger.debug("worker: in_progress refresh failed for %s", jid)
                        else:
                            return

                ka_task = asyncio.create_task(_keep_alive())
                job_started_at = time.monotonic()
                try:
                    await _process_one(
                        job_id,
                        queue,
                        storage,
                        None,
                        db_pool,
                        delivery_count=delivery_count,
                    )
                except Exception as exc:  # noqa: BLE001 - one job must never kill the consumer
                    # Anything _process_one didn't handle itself (e.g. a transient
                    # S3 body timeout while streaming the source) used to escape
                    # here and CRASH THE WORKER PROCESS: the message was acked in
                    # the finally, so the job sat "running" forever in the UI,
                    # and every queued job showed "waiting for worker" until the
                    # pod restarted. Fail the JOB instead and keep consuming.
                    logger.exception("worker: job %s failed outside the handled paths", job_id)
                    try:
                        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="worker", error=str(exc))
                    except Exception:  # noqa: BLE001
                        logger.warning("worker: could not record job error for %s", job_id)
                    await _audit_done(db_pool, job_id, "error", str(exc), job_started_at)
                finally:
                    ka_stop.set()
                    try:
                        await ka_task
                    except Exception:
                        pass
                    await msg.ack()
                    # Release the freed-but-retained arena memory this job left in the parent, so it
                    # doesn't accumulate across the run and inflate the next conversion's fork baseline.
                    _trim_parent_memory()
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await queue.unregister_worker(worker_id)
        except Exception:
            logger.exception("worker: unregister failed (non-fatal)")
        await queue.close()
        if db_pool is not None:
            try:
                await db_pool.close()
            except Exception:
                logger.exception("worker: db pool close failed")
        logger.info("worker: stopped")

    if bus_lost.is_set():
        # Non-zero so a supervisor restarts us. Deliberately raised out here,
        # after the cleanup above: unregistering and closing are best-effort and
        # each already tolerate a dead connection, and skipping them would leave
        # a stale registry row behind on the one exit path that most needs the
        # row gone.
        raise SystemExit("worker: exiting after losing the connection to the bus")


def run() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    run()
