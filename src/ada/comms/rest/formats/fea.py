"""FEA streaming-viewer artefact bake (``fea_artefacts``) and the legacy picker meta cache
(``fea_meta``).
"""

from __future__ import annotations

import asyncio
import functools
import os
import pathlib
import shutil
import tempfile
import traceback as tb_module
from typing import Awaitable, Callable

import asyncpg

from ada.config import logger

from .. import db as db_module
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


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
    # uses; _make_sin_reader reads it. Default (unset/empty) lets adapy pick:
    # full-materialise, or the streaming reader for a deck too large for it.
    # Set fresh per job so toggling takes effect without a worker restart.
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
            # Listed up front: put_path stages a gzip copy beside the manifest while it uploads,
            # and that staging file must not be picked up as an artefact of its own.
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
                # Streamed from disk: a large deck's bake runs to GBs across its blobs, and
                # reading each whole would peak the worker at the size of the largest one.
                await storage.put_path(scope, target_key, produced, content_encoding=content_encoding)
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

    from ..converter import compute_fea_meta

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


def _range_fetcher(storage: "Storage", scope, prefix: str, loop: asyncio.AbstractEventLoop):
    """A ``combine.StrideFetcher`` over the stored base bake, for a worker thread.

    Each stride is one ranged GET of an identity-stored blob, so a case reads the
    strides of its terms and nothing else -- never a whole multi-step blob."""

    def fetch(blob: dict, step_index: int) -> bytes:
        stride = int(blob["stride_bytes"])
        start = int(blob["header_bytes"]) + int(step_index) * stride
        coro = storage.get_range(scope, prefix + str(blob["url"]), start, stride)
        data = asyncio.run_coroutine_threadsafe(coro, loop).result()
        if len(data) != stride:
            raise ValueError(f"{blob['url']}: short range read for step {step_index}")
        return data

    return fetch


async def _upload_dir(storage: "Storage", scope, prefix: str, out_dir: pathlib.Path, last: str) -> None:
    """Upload every file of ``out_dir`` under ``prefix``; ``last`` (the JSON that
    says the set is complete) goes up last, gzipped like the manifest."""
    files = sorted(p for p in out_dir.iterdir() if p.is_file() and p.name != last)
    for produced in files:
        await storage.put_path(scope, prefix + produced.name, produced)
    await storage.put_path(scope, prefix + last, out_dir / last, content_encoding="gzip")


async def _fail(queue, db_pool, job_id: str, started_at: float, stage: str, exc: BaseException) -> None:
    logger.exception("worker: %s failed for job %s", stage, job_id)
    await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=str(exc))
    await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())


async def _load_base_manifest(storage: "Storage", scope, source_key: str) -> dict:
    import json as _json

    from ..converter import fea_artefact_manifest_key_for

    return _json.loads((await storage.get_bytes(scope, fea_artefact_manifest_key_for(source_key))).decode("utf-8"))


