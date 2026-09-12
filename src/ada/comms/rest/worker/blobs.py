"""Object-storage helpers around a job's source and derived blobs: waiting on an upstream blob,
the reduced-SIF / streamed-SIN source paths, sibling sidecars and the SIF index.
"""

from __future__ import annotations

import asyncio
import pathlib
import time
from dataclasses import dataclass

from ada.config import logger

from .. import source_cache
from ..queue import Job, JobQueue
from ..scope import Scope
from ..storage import Storage
from . import state
from .state import _SIDECAR_SIBLINGS


async def _wait_for_blob(
    storage: "Storage",
    scope,
    key: str,
    *,
    queue: "JobQueue",
    job_id: str,
    budget_s: float,
    interval_s: float | None = None,
    waiting_stage: str | None = None,
) -> bool:
    """Poll object storage until ``key`` exists, up to ``budget_s`` (sleeping
    ``interval_s`` between checks — defaulting to the module poll interval, read at
    call time so it stays tunable). Returns ``True`` as soon as the blob is present,
    ``False`` on timeout or a graceful worker shutdown.

    Interruptible + non-busy-spinning: the between-checks wait blocks on the
    module-level shutdown event (``state._WORKER_STOP``) via ``asyncio.wait_for`` so a
    SIGTERM wakes it immediately, mirroring the keep-alive/heartbeat loops. When no
    stop event is wired (unit tests) it falls back to a plain ``asyncio.sleep``."""
    interval_s = state.STRUCTURAL_ARTIFACT_WAIT_INTERVAL_S if interval_s is None else interval_s
    deadline = time.monotonic() + budget_s
    stop = state._WORKER_STOP
    announced = False
    while True:
        try:
            if await storage.exists(scope, key):
                return True
        except Exception:
            # A transient storage error shouldn't abort the wait — retry next tick.
            logger.debug("worker: exists() check failed for %s (retrying)", key)
        if stop is not None and stop.is_set():
            return False
        if time.monotonic() >= deadline:
            return False
        if waiting_stage and not announced:
            try:
                await queue.update(job_id, stage=waiting_stage, progress=0.10)
            except Exception:
                logger.debug("worker: could not update stage while waiting for %s", key)
            announced = True
        if stop is not None:
            try:
                # Wake early when the worker is asked to shut down.
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
                return False
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(interval_s)


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

    from ..converter import sif_index_key_for

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


async def _try_sin_stream_uri(storage: Storage, scope: Scope, source_key: str) -> str | None:
    """Presigned GET URL for reading a ``.sin`` deck straight from object storage.

    The SIN reader (:func:`ada.fem.formats.sesam.results.sin_reader.open_sin`)
    range-fetches through a paged byte source, so a conversion touches only the
    pointer tables plus the target step's records — no multi-GB full download,
    and resident bytes stay capped by the reader's page cache. Returns None
    (caller falls back to the full streaming download) when the store can't
    presign (LocalStore), the blob is gzip-at-rest (range offsets address the
    uncompressed file), or the source is missing (so the download path raises
    the proper FileNotFoundError instead of the child 404ing mid-read).
    """
    try:
        if not storage.supports_presigned_uploads:
            return None
        if await storage.is_gzip_stored(scope, source_key):
            return None
        if not await storage.exists(scope, source_key):
            return None
        # TTL must outlive the conversion — the child fetches pages throughout
        # its run, not just at open. 4 h covers the longest bakes.
        return await storage.presigned_get_url(scope, source_key, expires_in_seconds=4 * 3600, internal=True)
    except Exception:
        logger.exception("worker: presigning SIN source %s failed (non-fatal); full download", source_key)
        return None


async def _ensure_sif_index(storage: Storage, scope: Scope, source_key: str, src_path: pathlib.Path) -> None:
    """Build + upload the SIF byte-offset index sidecar if absent.

    One-time cheap byte scan (no float parsing) of the full local deck so later
    picks of other steps range-fetch a reduced file. Best-effort: a failure
    here never fails the job — it just means the next pick scans the whole file
    again."""
    from ada.fem.formats.sesam.results.sif_index import build_sif_index

    from ..converter import sif_index_key_for

    index_key = sif_index_key_for(source_key)
    try:
        if await storage.exists(scope, index_key):
            return
        idx = await asyncio.to_thread(build_sif_index, src_path)
        await storage.put_bytes(scope, index_key, idx.to_json())
        logger.info("worker: built SIF index for %s (%d steps)", source_key, len(idx.steps))
    except Exception:
        logger.exception("worker: building SIF index for %s failed (non-fatal)", source_key)


