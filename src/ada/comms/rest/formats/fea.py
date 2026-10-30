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
import threading
import traceback as tb_module
from typing import Awaitable, Callable

import asyncpg

from ada.config import logger

from .. import db as db_module
from .. import subprocess_convert
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


class BakeCancelled(RuntimeError):
    """The job was cancelled while its bake ran."""


class ArtefactUploader:
    """Ships each artefact the bake emits, on a thread of its own, in emission order.

    The bake calls :meth:`submit` from its thread the moment a file is complete
    (``bake_artefacts(on_artefact=...)``) and goes straight back to decoding: the
    upload never holds up the decode or the download, and the mesh is in storage
    -- with the preliminary manifest naming it -- while the fields are still being
    read. Order is kept, so the contract the bake writes in (a manifest after
    every file it names, its stamp after it) holds in storage too.

    The small JSON files (manifest, stamp, sidecars) are READ at submit time and
    shipped from memory: the bake re-writes the preliminary manifest as fields
    land, and the copy queued earlier must be the one it described, not a newer
    one naming blobs still behind it in the queue -- nor a half-rewritten file.
    Everything else is written once and streamed from disk.

    A failed upload fails the bake: the next :meth:`submit` raises it, and
    :meth:`finish` re-raises it -- an artefact is never silently dropped.
    ``on_uploaded(name, data)`` runs after each upload (``data``: the bytes of a
    JSON file, else None); the worker turns the stamps into the job's
    ``artefacts_ready``. :meth:`cancel` drops what is queued and stops.
    """

    #: JSON files up to this size are snapshotted at submit (the manifest index of a
    #: large SIN model is ~80 KB; its biggest detail sidecar ~40 MB is written once).
    SNAPSHOT_MAX_BYTES = 64 << 20

    def __init__(self, upload, on_uploaded=None) -> None:
        import queue as queue_module
        import threading

        self._upload = upload  # (name, path | None, data | None, content_encoding) -> None, blocking
        self._on_uploaded = on_uploaded
        self._q: "queue_module.Queue" = queue_module.Queue()
        self._error: BaseException | None = None
        self._cancelled = False
        self.uploaded: list[str] = []
        self._thread = threading.Thread(target=self._run, name="fea-artefact-uploader", daemon=True)
        self._thread.start()

    def submit(self, path: pathlib.Path) -> None:
        if self._cancelled:
            raise BakeCancelled("bake cancelled")
        if self._error is not None:
            raise RuntimeError(f"artefact upload failed: {self._error}") from self._error
        path = pathlib.Path(path)
        is_json = path.suffix.lower() == ".json"
        data = path.read_bytes() if is_json and path.stat().st_size <= self.SNAPSHOT_MAX_BYTES else None
        self._q.put((path, data, "gzip" if is_json else None))

    def cancel(self) -> None:
        self._cancelled = True
        self._q.put(None)

    def finish(self) -> None:
        """Wait for everything submitted to be uploaded; raise the first failure."""
        self._q.put(None)
        self._thread.join()
        if self._error is not None:
            raise RuntimeError(f"artefact upload failed: {self._error}") from self._error
        if self._cancelled:
            raise BakeCancelled("bake cancelled")

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            if self._error is not None or self._cancelled:
                continue  # drain: nothing after a failure (or a cancel) goes up
            path, data, encoding = item
            try:
                self._upload(path.name, None if data is not None else path, data, encoding)
                self.uploaded.append(path.name)
                if self._on_uploaded is not None:
                    self._on_uploaded(path.name, data)
            except BaseException as exc:
                self._error = exc


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
    progressive=None,
) -> None:
    """Bake the streaming-viewer artefact tree for ``job.source_key``.

    The source is at ``src_path`` -- or, for a ``.sin``, still arriving: then
    ``progressive`` is the :class:`~ada.fem.formats.sesam.results.byte_source.ProgressiveDownload`
    filling ``progressive.path``, and the bake decodes it as it lands (any chunk
    the reader needs first is fetched at once). Produces:

    * ``_derived/<src>.fea/fea.mesh.glb``
    * ``_derived/<src>.fea/fea.manifest.json`` (gzip) -- the compact index
    * ``_derived/<src>.fea/fea.<field>.bin`` × N (identity — HTTP-Range-able)
    * ``_derived/<src>.fea/fea.detail.*.json``, ``fea.groups.json``,
      ``fea.nodelabels.json`` (gzip) -- the manifest's per-model tables
    * ``_derived/<src>.fea/fea.stamp.json`` (gzip) -- its freshness stamps

    Mesh first: every file goes up the moment the bake has written it, on the
    :class:`ArtefactUploader`'s thread, and the bake writes a PRELIMINARY manifest
    (``preliminary: true``, its stamp carrying this job's id) as soon as the mesh
    is out and again as each field completes. The job's ``artefacts_ready``
    (``mesh_ready``, ``fields_ready`` / ``fields_total``, ``complete``) says how
    far storage has got, so a viewer polling ``/convert/{job_id}`` draws the mesh
    and adds fields while the bake runs; the manifest route serves the
    preliminary manifest while this job is alive. The final manifest replaces it.

    A failed upload fails the job. A cancelled job (its audit row flipped by the
    kill endpoint, checked on the heartbeat) stops the download, the decode (at
    its next step or chunk) and the uploader.

    Updates the queue + audit row to mirror the convert flow's
    end-of-job semantics so the existing ``/convert/{job_id}`` poll
    loop works unchanged.
    """
    import json as _json

    job_id = job.job_id

    # Defer the heavy imports until we actually have a job to bake —
    # the worker boots faster and a pure-convert worker doesn't pay
    # the import-time cost.
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    await _on_progress("parsing", 0.10)

    # Admin "Stream SIN FEA bake" and "Cache SIN FEA bake fields" toggles
    # (app_settings ``fea_sin_streamer``, ``fea_sin_field_cache``). The bake runs
    # in-process on an executor thread, so we drive the reader choice through the
    # same ADA_* env-var seam the convert path uses; _make_sin_reader reads them.
    # Default (unset/empty) lets adapy pick: a full read with its field values in a
    # file cache (see _make_sin_reader). Set fresh per job so toggling takes effect
    # without a worker restart.
    if db_pool is not None:
        for setting, env in (
            ("fea_sin_streamer", "ADA_FEA_SIN_STREAMER"),
            ("fea_sin_field_cache", "ADA_FEA_SIN_FIELD_CACHE"),
        ):
            try:
                value = await db_module.get_setting(db_pool, setting)
            except Exception:
                logger.exception("worker: failed to read %s setting", setting)
                value = None
            if value is not None and value.strip() != "":
                os.environ[env] = value
            else:
                os.environ.pop(env, None)

    loop = asyncio.get_running_loop()
    prefix = f"_derived/{job.source_key}.fea/"
    ready: dict = {}

    def upload(name: str, path: pathlib.Path | None, data: bytes | None, encoding: str | None) -> None:
        # Compression policy mirrors the API-side endpoint: gzip only the JSON
        # (manifest, stamp, sidecars). Field/edge/element ``.bin`` blobs are stored
        # *identity* — float32/int payloads barely compress, and keeping them
        # uncompressed lets the viewer HTTP-Range a single step out of a multi-step
        # field blob (see the blobs route) instead of pulling every step. Streamed
        # from disk: a large deck's bake runs to GBs across its blobs.
        if data is not None:
            coro = storage.put_bytes(scope, prefix + name, data, content_encoding=encoding)
        else:
            coro = storage.put_path(scope, prefix + name, path, content_encoding=encoding)
        asyncio.run_coroutine_threadsafe(coro, loop).result()

    def on_uploaded(name: str, data: bytes | None) -> None:
        # The stamp is the last file of each manifest write: once it is in storage,
        # so is that manifest and everything it names. Tell the poller.
        if name != "fea.stamp.json" or data is None:
            return
        stamp = _json.loads(data)
        if stamp.get("preliminary"):
            ready.update(
                mesh_ready=True,
                fields_ready=int(stamp.get("n_fields_ready") or 0),
                fields_total=stamp.get("n_fields_total"),
                complete=False,
            )
        else:
            ready.update(mesh_ready=True, fields_ready=int(stamp.get("n_fields") or 0), complete=True)
            ready["fields_total"] = ready["fields_ready"]
        coro = queue.update(job_id, artefacts_ready=dict(ready))
        try:
            asyncio.run_coroutine_threadsafe(coro, loop).result()
        except Exception:
            logger.debug("worker: artefacts_ready update failed for %s", job_id, exc_info=True)

    uploader = ArtefactUploader(upload, on_uploaded)
    cancelled = {"flag": False}

    def check_cancel(*_args) -> None:
        if cancelled["flag"]:
            raise BakeCancelled("bake cancelled")

    def on_artefact(path: pathlib.Path) -> None:
        check_cancel()
        uploader.submit(path)

    def cancel() -> None:
        cancelled["flag"] = True
        uploader.cancel()
        if progressive is not None:
            progressive.cancel()  # the decoder's next read of a missing chunk raises

    unregister = None
    if progressive is not None:
        from ada.fem.formats.sesam.results.byte_source import register_progressive_file

        src_path = pathlib.Path(progressive.path)
        unregister = register_progressive_file(progressive)

    bake_dir = pathlib.Path(tempfile.mkdtemp(prefix="fea-bake-"))
    try:
        # Heartbeat task — the bake runs on an executor thread and has
        # no progress callback of its own. Without an external ping
        # the queue's ``updated_at`` (and ``msg.in_progress`` on the
        # JetStream side once we plumb it) sit frozen at the last
        # progress milestone for the duration of the bake; the SPA's
        # "stuck at 10%" symptom is just the toast displaying the
        # last write. Re-emit progress every ``HEARTBEAT_SECONDS``
        # with a slow incremental tick (0.10 → 0.80, never reaching
        # the real 0.85 "uploading" milestone) so the user can tell
        # the worker is still alive. The same tick asks whether the job
        # was cancelled.
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
                    if db_pool is not None and not cancelled["flag"]:
                        try:
                            if await db_module.audit_is_cancelled(db_pool, job_id):
                                logger.info("worker: job %s cancelled mid-bake; stopping", job_id)
                                cancel()
                        except Exception:
                            logger.debug("worker: mid-bake cancel check failed for %s", job_id, exc_info=True)
                else:
                    return

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            if subprocess_convert.HAVE_POSIX_FORK:
                await _bake_in_child(
                    job, src_path, bake_dir, uploader=uploader, progressive=progressive, cancelled=cancelled
                )
                bake = None
            else:
                bake = await loop.run_in_executor(
                    None,
                    functools.partial(
                        bake_fea_artefacts_from_source,
                        src_path,
                        bake_dir,
                        src_key=job.source_key,
                        on_artefact=on_artefact,
                        on_progress=check_cancel,
                        partial_manifests=True,
                        stamp_extra={"job_id": job_id},
                    ),
                )
            await _on_progress("uploading", 0.85)
            # Everything emitted is queued; wait for the last of it (the final manifest
            # and stamp) to be in storage. Then the download: the bake read what it
            # needed, and the rest is the cache's -- but only a download that completed
            # and verified (the object unchanged) vouches for the bytes the bake read.
            await asyncio.to_thread(uploader.finish)
            if progressive is not None:
                await asyncio.to_thread(progressive.wait)
        except BaseException as exc:
            uploader.cancel()
            if progressive is not None and not progressive.complete:
                progressive.cancel()
            was_cancelled = cancelled["flag"] or isinstance(exc, BakeCancelled)
            if not isinstance(exc, Exception) and not was_cancelled:
                raise
            trace = tb_module.format_exc()
            heartbeat_stop.set()
            await heartbeat_task
            if was_cancelled:
                await queue.update(job_id, status="cancelled", stage="cancelled", error=None)
                await _audit_done(db_pool, job_id, "cancelled", None, started_at)
                return
            logger.exception("worker: fea bake failed for %s", job.source_key)
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
        del bake

        await queue.update(
            job_id,
            status=JOB_STATUS_DONE,
            stage="ready",
            progress=1.0,
            error=None,
        )
        await _audit_done(db_pool, job_id, "done", None, started_at)
    finally:
        if unregister is not None:
            unregister()
        try:
            shutil.rmtree(bake_dir, ignore_errors=True)
        except Exception:
            pass


