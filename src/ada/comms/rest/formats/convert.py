"""The registry-backed converter path — every ``ConverterRegistry`` target (``glb``, ``ifc``,
``step``, ``xml``, ``gnx``, ``obj``, ``stl``, ``gltf``, ``fem``, ``inp``, ``med``, ...).

Runs ``convert()`` in a forked child (crash isolation, rusage, heartbeat samples), streams the
output to object storage (gzip-at-rest for text formats and GLB, optional meshopt GLB
compression) and records metrics + provenance on the audit row. Registered as the catch-all
handler: an unknown ``target_format`` fails inside ``convert()`` with the same
``UnsupportedFormat`` message the old dispatch chain produced.
"""

from __future__ import annotations

import os
import time
import traceback as tb_module
from concurrent.futures import (  # noqa: F401 — kept for the legacy _process_one signature
    ThreadPoolExecutor,
)

from ada.config import logger

from .. import db as db_module
from ..converter import convert
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..subprocess_convert import (
    ConvertSample,
    IsolatedConvertResult,
    run_isolated_convert,
)
from ..worker.audit import _attach_cpp_profiles, _audit_done, _convert_meta_for
from ..worker.blobs import _ensure_sif_index
from ..worker.state import _touch_liveness
from .registry import JobContext, SourceFormatHandler


async def _run_convert(*, job: Job, ctx: JobContext) -> None:
    job_id = job.job_id
    scope = ctx.scope
    storage = ctx.storage
    queue = ctx.queue
    db_pool = ctx.db_pool
    started_at = ctx.started_at
    src_path = ctx.src_path
    _on_progress = ctx.on_progress
    fetch = ctx.fetch
    src_suffix = fetch.src_suffix
    sif_reduced = fetch.sif_reduced
    sin_source_uri = fetch.sin_source_uri
    source_fetch_mode = fetch.source_fetch_mode
    fetch_ms = fetch.fetch_ms
    fetch_bytes = fetch.fetch_bytes
    settings = ctx.settings
    profile_enabled = settings.profile_enabled
    env_overrides = settings.env_overrides
    timeout_s = settings.timeout_s
    per_job = getattr(job, "conversion_options", None) or {}

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
        from ada.visit.scene_handling.scene_from_step_stream import _cgroup_cpu_quota

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


class ConvertHandler(SourceFormatHandler):
    """Catch-all: any ``target_format`` not claimed by a synthetic handler."""

    kind = "convert"

    def supports(self, job: Job) -> bool:
        return True

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_convert(job=job, ctx=ctx)
