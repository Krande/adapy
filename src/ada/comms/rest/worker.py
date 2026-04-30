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
import os
import pathlib
import signal
import tempfile
import time
import traceback as tb_module
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable

import asyncpg

from ada.config import logger

from . import db as db_module
from .bundle import BundleError
from .config import load_settings
from .converter import convert
from .queue import (
    JOB_STATUS_DONE,
    JOB_STATUS_ERROR,
    JOB_STATUS_RUNNING,
    Job,
    JobQueue,
)
from .scope import Scope
from .storage import Storage


def _scope_of(job: Job) -> Scope:
    """Reconstruct the Scope a job's source/derived blobs live under.
    Defaults to ``shared`` for jobs serialized before scope_kind existed.
    """
    if job.scope_kind == "project" and job.scope_id:
        return Scope.project(job.scope_id)
    if job.scope_kind == "user" and job.scope_id:
        return Scope.user(job.scope_id)
    return Scope.shared()


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
        )
    except Exception:
        logger.exception("worker: audit update failed for job %s", job_id)


# Lazy psutil import — keep the worker tolerant if it's missing on
# some platform. The metrics dict is None in that case and the audit
# update simply passes None for every metric.
try:
    import psutil as _psutil  # type: ignore[import-not-found]
except Exception:  # pragma: no cover — psutil pinned in pixi env
    _psutil = None  # type: ignore[assignment]


def _capture_metrics_start():
    """Snapshot CPU / IO counters at job start. Returns an opaque
    object that ``_capture_metrics_end`` consumes; ``None`` when
    psutil isn't available so callers can fall through cleanly."""
    if _psutil is None:
        return None
    proc = _psutil.Process()
    cpu = proc.cpu_times()
    io = None
    try:
        # io_counters is unavailable on macOS; metrics still produce
        # CPU + RSS in that case.
        io = proc.io_counters()
    except Exception:
        io = None
    return {"proc": proc, "cpu_start": cpu, "io_start": io}


def _capture_metrics_end(start) -> dict:
    """Build the audit-row metrics dict from the start snapshot. Any
    counter we can't sample is omitted (None) so the column stays NULL
    rather than zero — which would falsely signal "ran but did
    nothing"."""
    if start is None or _psutil is None:
        return {}
    proc = start["proc"]
    out: dict = {}
    try:
        cpu_now = proc.cpu_times()
        out["cpu_user_ms"] = int(max(0.0, cpu_now.user - start["cpu_start"].user) * 1000)
        out["cpu_sys_ms"] = int(max(0.0, cpu_now.system - start["cpu_start"].system) * 1000)
    except Exception:
        pass
    try:
        # memory_info().rss is current RSS; use memory_info_ex / peak
        # when available, otherwise fall back to current. The job is
        # synchronous and short, so current ≈ peak in practice.
        mem = proc.memory_info()
        out["peak_rss_kb"] = int(getattr(mem, "rss", 0)) // 1024
    except Exception:
        pass
    if start["io_start"] is not None:
        try:
            io_now = proc.io_counters()
            out["read_bytes"] = int(
                max(0, io_now.read_bytes - start["io_start"].read_bytes)
            )
            out["write_bytes"] = int(
                max(0, io_now.write_bytes - start["io_start"].write_bytes)
            )
        except Exception:
            pass
    return out


