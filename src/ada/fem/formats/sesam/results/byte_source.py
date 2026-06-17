"""Byte-source backends for the Sesam SIN reader.

The SIN decoder (:mod:`ada.fem.formats.sesam.results.sin_reader`) needs
only a handful of byte-access primitives — sized reads, little-endian
scalar unpacks, a bounded ``find``, a typed contiguous view, and a
scattered float32 *gather* (``as_f32[indices]``). Factoring those behind
a small :class:`ByteSource` interface lets one decoder run over either:

* :class:`MmapSource` — an ``mmap`` (or in-memory ``bytes``) over a
  fully-present local file. Zero-copy: gathers and typed views are plain
  numpy views over the mapping, exactly as the reader did before this
  abstraction existed, and ``release`` drops cold pages via ``madvise``
  so a multi-GB SIN never pins its full size in RSS.
* :class:`PagedByteSource` — a page-cached view over a *range* fetcher
  (:class:`FileRangeSource` ``pread``, :class:`S3RangeSource` ranged
  ``GetObject``, or an HTTP-``Range`` fetch in the browser). Reads a
  multi-GB SIN without the whole object on local disk and without a
  single contiguous buffer (past the 2 GiB JS ``ArrayBuffer`` cap),
  resident memory bounded by the cache regardless of file size.

Both implement the same surface, so :func:`open_sin` (mmap) and a
range-streamed open share byte-for-byte the same decode path.
"""

from __future__ import annotations

import mmap
import os
import struct
from collections import OrderedDict
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class ByteSource(Protocol):
    """The byte-access surface the SIN decoder is written against.

    ``gather_f32`` is the one non-obvious primitive: the float32 analogue
    of ``np.frombuffer(whole_file)[word_idx]`` — fetch the float32 words
    at the given *word* offsets (byte offset // 4). It is the hot path for
    pointer-table truncation and bulk record reads, and the place each
    backend earns its keep (zero-copy view vs page-grouped fetch)."""

    def size(self) -> int: ...

    def read(self, offset: int, length: int) -> bytes: ...

    def u32(self, offset: int) -> int: ...

    def f32(self, offset: int) -> float: ...

    def find(self, needle: bytes, start: int, stop: int) -> int: ...

    def frombuffer(self, dtype, count: int, offset: int) -> np.ndarray: ...

    def gather_f32(self, word_idx: np.ndarray) -> np.ndarray: ...

    def release(self, lo: int, hi: int) -> None: ...

    def advise_random(self) -> None: ...

    def close(self) -> None: ...