async def _run_fea_case(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Materialise one lazy load combination (``job.step``) of a base bake.

    Tier A superposes the base bake's stored-case strides, read by range from
    storage -- no source file involved. A recipe the strides cannot reproduce
    (``needs_raw``) streams the source down and bakes that one case from its
    records. Either way the single-step blobs go up under
    ``_derived/<src>.fea/cases/<n>-<recipeHash8>/`` and ``fea.case.json`` last.
    """
    from ada.fem.results.artefacts.combine import (
        CASE_OVERLAY_NAME,
        NeedsRawRecords,
        case_dir_name,
        find_combination,
        materialise_case,
        materialise_case_from_source,
    )

    from ..converter import fea_artefact_prefix_for, fea_case_prefix_for

    job_id = job.job_id
    await queue.update(job_id, stage="loading", progress=0.05)
    work = pathlib.Path(tempfile.mkdtemp(prefix="fea-case-"))
    try:
        try:
            manifest = await _load_base_manifest(storage, scope, job.source_key)
            entry = find_combination(manifest, int(job.step))
        except Exception as exc:
            await _fail(queue, db_pool, job_id, started_at, "loading", exc)
            return
        out = work / "case"
        loop = asyncio.get_running_loop()
        await queue.update(job_id, stage="combining", progress=0.2)
        try:
            fetch = _range_fetcher(storage, scope, fea_artefact_prefix_for(job.source_key), loop)
            try:
                await loop.run_in_executor(
                    None, functools.partial(materialise_case, manifest, fetch, entry, out_dir=out)
                )
            except NeedsRawRecords as why:
                logger.info("fea-case: %s case %s from the source records (%s)", job.source_key, entry["n"], why)
                await queue.update(job_id, stage="reading source", progress=0.3)
                shutil.rmtree(out, ignore_errors=True)
                src_path = work / ("source" + pathlib.PurePosixPath(job.source_key).suffix)
                await storage.stream_to_path(scope, job.source_key, src_path)
                await loop.run_in_executor(
                    None,
                    functools.partial(materialise_case_from_source, src_path, manifest, entry, out, work_dir=work),
                )
        except Exception as exc:
            await _fail(queue, db_pool, job_id, started_at, "combining", exc)
            return
        await queue.update(job_id, stage="uploading", progress=0.85)
        try:
            prefix = fea_case_prefix_for(job.source_key, case_dir_name(entry))
            await _upload_dir(storage, scope, prefix, out, CASE_OVERLAY_NAME)
        except Exception as exc:
            await _fail(queue, db_pool, job_id, started_at, "upload", exc)
            return
        await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(db_pool, job_id, "done", None, started_at)
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def _run_fea_envelope(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Max / min of one field (``job.field``) over the lazy combinations of a base bake.

    Every Tier-A combination is superposed from the stored strides and folded
    into the running max / min; the ones that need the raw records are listed as
    ``skipped`` rather than read, so the legend can say what the range covers.
    Writes ``fea.envelope.json`` (ranges, cases, skipped) and, per blob, the max
    and min as one two-step blob of the base format plus the governing case
    indices (``<blob>.governing.bin``: 16-byte header ``AFGV``, uint32 version,
    uint32 n_cases, uint32 pad, then uint16 ``[2, *stride]``).
    """
    from ada.fem.results.artefacts.combine import write_envelope

    from ..converter import fea_artefact_prefix_for

    job_id = job.job_id
    await queue.update(job_id, stage="loading", progress=0.05)
    work = pathlib.Path(tempfile.mkdtemp(prefix="fea-envelope-"))
    try:
        try:
            manifest = await _load_base_manifest(storage, scope, job.source_key)
        except Exception as exc:
            await _fail(queue, db_pool, job_id, started_at, "loading", exc)
            return
        out = work / "envelope"
        loop = asyncio.get_running_loop()
        await queue.update(job_id, stage="combining", progress=0.2)
        try:
            fetch = _range_fetcher(storage, scope, fea_artefact_prefix_for(job.source_key), loop)
            await loop.run_in_executor(None, functools.partial(write_envelope, manifest, fetch, str(job.field), out))
        except Exception as exc:
            await _fail(queue, db_pool, job_id, started_at, "combining", exc)
            return
        await queue.update(job_id, stage="uploading", progress=0.85)
        try:
            prefix = job.derived_key.rsplit("/", 1)[0] + "/"
            await _upload_dir(storage, scope, prefix, out, "fea.envelope.json")
        except Exception as exc:
            await _fail(queue, db_pool, job_id, started_at, "upload", exc)
            return
        await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(db_pool, job_id, "done", None, started_at)
    finally:
        shutil.rmtree(work, ignore_errors=True)


class FeaCaseHandler(SyntheticFormatHandler):
    """``fea_case``: one lazy load combination. Synthetic -- the base bake in
    storage is the input; the source is fetched only for a raw-record recipe."""

    kind = "fea_case"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_fea_case(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )


class FeaEnvelopeHandler(SyntheticFormatHandler):
    """``fea_envelope``: one field's envelope over the lazy combinations."""

    kind = "fea_envelope"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_fea_envelope(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )


class FeaArtefactsHandler(SyntheticFormatHandler if not True else SourceFormatHandler):
    kind = "fea_artefacts"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_fea_artefact_bake(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
            src_path=ctx.src_path,
            _on_progress=ctx.on_progress,
        )


class FeaMetaHandler(SyntheticFormatHandler if not True else SourceFormatHandler):
    kind = "fea_meta"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_fea_meta_compute(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
            src_path=ctx.src_path,
            _on_progress=ctx.on_progress,
        )
