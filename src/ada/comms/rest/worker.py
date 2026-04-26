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
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable

from ada.config import logger

from .config import load_settings
from .converter import convert_to_glb
from .queue import (
    JOB_STATUS_DONE,
    JOB_STATUS_ERROR,
    JOB_STATUS_RUNNING,
    Job,
    JobQueue,
)
from .storage import Storage


# How long the pull-subscriber waits per fetch round before re-issuing.
# Short enough to react to shutdown; long enough not to spam NATS.
FETCH_TIMEOUT = 5.0
# Workers fetch one message at a time — conversions are heavy and we
# don't want to hold a batch of acks open during a long-running job.
FETCH_BATCH = 1


async def _process_one(
    job_id: str,
    queue: JobQueue,
    storage: Storage,
    pool: ThreadPoolExecutor,
) -> None:
    job = await queue.get(job_id)
    if job is None:
        logger.warning("worker: job %s not found in KV; skipping", job_id)
        return

    # Skip if a previous run already produced the derived blob. This is
    # the cheap safety net for redelivered messages.
    if await storage.exists(job.derived_key):
        await queue.update(
            job_id,
            status=JOB_STATUS_DONE,
            stage="cached",
            progress=1.0,
            error=None,
        )
        return

    await queue.update(job_id, status=JOB_STATUS_RUNNING, stage="loading", progress=0.05)

    try:
        source_bytes = await storage.get_bytes(job.source_key)
    except FileNotFoundError as exc:
        logger.warning("worker: source %s missing for job %s", job.source_key, job_id)
        await queue.update(
            job_id, status=JOB_STATUS_ERROR, stage="loading", error=str(exc)
        )
        return

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

    try:
        glb_bytes = await loop.run_in_executor(
            pool, convert_to_glb, source_bytes, job.source_key, _on_progress
        )
    except Exception as exc:
        logger.exception("worker: conversion failed for %s", job.source_key)
        await queue.update(
            job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc)
        )
        return

    await queue.update(job_id, stage="uploading", progress=0.95)
    try:
        await storage.put_bytes(job.derived_key, glb_bytes)
    except Exception as exc:
        logger.exception("worker: upload failed for %s", job.derived_key)
        await queue.update(
            job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc)
        )
        return

    await queue.update(
        job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None
    )


async def _run() -> None:
    settings = load_settings()
    if settings.queue.url is None:
        raise SystemExit("ADA_VIEWER_NATS_URL not set; nothing for the worker to do")

    storage = Storage.from_settings(settings)
    queue = JobQueue(settings.queue)
    await queue.connect()

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
                logger.info("worker: picked up job %s", job_id)
                try:
                    await _process_one(job_id, queue, storage, pool)
                finally:
                    await msg.ack()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        await queue.close()
        logger.info("worker: stopped")


def run() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    run()
