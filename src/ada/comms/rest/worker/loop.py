"""The worker process: boot, register, subscribe to the capability pools and pull jobs until
stopped. ``run()`` is the ``python -m ada.comms.rest.worker`` entrypoint.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Awaitable, Callable

import asyncpg

from ada.config import logger

from .. import db as db_module
from ..config import load_settings
from ..plugin_registry import discover_local_plugins
from ..queue import JOB_STATUS_ERROR, JobQueue
from ..storage import Storage
from . import state
from .advertise import _capture_worker_packages
from .audit import _audit_done
from .memory import _trim_parent_memory
from .pools import (
    BUS_HEARTBEAT_FAILURE_LIMIT,
    BUS_HEARTBEAT_SECONDS,
    FETCH_BATCH,
    FETCH_TIMEOUT,
    IN_PROGRESS_REFRESH_SECONDS,
    MAX_DELIVERIES,
    _advance_pool_cursor,
    _bool_env,
    _per_fetch_timeout,
    _pool_capabilities,
    _worker_id,
)
from .process import _process_one, _should_skip_cancelled
from .registration import build_registration
from .routing import misroute_reason
from .source_nodes import _rest_source_nodes_config
from .state import _touch_liveness


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
    state._WORKER_STORAGE = storage
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
    discover_local_plugins("worker")

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

    reg = await build_registration(queue)
    worker_id = reg.worker_id
    capabilities = reg.capabilities
    ext_allow_set = reg.ext_allow_set
    source_ext_set = reg.source_ext_set
    _publish_registration = reg.publish

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
        elif state._WORKER_IMAGE_TAG:
            try:
                await db_module.upsert_worker_packages(
                    db_pool,
                    worker_image_tag=state._WORKER_IMAGE_TAG,
                    packages=_capture_worker_packages(),
                )
            except Exception:
                logger.exception("worker: package manifest capture failed")
    else:
        # No DATABASE_URL at all. This is a SUPPORTED way to run a worker -- the
        # pool belongs to the API service, and everything that genuinely needs
        # one refuses itself with a clear message -- but two consequences are
        # invisible unless stated here, because both look like faults later:
        #
        #   * the audit row the API wrote when the job was enqueued is never
        #     patched with its outcome, so the run appears to stop at "queued"
        #     forever rather than showing as done;
        #   * no package manifest is recorded, so the admin UI has none to show
        #     for this worker, ever.
        #
        # Neither is an error and neither can be retried into working, so this
        # is the one place that can say so.
        logger.info(
            "worker: no DATABASE_URL — job outcomes will not be recorded in the audit log, "
            "and this worker reports no package manifest. Both are expected without a pool."
        )
        # Source-node change tracking is the third consequence, and unlike the
        # other two it has an alternative -- so say which way this worker is
        # configured. Silence here is what made a pool-less worker look like it
        # was tracking changes when it was discarding them.
        _sn_cfg = _rest_source_nodes_config()
        if _sn_cfg is not None:
            logger.info(
                "worker: source-node changes will be recorded through the API at %s "
                "(ADA_VIEWER_API_URL + ADA_VIEWER_TOKEN)",
                _sn_cfg[0],
            )
        else:
            logger.info(
                "worker: source-node changes will NOT be recorded — a plugin that tracks an "
                "external source will report that it cannot record. Set ADA_VIEWER_API_URL and "
                "ADA_VIEWER_TOKEN to record over the API instead of a pool."
            )

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
    state._WORKER_STOP = stop

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
                    misroute_msg = misroute_reason(
                        peeked.target_format,
                        peeked.source_key,
                        cap=cap,
                        source_ext_set=source_ext_set,
                        ext_allow_set=ext_allow_set,
                    )
                    if misroute_msg is not None:
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

                # Already cancelled before anyone started it? Ack and drop.
                #
                # Every other cancel check in this file is MID-RUN -- the
                # convert watchdog's `_cancel_check`, the plugin job's 2-second
                # poller. Both assume the job is running, which means a job
                # cancelled while queued was still picked up, started, and only
                # stopped a couple of seconds later. Harmless for a cancel
                # clicked during a normal queue wait; not harmless for a job
                # cancelled precisely BECAUSE nothing was ever going to serve
                # it (see the admin cancel route), where the worker that
                # eventually appears is the one that should not touch it.
                #
                # Best-effort: no pool means no audit row to consult, and a
                # failed query must not stop a worker from doing its job.
                if await _should_skip_cancelled(db_pool, job_id):
                    logger.info("worker: job %s was cancelled before it started — dropping", job_id)
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
