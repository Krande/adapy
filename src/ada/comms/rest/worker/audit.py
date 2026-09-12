"""Audit-row bookkeeping: final outcome patching (DB or over the API), conversion provenance and
the adacpp profiler summaries parsed out of the captured child log.
"""

from __future__ import annotations

import asyncio
import pathlib
import time

import asyncpg

from ada.config import logger

from .. import db as db_module
from .. import failure_capture
from ..queue import Job
from . import state
from .source_nodes import _rest_source_nodes_config


async def _report_job_status_over_api(job_id: str, payload: dict) -> bool:
    """Tell the API what a job is doing, for a worker with no database pool.

    WHY: both audit hops -- the running mark and the final outcome -- are gated on
    a pool, so on a pool-less worker the audit row stays `queued` for ever while
    the job runs, finishes and is swept. The queue record is accurate the whole
    time, so the conversion toast follows along and the Audit tab shows every job
    on that pool as permanently pending. An operator then cannot tell a healthy
    pool from a broken one, and anything reasoning over non-terminal rows blocks on
    jobs that finished minutes ago.

    The same argument, and the same mechanism, as the source-node REST recorder: a
    worker that already reaches this API should not need Postgres credentials to
    say what it just did.

    BEST EFFORT, ALWAYS. Returns whether it landed, and never raises: a report is
    commentary on work that has already happened, and failing a finished job
    because its commentary did not arrive would be strictly worse than the gap this
    closes. Blocking HTTP on the event loop's executor, because this is called from
    async code and the recorder idiom here is urllib.
    """
    cfg = _rest_source_nodes_config()
    if cfg is None:
        return False
    base, token = cfg

    def _post() -> bool:
        import json as _json
        import urllib.error
        import urllib.parse
        import urllib.request

        url = f"{base}/api/jobs/{urllib.parse.quote(job_id, safe='')}/status"
        body = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                resp.read()
            return True
        except urllib.error.HTTPError as exc:
            # THE BODY IS READ AND LOGGED, not just the status. A bare code is how
            # two separate failures today each cost an hour: the server says what
            # went wrong in the body, and throwing it away leaves the reader
            # inferring from a number. Read defensively -- a body that cannot be
            # read must not replace the error with a different one.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001
                pass
            # Logged at warning rather than retried: a 4xx will be just as wrong
            # next time.
            #
            # 404/405 IS NAMED, because the bare status is actively misleading. A
            # viewer that predates this route has no handler for the path, so the
            # SPA's catch-all answers the GET shape and POST comes back 405 -- a
            # status that reads as "the API refused this" when it means "this API
            # does not have the feature yet". An afternoon went into a 405 that
            # meant something equally structural, so this one says what to do.
            if exc.code in (404, 405):
                logger.warning(
                    "worker: this viewer has no POST /api/jobs/{id}/status route (%s), so job "
                    "outcomes stay `queued` in the audit log until it is updated. The jobs "
                    "themselves are unaffected.",
                    exc.code,
                )
            else:
                # A 5xx here is the API failing to record, which is worth the body:
                # it is the only place the reason exists.
                logger.warning(
                    "worker: audit report for job %s refused (%s)%s",
                    job_id,
                    exc.code,
                    f": {detail}" if detail else "",
                )
            return False
        except Exception as exc:  # noqa: BLE001 - commentary must not sink a job
            logger.warning("worker: audit report for job %s did not reach the API: %s", job_id, exc)
            return False

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _post)
    except Exception:  # noqa: BLE001
        logger.exception("worker: audit report for job %s could not be dispatched", job_id)
        return False


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
        # A worker without a pool reports over the API instead. Without this the
        # row never leaves `queued`, so every job this pool ever ran reads as
        # pending -- see _report_job_status_over_api.
        metrics = metrics or {}
        await _report_job_status_over_api(
            job_id,
            {
                "status": status,
                "error": error,
                "traceback": traceback,
                "duration_ms": int((time.time() - started_at) * 1000),
                "worker_image_tag": state._WORKER_IMAGE_TAG,
                **{
                    key: metrics[key]
                    for key in ("cpu_user_ms", "cpu_sys_ms", "peak_rss_kb", "read_bytes", "write_bytes")
                    if metrics.get(key) is not None
                },
            },
        )
        return
    metrics = metrics or {}
    # Preserve the input of a failed job while it still exists: the row outlives
    # the blob, and a user-scope source can be deleted at any time. Resolved from
    # the row rather than the call site so every error path is covered by this one
    # hook. Best-effort and deduplicated; None when disabled or ineligible.
    failure_key = None
    if status == "error" and state._WORKER_STORAGE is not None:
        failure_key = await failure_capture.capture_for_job(state._WORKER_STORAGE, db_pool, db_module, job_id)
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
            worker_image_tag=state._WORKER_IMAGE_TAG,
            convert_meta=metrics.get("convert_meta"),
            failure_key=failure_key,
        )
    except Exception:
        logger.exception("worker: audit update failed for job %s", job_id)


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
