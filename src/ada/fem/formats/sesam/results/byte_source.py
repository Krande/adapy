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
import threading
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
        self._as_u8: np.ndarray | None = None

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

    def u8_view(self) -> np.ndarray:
        """The whole buffer as a zero-copy uint8 array (what adacpp's SIN loops read)."""
        if self._as_u8 is None:
            self._as_u8 = np.frombuffer(self._buf, dtype=np.uint8)
        return self._as_u8

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
        # The cached views export the mapping; an mmap with a live export refuses to close.
        self._as_f32 = None
        self._as_u8 = None
        if isinstance(buf, mmap.mmap):
            try:
                buf.close()
            except (ValueError, BufferError):
                pass
        self._buf = b""


class FileRangeSource:
    """``pread``-style range fetcher over a local file — the worker path
    when avoiding a full mmap, and the dev stand-in for an object store.

    Uses ``os.pread`` where available (POSIX) — thread-safe and doesn't
    disturb a shared offset. Windows has no ``os.pread``, so there we fall
    back to ``seek`` + ``read`` under a lock so concurrent fetches don't
    race the shared file offset."""

    def __init__(self, path: str):
        self._fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        self._size = os.fstat(self._fd).st_size
        self._has_pread = hasattr(os, "pread")
        # Only needed for the seek+read fallback; cheap to always hold.
        self._lock = threading.Lock()

    def size(self) -> int:
        return self._size

    def fetch(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        if self._has_pread:
            return os.pread(self._fd, length, offset)
        # Windows: no pread — serialize seek+read so a concurrent fetch
        # can't move the offset out from under us mid-read.
        with self._lock:
            os.lseek(self._fd, offset, os.SEEK_SET)
            return os.read(self._fd, length)

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


class HttpRangeSource:
    """HTTP ``Range``-GET fetcher (stdlib ``urllib``) — for a presigned
    URL or any range-capable HTTP endpoint on CPython.

    Size comes from a ``Range: bytes=0-0`` GET probe (``Content-Range``
    total) unless passed in — NOT a ``HEAD``: a presigned URL is signed
    for one method only, so a HEAD against a GET-presigned URL fails the
    SigV4 check (403). Each ``fetch`` is one ranged ``GET``. In the
    browser, supply a fetch-based fetcher with the same ``size()`` /
    ``fetch()`` surface instead — this one relies on real sockets that
    pyodide doesn't provide.
    """

    def __init__(self, url: str, size: int | None = None, headers: dict | None = None):
        self._url = url
        self._headers = dict(headers or {})
        self._size = int(size) if size is not None else self._probe_size()

    def _probe_size(self) -> int:
        import urllib.request

        headers = {**self._headers, "Range": "bytes=0-0"}
        req = urllib.request.Request(self._url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            # 206: total after the slash in ``Content-Range: bytes 0-0/N``.
            cr = resp.headers.get("Content-Range")
            if cr is not None:
                total = cr.rpartition("/")[2].strip()
                if total and total != "*":
                    return int(total)
            # 200: the server ignored Range; Content-Length is the full size.
            # Close without reading the body — we only wanted the headers.
            cl = resp.headers.get("Content-Length")
        if cl is None:
            raise ValueError(f"GET {self._url} returned no Content-Range/Content-Length; pass size= explicitly")
        return int(cl)

    def size(self) -> int:
        return self._size

    def fetch(self, offset: int, length: int) -> bytes:
        import urllib.request

        if length <= 0:
            return b""
        end = offset + length - 1  # HTTP Range is inclusive
        headers = {**self._headers, "Range": f"bytes={offset}-{end}"}
        req = urllib.request.Request(self._url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            return resp.read()

    def close(self) -> None:
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


class DownloadCancelled(RuntimeError):
    """A :class:`ProgressiveDownload` was cancelled while a read waited on it."""


class ProgressiveDownload:
    """A remote object copied to a local file in the background, readable at once.

    Decode used to wait for the whole download (a large SIN model: 1.7 GB at
    object-store speed before the first byte was decoded). This overlaps them:

    * a background thread copies the object to ``path`` sequentially, one
      ``chunk_bytes`` chunk per ranged fetch, recording which chunks are on disk;
    * a read (:meth:`ensure`) of chunks already on disk costs nothing more; a read
      of chunks not yet there fetches THOSE chunks immediately, on the reader's
      thread, writes them into the file and marks them -- so the reader never
      waits for the sequential copy to get there, and the copy skips them;
    * a chunk being fetched by one side is waited for by the other, never
      fetched twice.

    ``fetcher`` is any range fetcher (``size()`` / ``fetch(offset, length)`` /
    ``close()``: :class:`FileRangeSource`, :class:`S3RangeSource`,
    :class:`HttpRangeSource`, or the worker's storage adapter). ``verify``,
    called once every chunk is on disk, says whether the copy is the object the
    download started from (the worker compares the object's etag); ``on_complete``
    then runs with the path -- the worker's source cache writes the entry's
    validity marker there, and ONLY there, so an interrupted or unverified copy
    is never a cache hit. Memory is one chunk per fetching thread: the data goes
    to the file, not to a buffer.

    :meth:`cancel` stops the copy and makes every waiting or later read raise
    :class:`DownloadCancelled`; :meth:`wait` joins the copy and re-raises its
    failure. Not a :class:`ByteSource`: the decoder reads the file through
    :class:`ProgressiveMmapSource`, which calls :meth:`ensure` before each access.
    """

    _MISSING, _FETCHING, _ON_DISK = 0, 1, 2

    def __init__(
        self,
        fetcher,
        path: str | os.PathLike,
        *,
        chunk_bytes: int = 8 << 20,
        verify=None,
        on_complete=None,
    ) -> None:
        if chunk_bytes <= 0 or chunk_bytes % 4096:
            raise ValueError("chunk_bytes must be a positive multiple of 4096")
        self._fetcher = fetcher
        self.path = os.fspath(path)
        self._size = int(fetcher.size())
        self.chunk_bytes = int(chunk_bytes)
        self._n = (self._size + self.chunk_bytes - 1) // self.chunk_bytes
        self._state = bytearray(self._n)  # one _MISSING/_FETCHING/_ON_DISK byte per chunk
        self._n_on_disk = 0
        self._cond = threading.Condition()
        self._cancelled = False
        self._error: BaseException | None = None
        self._verify = verify
        self._on_complete = on_complete
        self.complete = False
        #: Bytes fetched by the sequential copy and by reads that could not wait for it.
        self.bytes_sequential = 0
        self.bytes_on_demand = 0
        # The full-size file up front, so every chunk has its place and a reader can
        # map it whole; the chunks land in it out of order.
        with open(self.path, "wb") as fh:
            fh.truncate(self._size)
        self._fd = os.open(self.path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        self._has_pwrite = hasattr(os, "pwrite")
        self._write_lock = threading.Lock()  # the seek+write fallback (Windows)
        self._thread = threading.Thread(target=self._run, name="progressive-download", daemon=True)
        if self._n == 0:
            self._finish()
        else:
            self._thread.start()

    # -- public ---------------------------------------------------------------

    def size(self) -> int:
        return self._size

    @property
    def covered_bytes(self) -> int:
        with self._cond:
            return sum(self._chunk_len(i) for i in range(self._n) if self._state[i] == self._ON_DISK)

    def is_covered(self, offset: int, length: int) -> bool:
        if length <= 0 or self.complete:
            return True
        with self._cond:
            return all(self._state[i] == self._ON_DISK for i in self._chunks(offset, length))

    def ensure(self, offset: int, length: int) -> None:
        """Make ``[offset, offset + length)`` present in the file, fetching what is not."""
        if length <= 0 or self.complete:
            return
        self.ensure_chunks(self._chunks(offset, length))

    def ensure_chunks(self, chunks) -> None:
        """:meth:`ensure` by chunk index."""
        if self.complete:
            return
        for i in chunks:
            self._get_chunk(int(i), on_demand=True)

    def chunk_of(self, offset):
        """The chunk index (or array of them) holding byte ``offset``."""
        return offset // self.chunk_bytes

    def wait(self, timeout: float | None = None) -> bool:
        """Join the copy; True once complete. Re-raises the copy's failure."""
        if self._thread.is_alive():
            self._thread.join(timeout)
        if self._error is not None:
            raise self._error
        if self._cancelled and not self.complete:
            raise DownloadCancelled(f"download of {self.path} was cancelled")
        return self.complete

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()

    def close(self) -> None:
        """Cancel (if still running), join, release the file handle and the fetcher."""
        if not self.complete:
            self.cancel()
        if self._thread.is_alive():
            self._thread.join()
        try:
            os.close(self._fd)
        except OSError:
            pass
        try:
            self._fetcher.close()
        except Exception:
            pass

    # -- internals --------------------------------------------------------------

    def _chunk_len(self, i: int) -> int:
        return min(self.chunk_bytes, self._size - i * self.chunk_bytes)

    def _chunks(self, offset: int, length: int) -> range:
        lo = max(0, offset) // self.chunk_bytes
        hi = (min(self._size, offset + length) - 1) // self.chunk_bytes
        return range(lo, hi + 1)

    def _get_chunk(self, i: int, *, on_demand: bool) -> None:
        with self._cond:
            while True:
                if self._cancelled:
                    raise DownloadCancelled(f"download of {self.path} was cancelled")
                st = self._state[i]
                if st == self._ON_DISK:
                    return
                if st == self._MISSING:
                    self._state[i] = self._FETCHING
                    break
                if not on_demand:
                    return  # a reader is fetching it; the copy moves on
                self._cond.wait()
        try:
            offset, length = i * self.chunk_bytes, self._chunk_len(i)
            data = self._fetcher.fetch(offset, length)
            if len(data) != length:
                raise OSError(f"short range read at {offset}: {len(data)} of {length} bytes")
            self._write(offset, data)
        except BaseException:
            with self._cond:
                self._state[i] = self._MISSING
                self._cond.notify_all()
            raise
        with self._cond:
            self._state[i] = self._ON_DISK
            self._n_on_disk += 1
            if on_demand:
                self.bytes_on_demand += length
            else:
                self.bytes_sequential += length
            self._cond.notify_all()

    def _write(self, offset: int, data: bytes) -> None:
        if self._has_pwrite:
            view = memoryview(data)
            while view:
                n = os.pwrite(self._fd, view, offset)
                view, offset = view[n:], offset + n
            return
        with self._write_lock:
            os.lseek(self._fd, offset, os.SEEK_SET)
            view = memoryview(data)
            while view:
                view = view[os.write(self._fd, view) :]

    def _run(self) -> None:
        try:
            while True:
                for i in range(self._n):
                    if self._cancelled:
                        return
                    self._get_chunk(i, on_demand=False)  # skips chunks on disk or being fetched
                # Chunks a reader is still fetching: wait for them, so complete means
                # complete. A reader's failed fetch puts its chunk back to missing:
                # then go round again and take it.
                with self._cond:
                    while not self._cancelled and self._n_on_disk < self._n and self._MISSING not in self._state:
                        self._cond.wait(0.5)
                    if self._cancelled:
                        return
                    if self._n_on_disk == self._n:
                        break
            self._finish()
        except DownloadCancelled:
            return
        except BaseException as exc:  # surfaced by wait(); reads keep fetching on their own
            self._error = exc

    def _finish(self) -> None:
        if self._verify is not None and not self._verify():
            raise OSError(f"{self.path}: the object changed while it was being downloaded")
        try:
            os.fsync(self._fd)
        except OSError:
            pass
        self.complete = True
        if self._on_complete is not None:
            self._on_complete(self.path)


class ProgressiveMmapSource(MmapSource):
    """:class:`MmapSource` over the file a :class:`ProgressiveDownload` is filling.

    Every access first makes its bytes present (:meth:`ProgressiveDownload.ensure`),
    so the decoder reads a file that is still arriving as if it were whole. Reads
    are the mmap's, zero-copy, as on a local file. ``u8_view`` -- the whole-buffer
    view the adacpp SIN loops read with no way to say which bytes they will touch
    -- is offered only once the download is complete; until then the reader takes
    its numpy path, which returns the same arrays bit for bit (``sin_native``).
    """

    def __init__(self, download: ProgressiveDownload):
        self._dl = download
        self._fh = open(download.path, "rb")
        try:
            buf = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ) if download.size() else b""
        except BaseException:
            self._fh.close()
            raise
        super().__init__(buf)

    def read(self, offset: int, length: int) -> bytes:
        self._dl.ensure(offset, length)
        return super().read(offset, length)

    def u32(self, offset: int) -> int:
        self._dl.ensure(offset, 4)
        return super().u32(offset)

    def f32(self, offset: int) -> float:
        self._dl.ensure(offset, 4)
        return super().f32(offset)

    def find(self, needle: bytes, start: int, stop: int) -> int:
        self._dl.ensure(start, min(stop, self._size) - start)
        return super().find(needle, start, stop)

    def frombuffer(self, dtype, count: int, offset: int) -> np.ndarray:
        self._dl.ensure(offset, count * np.dtype(dtype).itemsize)
        return super().frombuffer(dtype, count, offset)

    def gather_f32(self, word_idx: np.ndarray) -> np.ndarray:
        if not self._dl.complete:
            word_idx = np.asarray(word_idx, dtype=np.int64)
            if word_idx.size:
                self._dl.ensure_chunks(np.unique(self._dl.chunk_of(word_idx << 2)).tolist())
        return super().gather_f32(word_idx)

    def u8_view(self):  # type: ignore[override]
        return super().u8_view() if self._dl.complete else None

    def close(self) -> None:
        super().close()
        try:
            self._fh.close()
        except OSError:
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
    "HttpRangeSource",
    "ProgressiveDownload",
    "ProgressiveMmapSource",
    "DownloadCancelled",
    "register_progressive_file",
    "progressive_file",
    "file_paged_source",
]


# Local paths whose bytes are still arriving through a ProgressiveDownload. A
# reader that opens such a path by name (open_sin, deep inside the bake's reader
# factories) reads it through a ProgressiveMmapSource instead of mapping a file
# whose unarrived chunks are zeros. Keyed by the resolved path.
_PROGRESSIVE: dict[str, ProgressiveDownload] = {}
_PROGRESSIVE_LOCK = threading.Lock()


def _progressive_key(path) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def register_progressive_file(download: ProgressiveDownload):
    """Route opens of ``download.path`` through it until the returned callable is called."""
    key = _progressive_key(download.path)
    with _PROGRESSIVE_LOCK:
        _PROGRESSIVE[key] = download

    def unregister() -> None:
        with _PROGRESSIVE_LOCK:
            if _PROGRESSIVE.get(key) is download:
                del _PROGRESSIVE[key]

    return unregister


def progressive_file(path) -> ProgressiveDownload | None:
    """The still-arriving download registered for ``path``, if any (None once complete)."""
    with _PROGRESSIVE_LOCK:
        dl = _PROGRESSIVE.get(_progressive_key(path))
    return dl