class MmapSource:
    """:class:`ByteSource` over an ``mmap.mmap`` (or any bytes-like buffer).

    The default backend for local files. Scalar reads use
    ``struct.unpack_from`` straight off the buffer; ``frombuffer`` and
    ``gather_f32`` are zero-copy numpy views over the mapping — identical
    to the access the reader used before the abstraction, so there is no
    performance cost on the mmap path. Also accepts plain ``bytes`` /
    ``bytearray`` (used by unit tests and small in-memory buffers), in
    which case ``release`` / ``advise_random`` are no-ops.
    """

    def __init__(self, buf):
        self._buf = buf
        self._size = len(buf)
        # Whole-buffer float32 view, lazily created and cached. Over an
        # mmap this is zero-copy and faults pages on access; the gather
        # then touches only the pages the indices land in.
        self._as_f32: np.ndarray | None = None

    def size(self) -> int:
        return self._size

    def read(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        return bytes(self._buf[offset : offset + length])

    def u32(self, offset: int) -> int:
        if offset + 4 > self._size:
            return 0
        return struct.unpack_from("<I", self._buf, offset)[0]

    def f32(self, offset: int) -> float:
        return struct.unpack_from("<f", self._buf, offset)[0]

    def find(self, needle: bytes, start: int, stop: int) -> int:
        return self._buf.find(needle, start, stop)

    def frombuffer(self, dtype, count: int, offset: int) -> np.ndarray:
        return np.frombuffer(self._buf, dtype=dtype, count=count, offset=offset)

    def gather_f32(self, word_idx: np.ndarray) -> np.ndarray:
        if self._as_f32 is None:
            self._as_f32 = np.frombuffer(self._buf, dtype=np.float32)
        return self._as_f32[word_idx]

    def release(self, lo: int, hi: int) -> None:
        """``MADV_DONTNEED`` the page-aligned span [lo, hi) so its mmap
        pages stop counting against RSS. No-op on non-mmap buffers."""
        buf = self._buf
        if not isinstance(buf, mmap.mmap):
            return
        lo = max(0, lo)
        hi = min(self._size, hi)
        if hi <= lo:
            return
        try:
            page = mmap.ALLOCATIONGRANULARITY
            aligned_start = (lo // page) * page
            aligned_end = min(self._size, ((hi + page - 1) // page) * page)
            length = aligned_end - aligned_start
            if length > 0:
                buf.madvise(mmap.MADV_DONTNEED, aligned_start, length)
        except (AttributeError, OSError, ValueError):
            pass

    def advise_random(self) -> None:
        buf = self._buf
        if isinstance(buf, mmap.mmap):
            try:
                buf.madvise(mmap.MADV_RANDOM)
            except (AttributeError, OSError):
                pass

    def close(self) -> None:
        buf = self._buf
        if isinstance(buf, mmap.mmap):
            try:
                buf.close()
            except (ValueError, BufferError):
                pass
        self._buf = b""
        self._as_f32 = None


class FileRangeSource:
    """``pread``-style range fetcher over a local file — the worker path
    when avoiding a full mmap, and the dev stand-in for an object store.
    ``os.pread`` is thread-safe and doesn't disturb a shared offset."""

    def __init__(self, path: str):
        self._fd = os.open(path, os.O_RDONLY)
        self._size = os.fstat(self._fd).st_size

    def size(self) -> int:
        return self._size

    def fetch(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        return os.pread(self._fd, length, offset)

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


class S3RangeSource:
    """boto3 ``Range``-GET fetcher (any S3-compatible object store).

    No network call at import; boto3 is only touched in the constructor
    (for the size) and per ``fetch``. Each ``fetch`` is one HTTP request,
    so the :class:`PagedByteSource` page size sets the request
    granularity — keep it generous (>=1 MiB) so we don't issue a request
    per record page.
    """

    def __init__(self, client, bucket: str, key: str, size: int | None = None):
        self._c = client
        self._bucket = bucket
        self._key = key
        if size is None:
            size = client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        self._size = int(size)

    def size(self) -> int:
        return self._size

    def fetch(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        end = offset + length - 1  # HTTP Range is inclusive
        resp = self._c.get_object(Bucket=self._bucket, Key=self._key, Range=f"bytes={offset}-{end}")
        return resp["Body"].read()

    def close(self) -> None:  # boto3 clients are reusable; nothing to free
        pass


class PagedByteSource:
    """:class:`ByteSource` backed by an LRU page cache over a range fetcher.

    All reads are served from page-aligned chunks so a stream of small
    scattered reads (struct unpacks, pointer-table NFIELD probes)
    coalesces into whole-page fetches, and the resident set is capped at
    ``max_resident_bytes`` no matter how large the file is. The fetcher
    only needs ``size()`` / ``fetch(offset, length) -> bytes`` / ``close()``
    (see :class:`FileRangeSource`, :class:`S3RangeSource`).

    Instrumentation (``bytes_fetched`` / ``fetch_count`` /
    ``peak_resident_bytes``) makes the streaming win measurable.
    """

    def __init__(self, fetcher, page_bits: int = 20, max_resident_bytes: int = 256 << 20):
        self._fetcher = fetcher
        self._size = fetcher.size()
        self.page_bits = page_bits
        self.page_size = 1 << page_bits
        self._max_pages = max(1, max_resident_bytes // self.page_size)
        self._cache: "OrderedDict[int, bytes]" = OrderedDict()
        self.bytes_fetched = 0
        self.fetch_count = 0
        self.peak_resident_bytes = 0

    def size(self) -> int:
        return self._size

    def close(self) -> None:
        self._cache.clear()
        self._fetcher.close()

    # ── page cache core ───────────────────────────────────────────────
    def _page(self, pg: int) -> bytes:
        buf = self._cache.get(pg)
        if buf is not None:
            self._cache.move_to_end(pg)
            return buf
        start = pg << self.page_bits
        length = min(self.page_size, self._size - start)
        buf = self._fetcher.fetch(start, length)
        self.bytes_fetched += len(buf)
        self.fetch_count += 1
        self._cache[pg] = buf
        self._cache.move_to_end(pg)
        while len(self._cache) > self._max_pages:
            self._cache.popitem(last=False)  # evict least-recently-used
        self.peak_resident_bytes = max(self.peak_resident_bytes, len(self._cache) * self.page_size)
        return buf

    # ── contiguous access ─────────────────────────────────────────────
    def read(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        if offset < 0 or offset + length > self._size:
            raise ValueError(f"read [{offset}, {offset + length}) out of bounds (size {self._size})")
        first = offset >> self.page_bits
        last = (offset + length - 1) >> self.page_bits
        if first == last:
            base = first << self.page_bits
            page = self._page(first)
            lo = offset - base
            return page[lo : lo + length]
        chunks = []
        for pg in range(first, last + 1):
            base = pg << self.page_bits
            page = self._page(pg)
            lo = max(0, offset - base)
            hi = min(len(page), offset + length - base)
            chunks.append(page[lo:hi])
        return b"".join(chunks)

    def u32(self, offset: int) -> int:
        if offset + 4 > self._size:
            return 0
        return struct.unpack_from("<I", self.read(offset, 4))[0]

    def f32(self, offset: int) -> float:
        return struct.unpack_from("<f", self.read(offset, 4))[0]

    def find(self, needle: bytes, start: int, stop: int) -> int:
        """Bounded forward search — the reader only searches the small
        header prefix, so a single range read covers it."""
        stop = min(stop, self._size)
        if start >= stop:
            return -1
        buf = self.read(start, stop - start)
        idx = buf.find(needle)
        return start + idx if idx >= 0 else -1

    def frombuffer(self, dtype, count: int, offset: int) -> np.ndarray:
        itemsize = np.dtype(dtype).itemsize
        return np.frombuffer(self.read(offset, count * itemsize), dtype=dtype, count=count)

    # ── scattered gather (the lazy as_f32[indices]) ───────────────────
    def gather_f32(self, word_idx: np.ndarray) -> np.ndarray:
        """Lazy equivalent of ``np.frombuffer(whole_file, f32)[word_idx]``.

        Groups the (float32-word) indices by page, fetches each touched
        page once in sorted order (so the LRU never has to revisit), and
        assembles the result. Total bytes fetched = the unique pages the
        indices land in — for a contiguous record prefix that's ~the real
        data, never the whole capped table spread across the file.

        f32 words are 4-aligned and the page size is a multiple of 4, so
        no value straddles a page boundary.
        """
        word_idx = np.asarray(word_idx, dtype=np.int64)
        out = np.empty(word_idx.shape[0], dtype=np.float32)
        if word_idx.size == 0:
            return out
        byte_off = word_idx << 2
        pages = byte_off >> self.page_bits
        order = np.argsort(pages, kind="stable")
        pages_sorted = pages[order]
        boundaries = np.flatnonzero(np.diff(pages_sorted)) + 1
        seg_starts = np.concatenate(([0], boundaries))
        seg_ends = np.concatenate((boundaries, [order.size]))
        for s, e in zip(seg_starts, seg_ends):
            pg = int(pages_sorted[s])
            sel = order[s:e]
            page = self._page(pg)
            page_f32 = np.frombuffer(page, dtype=np.float32, count=len(page) // 4)
            within_word = (byte_off[sel] - (pg << self.page_bits)) >> 2
            out[sel] = page_f32[within_word]
        return out

    def release(self, lo: int, hi: int) -> None:
        # The LRU already bounds resident bytes; explicit release is a no-op.
        pass

    def advise_random(self) -> None:
        pass


def file_paged_source(path: str, **kw) -> PagedByteSource:
    """Convenience: a :class:`PagedByteSource` over a local file (no mmap,
    no whole-file buffer). Swap :class:`FileRangeSource` for
    :class:`S3RangeSource` for true no-disk streaming."""
    return PagedByteSource(FileRangeSource(path), **kw)


__all__ = [
    "ByteSource",
    "MmapSource",
    "PagedByteSource",
    "FileRangeSource",
    "S3RangeSource",
    "file_paged_source",
]