async def _process_one(
    job_id: str,
    queue: JobQueue,
    storage: Storage,
    pool: ThreadPoolExecutor,
    db_pool: asyncpg.Pool | None,
    delivery_count: int = 1,
) -> None:
    started_at = time.monotonic()
    job = await queue.get(job_id)
    if job is None:
        logger.warning("worker: job %s not found in KV; skipping", job_id)
        return

    scope = _scope_of(job)

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
            job_id, status=JOB_STATUS_ERROR, stage="aborted", progress=0.0, error=msg,
        )
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return

    # Skip if a previous run already produced the derived blob. This is
    # the cheap safety net for redelivered messages.
    if await storage.exists(scope, job.derived_key):
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

    # Stream source to a worker-local tempfile rather than buffering
    # the whole payload in RAM. Big result decks (Sesam SIF can be
    # 950 MB+) blow up the worker pod otherwise; smaller sources still
    # benefit from skipping the bytes/path round-trip.
    src_suffix = pathlib.PurePosixPath(job.source_key).suffix or ""
    src_fd, src_name = tempfile.mkstemp(suffix=src_suffix)
    os.close(src_fd)
    src_path = pathlib.Path(src_name)
    try:
        try:
            await storage.stream_to_path(scope, job.source_key, src_path)
        except FileNotFoundError as exc:
            logger.warning("worker: source %s missing for job %s", job.source_key, job_id)
            await queue.update(
                job_id, status=JOB_STATUS_ERROR, stage="loading", error=str(exc)
            )
            await _audit_done(db_pool, job_id, "error", str(exc), started_at)
            return

        # Capture resource counters around the convert call. psutil
        # samples CPU times, RSS, and (Linux) per-process IO bytes; the
        # delta lands on the audit row regardless of success or error
        # so admins can see where time/memory went on a failed job.
        metrics_start = _capture_metrics_start()

        # Profile setting flips via the admin panel and is read fresh
        # per job — admins can flip it on, send a representative job,
        # and flip it off without a worker restart. No cache: one
        # extra DB round-trip per job is negligible next to a
        # tessellation pass.
        profile_enabled = False
        if db_pool is not None:
            try:
                v = await db_module.get_setting(db_pool, "profile_conversions")
                profile_enabled = (v or "").strip().lower() in {"1", "true", "yes", "on"}
            except Exception:
                logger.exception("worker: failed to read profile_conversions setting")

        import cProfile
        profiler = cProfile.Profile() if profile_enabled else None

        # Hop into a thread for the conversion — ada-py / trimesh are
        # synchronous and CPU-heavy. The progress callback re-enters the
        # asyncio loop via run_coroutine_threadsafe.
        loop = asyncio.get_running_loop()
        last_kv_write = 0.0

        def _on_progress(stage: str, frac: float) -> None:
            # Throttle KV writes; conversion may emit many updates.
            nonlocal last_kv_write
            now = time.monotonic()
            if now - last_kv_write < 0.25 and frac < 1.0:
                return
            last_kv_write = now
            try:
                asyncio.run_coroutine_threadsafe(
                    queue.update(job_id, stage=stage, progress=frac),
                    loop,
                )
            except RuntimeError:
                # Loop closed during shutdown — drop the update.
                pass

        def _convert_call():
            # Wrap the synchronous conversion in cProfile when enabled.
            # The profiler is enabled inside the worker thread so its
            # samples capture the conversion stack and not the
            # asyncio scheduler.
            if profiler is not None:
                profiler.enable()
            try:
                return convert(
                    src_path,
                    job.source_key,
                    job.target_format,
                    _on_progress,
                    step=job.step,
                    field=job.field,
                )
            finally:
                if profiler is not None:
                    profiler.disable()

        try:
            out_bytes = await loop.run_in_executor(pool, _convert_call)
        except BundleError as exc:
            # User-visible bundle problem (missing include, mixed formats,
            # ambiguous entry, ...). The message is already operator-
            # friendly — log at info, not exception, so the worker's stderr
            # doesn't fill with stack traces for what is really user input.
            logger.info("worker: bundle rejected for %s: %s", job.source_key, exc)
            await queue.update(
                job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc)
            )
            metrics = _capture_metrics_end(metrics_start)
            await _audit_done(db_pool, job_id, "error", str(exc), started_at, metrics=metrics)
            return
        except Exception as exc:
            logger.exception("worker: conversion failed for %s -> %s", job.source_key, job.target_format)
            trace = tb_module.format_exc()
            await queue.update(
                job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc)
            )
            metrics = _capture_metrics_end(metrics_start)
            await _audit_done(
                db_pool, job_id, "error", str(exc), started_at,
                traceback=trace, metrics=metrics,
            )
            return

        await queue.update(job_id, stage="uploading", progress=0.95)
        # Gzip text-format outputs (IFC, Genie XML); GLB is binary geometry
        # that doesn't compress meaningfully and is what the in-browser
        # viewer fetches on the hot path.
        derived_encoding = "gzip" if job.target_format in {"ifc", "xml"} else None
        try:
            await storage.put_bytes(scope, job.derived_key, out_bytes, content_encoding=derived_encoding)
        except Exception as exc:
            logger.exception("worker: upload failed for %s", job.derived_key)
            trace = tb_module.format_exc()
            await queue.update(
                job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc)
            )
            metrics = _capture_metrics_end(metrics_start)
            await _audit_done(
                db_pool, job_id, "error", str(exc), started_at,
                traceback=trace, metrics=metrics,
            )
            return

        # Conversion + upload succeeded — collect metrics + (optionally)
        # serialize and upload the cProfile dump. Profile upload errors
        # don't fail the job; they just leave profile_key NULL.
        metrics = _capture_metrics_end(metrics_start)
        if profiler is not None:
            try:
                prof_path = pathlib.Path(tempfile.mkstemp(suffix=".prof")[1])
                try:
                    profiler.dump_stats(str(prof_path))
                    prof_bytes = prof_path.read_bytes()
                finally:
                    try:
                        prof_path.unlink()
                    except OSError:
                        pass
                profile_key = f"_derived/{job.source_key}.{job_id}.prof"
                await storage.put_bytes(scope, profile_key, prof_bytes)
                metrics["profile_key"] = profile_key
            except Exception:
                logger.exception("worker: profile upload failed for job %s", job_id)

        await queue.update(
            job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None
        )
        await _audit_done(db_pool, job_id, "done", None, started_at, metrics=metrics)
    finally:
        try:
            src_path.unlink()
        except OSError:
            pass