@dataclass
class SourceFetch:
    """How a source-backed job's input landed on the worker's disk (audit provenance)."""

    src_suffix: str
    # A SIF deck with a cached byte-offset index range-fetches only the target step (reduced,
    # still-valid SIF) instead of the whole ~1 GB file; gates the post-convert index build.
    sif_reduced: bool = False
    # A ``.sin`` result deck is read straight from object storage via a presigned URL — the
    # reader's paged range-fetch touches only the pointer tables + one step's records.
    sin_source_uri: str | None = None
    # "cache-hit" / "cache-miss" / "direct" (None for the SIF-reduced / SIN-stream special paths).
    source_fetch_mode: str | None = None
    fetch_ms: int = 0
    fetch_bytes: int | None = None


async def fetch_source(storage: Storage, scope: Scope, job: Job, src_path: pathlib.Path) -> SourceFetch:
    """Stream the job's source (+ known sibling sidecars) to ``src_path``.

    Raises ``FileNotFoundError`` when the source itself is missing; a missing sibling is silent.
    Returns the fetch provenance the audit row records.
    """
    src_suffix = src_path.suffix or ""
    job_id = job.job_id
    # A SIF deck with a cached byte-offset index range-fetches only the target
    # step (reduced, still-valid SIF) instead of the whole ~1 GB file. Falls
    # back to the full stream when there's no index / it's gzip-stored / fetch
    # fails. ``sif_reduced`` gates the post-convert index build below.
    sif_reduced = False
    # A ``.sin`` result deck is read straight from object storage via a
    # presigned URL — the reader's paged range-fetch touches only the pointer
    # tables + one step's records, so the multi-GB download is skipped
    # entirely. glb is the only registry target for ``.sin`` (the FEA-result
    # route); None falls back to the full stream below.
    sin_source_uri: str | None = None
    # How the source landed on disk: "cache-hit" / "cache-miss" / "direct"
    # (None for the SIF-reduced / SIN-stream special paths). Recorded in
    # convert_meta so audit timing analysis can see the cache working —
    # fetch_ms drops to ~0 on hits.
    source_fetch_mode: str | None = None
    fetch_t0 = time.monotonic()
    if src_suffix.lower() == ".sif":
        sif_reduced = await _try_reduced_sif_source(storage, scope, job.source_key, job.step, src_path)
    elif src_suffix.lower() == ".sin" and job.target_format == "glb":
        sin_source_uri = await _try_sin_stream_uri(storage, scope, job.source_key)
    if not sif_reduced and sin_source_uri is None:
        # Cross-job source cache: an audit sweep converts the same
        # source to many targets, and re-downloading a multi-hundred-
        # MB source per target costs 30-60 s each time. Falls back to
        # a plain stream on any cache error (never fails the job) and
        # still raises FileNotFoundError for a missing source.
        source_fetch_mode = await source_cache.default_cache().fetch(storage, scope, job.source_key, src_path)

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

    # Source (+ sidecar) download is done — snapshot the slice so the audit
    # can attribute it. ``convert_ms`` spans started_at → post-convert, so a
    # slow object-storage stream (a multi-GB SIN deck takes 50–100 s) would
    # otherwise read as a slow conversion.
    fetch_ms = round((time.monotonic() - fetch_t0) * 1000)
    try:
        fetch_bytes = src_path.stat().st_size
    except OSError:
        fetch_bytes = None
    return SourceFetch(
        src_suffix=src_suffix,
        sif_reduced=sif_reduced,
        sin_source_uri=sin_source_uri,
        source_fetch_mode=source_fetch_mode,
        fetch_ms=fetch_ms,
        fetch_bytes=fetch_bytes,
    )
