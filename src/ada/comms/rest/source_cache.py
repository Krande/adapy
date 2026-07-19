"""Cross-job source-blob cache for the conversion worker.

An audit sweep converts one source to many targets (glb/obj/stl/step/ifc/
xml/parity/...), and each of those jobs used to stream the SAME source
object from storage again — a 778 MB source at ~15 MB/s costs 30-60 s of
pure re-download per job, times one job per target. This module keeps the
downloaded (already-decompressed) source bytes on worker-local disk keyed
by (scope, key, version) so every job after the first pays ~0 fetch time.

Design:

* **Versioned keys** — the cache filename is a SHA-256 over the scope
  prefix, the object key, and a version token from ``Storage.head``
  (etag when the backend reports one, else size + last_modified). A
  changed blob therefore hashes to a DIFFERENT entry name and can never
  be served stale; the superseded entry ages out via LRU. The head call
  is a real object-store metadata request (obstore ``head_async``), not
  an HTTP HEAD on a presigned GET URL — the latter is method-bound and
  403s on Garage.
* **Bounded, LRU** — ``ADA_WORKER_SOURCE_CACHE_MB`` caps the on-disk
  size (default 4096 MiB; ``0`` disables the cache entirely). Oldest-
  mtime entries are evicted before a new blob is written; a hit touches
  the entry's mtime.
* **Atomic entries** — blobs are streamed to a dot-prefixed temp name in
  the cache dir and ``os.replace``d into place, then a sidecar ``.meta``
  file records the byte size. An entry is only ever served when the meta
  matches the blob's on-disk size, so a torn write / external truncation
  reads as a miss, never as a corrupt source.
* **Read-only hand-off** — the job's own temp path is hard-linked to the
  cache entry (falling back to a copy across filesystems), so the job's
  post-conversion ``unlink`` drops its link without touching the cache,
  and an eviction mid-job can't yank the inode out from under a running
  conversion. Converters treat the source as read-only input (outputs and
  co-downloaded sidecars land at *sibling* paths next to the job's temp
  name, not next to the cache entry).
* **Fail-open** — any cache-machinery error falls back to the plain
  direct download. The only exception allowed through is
  ``FileNotFoundError`` from the actual download, which the worker
  already handles as "source missing".

Multiple worker pods each keep an independent cache (worker-local disk);
that costs one download per pod per source version, which is fine.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import pathlib
import shutil
import tempfile

logger = logging.getLogger(__name__)

_CACHE_MB_ENV = "ADA_WORKER_SOURCE_CACHE_MB"
_CACHE_DIR_ENV = "ADA_WORKER_SOURCE_CACHE_DIR"
_DEFAULT_CACHE_MB = 4096

# Fetch-mode strings recorded in the audit row's convert_meta.
MODE_HIT = "cache-hit"
MODE_MISS = "cache-miss"
MODE_DIRECT = "direct"


def _cap_bytes_from_env() -> int:
    raw = os.environ.get(_CACHE_MB_ENV, "").strip()
    if raw:
        try:
            return max(0, int(float(raw))) * (1 << 20)
        except ValueError:
            logger.warning("source-cache: bad %s=%r; using default %d MiB", _CACHE_MB_ENV, raw, _DEFAULT_CACHE_MB)
    return _DEFAULT_CACHE_MB * (1 << 20)


def _dir_from_env() -> pathlib.Path:
    raw = os.environ.get(_CACHE_DIR_ENV, "").strip()
    if raw:
        return pathlib.Path(raw)
    # Same filesystem as the worker's mkstemp source paths by default, so
    # the hard-link hand-off works and hits cost no byte copy at all.
    return pathlib.Path(tempfile.gettempdir()) / "ada_worker_source_cache"


class SourceBlobCache:
    """Disk LRU of downloaded source blobs, keyed by (scope, key, version)."""

    def __init__(self, cache_dir: pathlib.Path, cap_bytes: int) -> None:
        self.cache_dir = pathlib.Path(cache_dir)
        self.cap_bytes = int(cap_bytes)
        # Per-entry locks: jobs are processed sequentially today, but the
        # worker is an asyncio program — this keeps a future concurrent
        # pull loop from double-downloading (or torn-reading) one entry.
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def from_env(cls) -> "SourceBlobCache":
        return cls(_dir_from_env(), _cap_bytes_from_env())

    @property
    def enabled(self) -> bool:
        return self.cap_bytes > 0

    async def fetch(self, storage, scope, key: str, dest_path: pathlib.Path) -> str:
        """Materialise object ``key`` (decompressed) at ``dest_path``.

        Returns the fetch mode: ``cache-hit`` / ``cache-miss`` / ``direct``
        (cache disabled or bypassed after an internal error). Raises
        ``FileNotFoundError`` when the object doesn't exist — same contract
        as ``Storage.stream_to_path``.
        """
        dest_path = pathlib.Path(dest_path)
        if not self.enabled:
            await storage.stream_to_path(scope, key, dest_path)
            return MODE_DIRECT

        try:
            version, stored_size = await self._version_token(storage, scope, key)
            entry = self._entry_path(scope, key, version)
        except Exception:
            logger.exception("source-cache: version probe failed for %s; direct download", key)
            entry, stored_size = None, 0
        if entry is None:
            # No metadata (missing key or backend hiccup) — the direct
            # stream raises a clean FileNotFoundError for truly-absent keys.
            await storage.stream_to_path(scope, key, dest_path)
            return MODE_DIRECT

        lock = self._locks.setdefault(entry.name, asyncio.Lock())
        async with lock:
            try:
                if self._entry_valid(entry):
                    os.utime(entry)  # LRU touch
                    self._materialize(entry, dest_path)
                    logger.info("source-cache: hit %s (%d bytes) -> %s", key, entry.stat().st_size, dest_path.name)
                    return MODE_HIT
            except Exception:
                logger.exception("source-cache: hit-path failed for %s; refetching", key)

            tmp = entry.with_name(f".{entry.name}.{os.getpid()}.tmp")
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                # Pre-evict on the stored-size estimate (a gzip-at-rest blob
                # decompresses larger; the post-write pass below settles it).
                self._evict(incoming=stored_size)
                try:
                    await storage.stream_to_path(scope, key, tmp)
                except FileNotFoundError:
                    raise  # source genuinely missing — worker handles this
                disk_size = tmp.stat().st_size
                os.replace(tmp, entry)
                self._write_meta(entry, disk_size)
                self._materialize(entry, dest_path)
                if disk_size > self.cap_bytes:
                    # Single blob over the whole cap: serve it, don't keep it.
                    self._remove_entry(entry, reason="over-cap")
                else:
                    self._evict(exclude=entry.name)
                logger.info("source-cache: miss %s (%d bytes cached)", key, disk_size)
                return MODE_MISS
            except FileNotFoundError:
                raise
            except Exception:
                logger.exception("source-cache: caching %s failed; direct download", key)
                await storage.stream_to_path(scope, key, dest_path)
                return MODE_DIRECT
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    # -- internals -----------------------------------------------------

    async def _version_token(self, storage, scope, key: str) -> tuple[str | None, int]:
        """(version discriminator, stored-size estimate) from the storage
        layer's metadata call.

        ``Storage.head`` wraps obstore ``head_async`` — a real metadata
        request against the store API (NOT an HTTP HEAD on a presigned GET
        URL, which is method-bound and 403s on Garage). Prefers the etag;
        size + last_modified is the fallback for backends without one.
        """
        meta = await storage.head(scope, key)
        if meta is None:
            return None, 0
        size = int(meta.get("size") or 0)
        etag = meta.get("e_tag")
        if etag:
            return f"etag:{etag}", size
        return f"sz:{size}:lm:{meta.get('last_modified')}", size

    def _entry_path(self, scope, key: str, version: str | None) -> pathlib.Path | None:
        if version is None:
            return None
        digest = hashlib.sha256(f"{scope.prefix()}\n{key}\n{version}".encode()).hexdigest()
        suffix = pathlib.PurePosixPath(key).suffix.lower()
        return self.cache_dir / f"{digest}{suffix}"

    def _entry_valid(self, entry: pathlib.Path) -> bool:
        """True iff the blob exists AND its byte size matches the meta
        sidecar written after the atomic rename — a torn/truncated entry
        (or a blob whose meta never landed) reads as a miss."""
        meta = entry.with_name(entry.name + ".meta")
        try:
            expected = int(meta.read_text().strip())
        except (OSError, ValueError):
            return False
        try:
            return entry.stat().st_size == expected
        except OSError:
            return False

    def _write_meta(self, entry: pathlib.Path, size: int) -> None:
        meta = entry.with_name(entry.name + ".meta")
        tmp = meta.with_name(f".{meta.name}.{os.getpid()}.tmp")
        tmp.write_text(f"{size}\n")
        os.replace(tmp, meta)

    def _materialize(self, entry: pathlib.Path, dest_path: pathlib.Path) -> None:
        """Hand the cached blob to the job at its own temp path.

        Hard-link when possible (same filesystem): zero-copy, and the job's
        eventual ``unlink`` only drops its own link. Converters never write
        into the source path (outputs + sidecars are sibling files of the
        job's temp name), so sharing the inode is safe. Cross-device falls
        back to a plain copy.
        """
        try:
            dest_path.unlink(missing_ok=True)
            os.link(entry, dest_path)
        except OSError:
            shutil.copyfile(entry, dest_path)

    def _entries(self) -> list[pathlib.Path]:
        try:
            return [
                p
                for p in self.cache_dir.iterdir()
                if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".meta")
            ]
        except OSError:
            return []

    def _remove_entry(self, entry: pathlib.Path, *, reason: str) -> None:
        size = 0
        try:
            size = entry.stat().st_size
        except OSError:
            pass
        for p in (entry, entry.with_name(entry.name + ".meta")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        logger.info("source-cache: evict %s (%d bytes, %s)", entry.name, size, reason)

    def _evict(self, *, incoming: int = 0, exclude: str | None = None) -> None:
        """Drop oldest-mtime entries until total + incoming fits the cap."""
        entries = []
        total = 0
        for p in self._entries():
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total + incoming <= self.cap_bytes:
            return
        entries.sort()  # oldest mtime first
        for _mtime, size, p in entries:
            if total + incoming <= self.cap_bytes:
                break
            if exclude is not None and p.name == exclude:
                continue
            self._remove_entry(p, reason="lru")
            total -= size


_default_cache: SourceBlobCache | None = None


def default_cache() -> SourceBlobCache:
    """Process-wide cache instance, env-configured on first use."""
    global _default_cache
    if _default_cache is None:
        _default_cache = SourceBlobCache.from_env()
        logger.info(
            "source-cache: dir=%s cap=%d MiB%s",
            _default_cache.cache_dir,
            _default_cache.cap_bytes >> 20,
            "" if _default_cache.enabled else " (disabled)",
        )
    return _default_cache
