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
from concurrent.futures import (  # noqa: F401 — kept for the legacy _process_one signature
    ThreadPoolExecutor,
)
from typing import Awaitable, Callable

import asyncpg

from ada.config import logger

from . import db as db_module
from .config import load_settings
from .converter import LEGACY_CONVERT_EXTS, ConverterRegistry, convert
from .queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, JOB_STATUS_RUNNING, Job, JobQueue
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

# While a job runs we refresh the JetStream ack deadline with
# ``msg.in_progress()`` on this cadence. A live worker keeps extending its
# lease; the moment it dies (OOM-killed pod, node failure, crash) the refreshes
# stop and JetStream redelivers within ~one ack_wait (see queue._ACK_WAIT_SECONDS)
# instead of the old fixed 30 min — so a poison/OOM job is detected and
# dead-lettered (MAX_DELIVERIES) in minutes, not ~80. Must be comfortably shorter
# than ack_wait; the conversion runs in a child process so the parent event loop
# stays free to fire these.
IN_PROGRESS_REFRESH_SECONDS = 30

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
            worker_image_tag=_WORKER_IMAGE_TAG,
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


async def _run_parity_validation(
    *,
    job: Job,
    src_path: pathlib.Path,
    scope,
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    _on_progress: Callable[[str, float], Awaitable[None]],
) -> None:
    """Cross-format visual-parity validation for one source (target_format=='parity').

    Re-derives the source to each structure-preserving format, reloads, and
    compares the visualized-element count (ada.cadit.visual_parity). Produces no
    derived blob: the structured per-format result goes to the ``audit_parity``
    table and the cell is audited done/error (a mismatch maps to ``error`` so it
    surfaces in the run's failed cells). Never raises.

    Re-deriving (rather than reading the stored GLB) is deliberate: the stored GLB
    is mesh-merged, so its scene-entry count is not the object count — the parity
    check must reload with merging off, which ``parity_for_source_file`` does.
    """
    job_id = job.job_id
    suffix = pathlib.PurePosixPath(job.source_key).suffix.lower()
    formats = tuple(t for t in ("ifc", "xml", "step") if t in ConverterRegistry.targets_for(suffix))

    # Lazy import keeps the FEM/CAD stack out of the worker import path until
    # a parity job actually runs (same pattern as _run_fea_meta_compute).
    from ada.cadit.visual_parity import parity_for_source_file

    await _on_progress("parity", 0.20)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, functools.partial(parity_for_source_file, src_path, formats))
    except Exception as exc:
        logger.exception("worker: parity validation failed for %s", job.source_key)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="parity", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=trace)
        return

    if db_pool is not None:
        try:
            await db_module.insert_audit_parity(
                db_pool,
                job_id=job_id,
                source_key=job.source_key,
                baseline=result.expected,
                counts=result.counts,
                consistent=result.consistent,
                mismatches=result.mismatches,
                errors=result.errors,
            )
        except Exception:
            logger.exception("worker: insert_audit_parity failed for %s", job.source_key)

    if result.consistent:
        await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(db_pool, job_id, "done", None, started_at)
    else:
        msg = result.summary()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="ready", progress=1.0, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at)


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
        glb_path = pathlib.Path(tempfile.mkstemp(suffix=".glb")[1])
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
        await storage.put_bytes(scope, job.derived_key, glb_bytes)
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

    def put_bytes(self, key: str, data: bytes) -> None:
        self._run(self._s.put_bytes(self._scope, key, data))


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

    def _invoke() -> dict:
        return run_utility(
            uname,
            src_path,
            storage=sync_storage,
            scope=scope,
            on_progress=_on_progress,
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
    if not getattr(job, "force_rebuild", False) and await storage.exists(scope, job.derived_key):
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
    try:
        try:
            if src_suffix.lower() == ".sif":
                sif_reduced = await _try_reduced_sif_source(storage, scope, job.source_key, job.step, src_path)
            if not sif_reduced:
                await storage.stream_to_path(scope, job.source_key, src_path)
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

            # Optional per-job wall-clock budget. Empty / 0 / non-
            # numeric leaves the watchdog off so legitimately-long
            # bakes (a 4 GiB Abaqus ODB sweep can take 20+ min)
            # aren't artificially killed. Set as a positive minutes
            # value to enable; the parent process then SIGTERMs the
            # convert subprocess after the deadline and SIGKILLs
            # 30 s later if it's still alive.
            timeout_minutes_raw = await _read_bool_setting("conversion_timeout_minutes")
            timeout_s: float | None = None
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
                "pcurve_drive_edge": "ADA_PCURVE_DRIVE_EDGE",
                "skip_shapefix": "ADA_SKIP_SHAPEFIX",
                "merge_meshes": "ADA_GLB_MERGE_MESHES",
                # STEP→GLB streaming defaults (large-file OOM guard).
                "step_streamer_auto": "ADA_STEP_STREAMER_AUTO",
                "step_streamer_threshold_mb": "ADA_STEP_STREAMER_THRESHOLD_MB",
                # Per-solid tessellation budget; a solid that overruns it (OCC hang) is
                # killed and skipped so one bad solid can't freeze the whole conversion.
                "step_stream_solid_timeout_s": "ADA_STEP_STREAM_SOLID_TIMEOUT_S",
                # FEM→IFC memory-bounded writer. Default on (converter treats
                # unset as on); set falsy to revert to the in-memory writer.
                "ifc_streaming": "ADA_IFC_STREAMING",
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
                "step_streamer": "ADA_STEP_STREAMER",
                "ifc_streaming": "ADA_IFC_STREAMING",
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
        # Gzip text-format outputs (IFC, Genie XML); GLB is binary geometry
        # that doesn't compress meaningfully and is what the in-browser
        # viewer fetches on the hot path.
        derived_encoding = "gzip" if job.target_format in {"ifc", "xml"} else None
        try:
            # Stream the output file straight to object storage (multipart) —
            # never reading it into a parent-side bytes buffer. cleanup_output()
            # drops the tmpfile + work dir once the upload settles either way.
            await storage.put_path(
                scope, job.derived_key, iresult.out_path, content_encoding=derived_encoding
            )
        except Exception as exc:
            logger.exception("worker: upload failed for %s", job.derived_key)
            trace = tb_module.format_exc()
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
            metrics = dict(iresult.final_metrics)
            metrics["profile_key"] = await _maybe_upload_profile_bytes(iresult.profile_bytes)
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
            iresult.cleanup_output()

        # Conversion + upload succeeded — collect metrics and (optionally)
        # the cProfile dump from the child.
        metrics = dict(iresult.final_metrics)
        metrics["profile_key"] = await _maybe_upload_profile_bytes(iresult.profile_bytes)

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


async def _run() -> None:
    settings = load_settings()
    if settings.queue.url is None:
        raise SystemExit("ADA_VIEWER_NATS_URL not set; nothing for the worker to do")

    storage = Storage.from_settings(settings)
    queue = JobQueue(settings.queue)
    await queue.connect()

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
    worker_id = os.environ.get("HOSTNAME", "").strip() or f"local-{os.getpid()}"
    capabilities = [c.strip() for c in os.environ.get("ADA_WORKER_CAPABILITIES", "base").split(",") if c.strip()]
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
    full_matrix = ConverterRegistry.matrix()
    if ext_allow_set is not None:
        conversions = [m for m in full_matrix if m["from"] in ext_allow_set]
    else:
        conversions = full_matrix

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

    async def _publish_registration() -> None:
        try:
            await queue.register_worker(
                worker_id,
                {
                    "image_tag": image_tag or None,
                    "capabilities": capabilities,
                    "source_exts": source_exts,
                    "conversions": conversions,
                    "utilities": utilities,
                    "started_at": started_at,
                    "last_heartbeat": time.time(),
                },
            )
        except Exception:
            logger.exception("worker: register_worker failed (non-fatal)")

    await _publish_registration()
    logger.info(
        "worker: registered id=%s capabilities=%s",
        worker_id,
        ",".join(capabilities),
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

    # Subscribe to ONLY this pool's subject — NATS does the routing
    # so this worker never sees jobs tagged for another capability.
    # ``primary_capability`` is the first entry in ADA_WORKER_CAPABILITIES
    # (defaults to ``base`` when the env is unset). Workers with
    # multiple capabilities pick the first one as their pool — running
    # a worker that bridges two pools needs two distinct deployments.
    primary_capability = capabilities[0].lower() if capabilities else "base"
    logger.info(
        "worker: subscribing to capability pool %r (consumer durable suffix)",
        primary_capability,
    )
    sub = await queue.pull_subscribe(primary_capability)

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
                    if peeked.target_format == "component_build":
                        can_handle = True
                        ext = ""
                    else:
                        ext = pathlib.PurePosixPath(peeked.source_key).suffix.lower()
                        legacy_ok = ext in LEGACY_CONVERT_EXTS and (ext_allow_set is None or ext in ext_allow_set)
                        can_handle = ext in source_ext_set or legacy_ok
                    if not can_handle:
                        misroute_msg = (
                            f"misrouted: pool capability {primary_capability!r} "
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
