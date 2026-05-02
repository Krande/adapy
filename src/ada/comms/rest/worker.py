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
from concurrent.futures import ThreadPoolExecutor  # noqa: F401 — kept for the legacy _process_one signature
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
        if db_pool is not None:
            async def _read_bool_setting(key: str) -> str | None:
                try:
                    return await db_module.get_setting(db_pool, key)
                except Exception:
                    logger.exception("worker: failed to read %s setting", key)
                    return None

            v = await _read_bool_setting("profile_conversions")
            profile_enabled = (v or "").strip().lower() in {"1", "true", "yes", "on"}

            # setting key → env var name. Worker passes the raw
            # truthy/falsy text through; surfaces.py /
            # converter.py do the same parsing they always have, so
            # the env-driven and admin-driven paths agree on edge
            # cases (e.g. "yes" / "no").
            _env_map = {
                "use_sat_pcurves": "ADA_USE_SAT_PCURVES",
                "pcurve_drive_edge": "ADA_PCURVE_DRIVE_EDGE",
                "skip_shapefix": "ADA_SKIP_SHAPEFIX",
                "merge_meshes": "ADA_GLB_MERGE_MESHES",
            }
            for skey, env_name in _env_map.items():
                raw = await _read_bool_setting(skey)
                if raw is not None and raw.strip() != "":
                    env_overrides[env_name] = raw

        # Per-job overrides win over global settings. ``None`` clears
        # an env var, allowing a job to ask "ignore the global
        # toggle, run with adapy's code default" without restarting.
        per_job = getattr(job, "conversion_options", None) or {}
        if per_job:
            _env_map_full = {
                "use_sat_pcurves": "ADA_USE_SAT_PCURVES",
                "pcurve_drive_edge": "ADA_PCURVE_DRIVE_EDGE",
                "skip_shapefix": "ADA_SKIP_SHAPEFIX",
                "merge_meshes": "ADA_GLB_MERGE_MESHES",
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
                profile_enabled = str(per_job["profile_conversions"]).strip().lower() in {
                    "1", "true", "yes", "on"
                }

        # Forward progress from the converter to the KV-backed queue,
        # throttled so a chatty stage doesn't spam writes.
        last_kv_write = 0.0

        async def _on_progress(stage: str, frac: float) -> None:
            nonlocal last_kv_write
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

        # Run convert() in a forked child. Crash isolation + rusage on
        # exit + per-/proc heartbeat sampling all in one. See
        # subprocess_convert.run_isolated_convert for the rationale.
        try:
            iresult: IsolatedConvertResult = await run_isolated_convert(
                convert,
                src_path,
                job.source_key,
                job.target_format,
                convert_kwargs={"step": job.step, "field": job.field},
                on_progress=_on_progress,
                on_sample=_on_sample,
                profile_in_child=profile_enabled,
                env_overrides=env_overrides or None,
            )
        except Exception as exc:
            # Failure in the parent-side machinery (fork, /proc reads,
            # asyncio plumbing). The child either never started or we
            # lost track of it; treat as a worker error.
            logger.exception("worker: subprocess wrapper failed for %s", job_id)
            trace = tb_module.format_exc()
            await queue.update(
                job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc)
            )
            await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=trace)
            return

        # Map the isolated result back to the existing audit/error flow.
        if iresult.exit_code != 0 or iresult.out_bytes is None:
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
                    job.source_key, iresult.signal_name,
                )
            else:
                logger.error("worker: conversion failed for %s -> %s: %s",
                             job.source_key, job.target_format, err_msg)
            await queue.update(
                job_id, status=JOB_STATUS_ERROR, stage="convert", error=err_msg,
            )
            metrics = dict(iresult.final_metrics)
            metrics["profile_key"] = await _maybe_upload_profile_bytes(iresult.profile_bytes)
            await _audit_done(
                db_pool, job_id, "error", err_msg, started_at,
                traceback=trace, metrics=metrics,
            )
            return

        out_bytes = iresult.out_bytes

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
            metrics = dict(iresult.final_metrics)
            metrics["profile_key"] = await _maybe_upload_profile_bytes(iresult.profile_bytes)
            await _audit_done(
                db_pool, job_id, "error", str(exc), started_at,
                traceback=trace, metrics=metrics,
            )
            return

        # Conversion + upload succeeded — collect metrics and (optionally)
        # the cProfile dump from the child.
        metrics = dict(iresult.final_metrics)
        metrics["profile_key"] = await _maybe_upload_profile_bytes(iresult.profile_bytes)

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

    # The previous threadpool ran convert() in-process; that's been
    # replaced by a per-job forked subprocess (see subprocess_convert).
    # Keep the parameter on _process_one for now (callers may still
    # pass it) but no longer create one here.
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
                        job_id, queue, storage, None, db_pool,
                        delivery_count=delivery_count,
                    )
                finally:
                    await msg.ack()
    finally:
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