async def _run() -> None:
    settings = load_settings()
    if settings.queue.url is None:
        raise SystemExit("ADA_VIEWER_NATS_URL not set; nothing for the worker to do")

    storage = Storage.from_settings(settings)
    queue = JobQueue(settings.queue)
    await queue.connect()

    # Self-identify so the viewer's /api/config can surface the
    # currently-running worker image tag. Best-effort: a KV write
    # failure shouldn't keep the worker from accepting jobs.
    image_tag = os.environ.get("ADA_IMAGE_TAG", "").strip()
    if image_tag:
        try:
            await queue.set_meta("worker_image_tag", image_tag)
            logger.info("worker: published image tag %s", image_tag)
        except Exception:
            logger.exception("worker: failed to publish image tag (non-fatal)")

    # Optional DB pool — only used to flip audit_log rows from 'queued'
    # to 'done'/'error' when a job finishes. Without it the worker still
    # functions; admin panel rows just stay at 'queued'. Migrations are
    # the API's job, so the worker does NOT call init_pool — it builds a
    # plain pool and trusts the schema is already applied.
    db_pool: asyncpg.Pool | None = None
    if settings.database_url:
        try:
            db_pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=1,
                max_size=4,
                max_inactive_connection_lifetime=600.0,
            )
            logger.info("worker: db pool ready")
        except Exception:
            logger.exception("worker: db connect failed; running without audit updates")

    sub = await queue.pull_subscribe()

    stop = asyncio.Event()

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

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="convert")

    logger.info("worker: ready, polling %s", settings.queue.subject)
    try:
        while not stop.is_set():
            try:
                msgs = await sub.fetch(batch=FETCH_BATCH, timeout=FETCH_TIMEOUT)
            except asyncio.TimeoutError:
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
                logger.info(
                    "worker: picked up job %s (delivery %d/%d)",
                    job_id, delivery_count, MAX_DELIVERIES,
                )
                try:
                    await _process_one(
                        job_id, queue, storage, pool, db_pool,
                        delivery_count=delivery_count,
                    )
                finally:
                    await msg.ack()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        await queue.close()
        if db_pool is not None:
            try:
                await db_pool.close()
            except Exception:
                logger.exception("worker: db pool close failed")
        logger.info("worker: stopped")


def run() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    run()