async def _bake_in_child(
    job: Job,
    src_path: pathlib.Path,
    bake_dir: pathlib.Path,
    *,
    uploader: "ArtefactUploader",
    progressive,
    cancelled: dict,
) -> None:
    """Run the bake in a forked child, relaying what it needs from this process.

    In-process, on an executor thread, a reader holding the GIL for minutes (aba-cpp's export of
    a multi-GB Abaqus ODB) starved this event loop: the JetStream lease refresh stopped, and the
    job was redelivered to a second worker while the first was still on it. The child relays
    instead of calling in:

    * each artefact it writes (``on_artefact``) goes to this process's ``uploader``, which uploads
      it as before -- partial manifests and stamps included;
    * a SIN still arriving (``progressive``) is read through :class:`_RelayedDownload`: the child
      maps the same file, and asks this process to make a range present before reading it;
    * a cancel (``cancelled["flag"]``) kills the child.

    Raises :class:`BakeCancelled` when cancelled, the upload's own error when an upload failed,
    else the child's error.
    """
    relayed = (
        None
        if progressive is None
        else {
            "path": str(progressive.path),
            "size": int(progressive.size()),
            "chunk_bytes": int(progressive.chunk_bytes),
        }
    )
    src_key, job_id = job.source_key, job.job_id

    def _in_child(child) -> None:
        _bake_child(child, src_path, bake_dir, src_key, job_id, relayed)

    async def _serve(op: str, args: dict):
        if op == "artefact":
            uploader.submit(pathlib.Path(args["path"]))
            return None
        if op == "ensure":
            await asyncio.to_thread(progressive.ensure, int(args["offset"]), int(args["length"]))
            return {"complete": bool(progressive.complete)}
        if op == "ensure_chunks":
            await asyncio.to_thread(progressive.ensure_chunks, [int(c) for c in args["chunks"]])
            return {"complete": bool(progressive.complete)}
        raise ValueError(f"unknown bake request {op!r}")

    async def _cancel_check() -> bool:
        return bool(cancelled["flag"])

    from ..worker.state import _touch_liveness

    async def _on_sample(_sample) -> None:
        _touch_liveness()

    res = await subprocess_convert.run_isolated(
        _in_child,
        label="FEA bake",
        child_name="FEA bake",
        crash_hint="(SIGSEGV/SIGABRT typically means a native fault inside a result reader).",
        work_prefix="fea-bake-",
        on_sample=_on_sample,
        cancel_check=_cancel_check,
        serve=_serve,
        # Not the conversion ceiling (ADA_CONVERT_MEM_LIMIT_MB, ~3 GB): a large deck's bake peaks
        # past it (a 2.7 GB SIN: about 5 GB), and on its old executor thread only the pod limit
        # applied. ADA_FEA_BAKE_MEM_LIMIT_MB when set, else 85% of the pod's limit -- so a runaway
        # bake dies on its own instead of taking the worker pod down.
        mem_limit_bytes=subprocess_convert._mem_limit_from_env("ADA_FEA_BAKE_MEM_LIMIT_MB"),
    )
    ok = res.ok  # read before cleanup: ``ok`` needs the work dir
    res.cleanup()  # the artefacts are in bake_dir; the child's own work dir holds only its logs
    if res.cancelled or cancelled["flag"]:
        raise BakeCancelled("bake cancelled")
    if not ok:
        if res.signal_name:
            logger.warning("worker: fea bake child for %s ended via %s", src_key, res.signal_name)
        # An upload that failed failed the child (its next relay raised it): report the upload.
        await asyncio.to_thread(uploader.finish)
        raise RuntimeError(res.error_message or res.error or "the FEA bake produced no output")


