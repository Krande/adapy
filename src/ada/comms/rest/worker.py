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
import functools
import os
import pathlib
import shutil
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
from .converter import LEGACY_CONVERT_EXTS, ConverterRegistry, convert
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

# Per-source-suffix sidecar files that the worker co-downloads next to
# the main payload so format-specific readers find them by basename.
# Keep this conservative — a 404 on an absent sibling is silent, but
# we still pay one S3 HEAD per attempt. Add entries here as readers
# grow new sidecar needs; ``.adapy_fem.json`` is the code_aster
# lineage + per-element tessellation companion.
_SIDECAR_SIBLINGS: dict[str, tuple[str, ...]] = {
    ".rmed": (".adapy_fem.json",),
}


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
    * ``_derived/<src>.fea/fea.<field>.bin`` × N (gzip)

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
                    await asyncio.wait_for(
                        heartbeat_stop.wait(), timeout=HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    heartbeat_progress["value"] = min(
                        HEARTBEAT_MAX, heartbeat_progress["value"] + HEARTBEAT_INC
                    )
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
            await queue.update(
                job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc)
            )
            await _audit_done(
                db_pool, job_id, "error", str(exc), started_at, traceback=trace,
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
                # Compression policy mirrors the API-side endpoint:
                # gzip the manifest JSON and field blobs (compress
                # well), skip the mesh GLB (already binary-packed).
                content_encoding = (
                    "gzip" if produced.suffix.lower() in {".json", ".bin"} else None
                )
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
                job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc),
            )
            await _audit_done(
                db_pool, job_id, "error", str(exc), started_at, traceback=trace,
            )
            return

        await queue.update(
            job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None,
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
        await queue.update(
            job_id, status=JOB_STATUS_ERROR, stage="convert", error=str(exc)
        )
        await _audit_done(
            db_pool, job_id, "error", str(exc), started_at, traceback=trace,
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
            job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc),
        )
        await _audit_done(
            db_pool, job_id, "error", str(exc), started_at, traceback=trace,
        )
        return

    await queue.update(
        job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None,
    )
    await _audit_done(db_pool, job_id, "done", None, started_at)


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
                    sib_key, job_id,
                )

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

        # FEA streaming-viewer artefact bake — sibling code path to
        # the convert pipeline. The bake produces multiple files (mesh
        # GLB + manifest + per-field blobs) under
        # `_derived/<src>.fea/`, which doesn't fit the convert
        # contract of "one bytes blob per derived_key". Runs in-process
        # in a thread executor; the bake is pure Python (h5py + trimesh)
        # without the native-crash exposure that justifies fork
        # isolation for the convert path.
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
        # pcurve_drive_edge / skip_shapefix) still flow via env vars
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
                },
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
    worker_id = (os.environ.get("HOSTNAME", "").strip() or f"local-{os.getpid()}")
    capabilities = [
        c.strip()
        for c in os.environ.get("ADA_WORKER_CAPABILITIES", "base").split(",")
        if c.strip()
    ]
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
    # Optional per-pod allowlist. Capability workers (e.g. asa-abacpp)
    # FROM the base image and so inherit its full stream-reader
    # registry — without this gate they'd race the base pool for
    # extensions they don't actually need to handle (e.g. ``.rmed``)
    # and, when running stale code, fail those jobs. The allowlist
    # is comma-separated source suffixes (``.odb,.sqlite``); leading
    # dots optional. Unset → handle everything in the registry, which
    # is the right default for the base worker.
    allow_env = os.environ.get("ADA_WORKER_EXT_ALLOW", "").strip()
    if allow_env:
        ext_allow_set: set[str] | None = {
            ("." + e.strip().lstrip(".")) .lower()
            for e in allow_env.split(",")
            if e.strip()
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
    full_matrix = ConverterRegistry.matrix()
    if ext_allow_set is not None:
        conversions = [m for m in full_matrix if m["from"] in ext_allow_set]
    else:
        conversions = full_matrix

    async def _publish_registration() -> None:
        try:
            await queue.register_worker(
                worker_id,
                {
                    "image_tag": image_tag or None,
                    "capabilities": capabilities,
                    "source_exts": source_exts,
                    "conversions": conversions,
                    "started_at": started_at,
                    "last_heartbeat": time.time(),
                },
            )
        except Exception:
            logger.exception("worker: register_worker failed (non-fatal)")

    await _publish_registration()
    logger.info(
        "worker: registered id=%s capabilities=%s",
        worker_id, ",".join(capabilities),
    )

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

    # Heartbeat loop — re-publish the registration every 15 s so the
    # admin panel can filter stale workers (a pod that crashed without
    # graceful shutdown will fall off the list within HEARTBEAT_STALE_S).
    async def _heartbeat_loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                await _publish_registration()
            else:
                return  # stop set — exit cleanly

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

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

                # Capability gate. Multi-pool deployments fan one
                # JetStream subject across heterogeneous workers
                # (base + capability pools that share the same queue).
                # If this pool's stream-reader registry doesn't cover
                # the job's source extension AND the legacy /convert
                # pipeline can't handle it either, NAK with a small
                # delay so a more capable worker has a chance to grab
                # the redelivery. Cap the dance at a few rounds so a
                # missing-capability misroute eventually surfaces as
                # a real bake error rather than spinning forever.
                if delivery_count <= 3:
                    peeked = await queue.get(job_id)
                    if peeked is not None:
                        ext = pathlib.PurePosixPath(
                            peeked.source_key
                        ).suffix.lower()
                        # Legacy /convert path also gated by the
                        # allowlist when set — otherwise an abacpp pod
                        # restricted to .odb would still pick up
                        # legacy converter jobs (.ifc, .step, …) just
                        # because LEGACY_CONVERT_EXTS doesn't go
                        # through the registry.
                        legacy_ok = ext in LEGACY_CONVERT_EXTS and (
                            ext_allow_set is None or ext in ext_allow_set
                        )
                        can_handle = ext in source_ext_set or legacy_ok
                        if not can_handle:
                            logger.info(
                                "worker: NAK job %s ext=%s not in registry "
                                "(have stream=%s legacy convert) delivery=%d",
                                job_id, ext, sorted(source_ext_set),
                                delivery_count,
                            )
                            try:
                                await msg.nak(delay=2.0)
                            except Exception:
                                logger.exception(
                                    "worker: nak failed for %s", job_id,
                                )
                            continue

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


def run() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    run()