def _bake_child(child, src_path, out_dir, src_key: str, job_id: str, relayed: dict | None) -> None:
    """The bake, in the forked child (see :func:`_bake_in_child`)."""
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    # One request at a time on the relay: the bake may emit from several threads.
    lock = threading.Lock()

    def call(op: str, **args):
        with lock:
            return child.call(op, **args)

    if relayed is not None:
        from ada.fem.formats.sesam.results.byte_source import register_progressive_file

        # Replaces the parent's registration this process inherited: that download's threads
        # did not come along across the fork.
        register_progressive_file(_RelayedDownload(call, **relayed))
    bake_fea_artefacts_from_source(
        src_path,
        out_dir,
        src_key=src_key,
        on_artefact=lambda path: call("artefact", path=str(path)),
        partial_manifests=True,
        stamp_extra={"job_id": job_id},
    )


class _RelayedDownload:
    """The parent's ``ProgressiveDownload``, seen from the bake's child process.

    What ``ProgressiveMmapSource`` uses of a download: the child maps the same file (the parent's
    writes show through the shared page cache) and asks the parent, over the relay, to make a range
    present before reading it. Chunks it has been told are on disk are not asked about again.
    """

    def __init__(self, call: Callable[..., dict], *, path: str, size: int, chunk_bytes: int) -> None:
        self._call = call
        self.path = path
        self._size = int(size)
        self.chunk_bytes = int(chunk_bytes)
        self._n = (self._size + self.chunk_bytes - 1) // self.chunk_bytes
        self._have = bytearray(self._n)
        self.complete = self._n == 0

    def size(self) -> int:
        return self._size

    def chunk_of(self, offset):
        return offset // self.chunk_bytes

    def _chunks(self, offset: int, length: int) -> range:
        if length <= 0:
            return range(0)
        return range(offset // self.chunk_bytes, min(self._n, (offset + length - 1) // self.chunk_bytes + 1))

    def _mark(self, reply: dict | None, chunks) -> None:
        for i in chunks:
            self._have[i] = 1
        if reply and reply.get("complete"):
            self.complete = True

    def ensure(self, offset: int, length: int) -> None:
        if self.complete:
            return
        chunks = self._chunks(int(offset), int(length))
        if all(self._have[i] for i in chunks):
            return
        self._mark(self._call("ensure", offset=int(offset), length=int(length)), chunks)

    def ensure_chunks(self, chunks) -> None:
        if self.complete:
            return
        missing = [int(c) for c in chunks if not self._have[int(c)]]
        if missing:
            self._mark(self._call("ensure_chunks", chunks=missing), missing)


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

    async def is_cached(self, storage: "Storage", scope, job: Job) -> bool:
        """The manifest exists AND is a finished bake's: a bake ships a preliminary
        manifest (stamp ``preliminary: true``) at the derived key while it runs, and
        one left behind by a crashed run must not pass a redelivery off as cached."""
        import json as _json

        if not await storage.exists(scope, job.derived_key):
            return False
        stamp_key = job.derived_key.rsplit("/", 1)[0] + "/fea.stamp.json"
        try:
            stamp = _json.loads((await storage.get_bytes(scope, stamp_key)).decode("utf-8"))
        except FileNotFoundError:
            return True  # a bake from before the stamp: finished, as it always was
        except Exception:
            return False
        return not stamp.get("preliminary")

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
            progressive=getattr(ctx.fetch, "progressive", None),
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
