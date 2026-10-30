"""ProgressiveDownload: decode a remote file while it downloads.

A background thread copies the object sequentially; a read of chunks not yet on
disk fetches them at once (and the copy skips them); the copy counts as complete
-- and its ``on_complete`` (the worker's cache-validity marker) runs -- only
when every chunk is on disk and ``verify`` agrees.
"""

from __future__ import annotations

import os
import threading
import time

import numpy as np
import pytest

from ada.fem.formats.sesam.results.byte_source import (
    DownloadCancelled,
    MmapSource,
    ProgressiveDownload,
    ProgressiveMmapSource,
    progressive_file,
    register_progressive_file,
)

CHUNK = 4096


class Remote:
    """A range fetcher over bytes, recording fetches; ``gate`` holds the copy back."""

    def __init__(self, data: bytes, *, delay: float = 0.0, fail_at: int | None = None):
        self.data = data
        self.delay = delay
        self.fail_at = fail_at
        self.fetches: list[tuple[int, int, str]] = []
        self.gate = threading.Event()
        self.gate.set()
        self.lock = threading.Lock()

    def size(self) -> int:
        return len(self.data)

    def fetch(self, offset: int, length: int) -> bytes:
        name = threading.current_thread().name
        if name == "progressive-download":
            self.gate.wait()
        if self.fail_at is not None and offset <= self.fail_at < offset + length:
            raise OSError("remote went away")
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.fetches.append((offset, length, name))
        return self.data[offset : offset + length]

    def close(self) -> None:
        pass


def _payload(n_chunks: int, tail: int = 100) -> bytes:
    return np.arange((n_chunks * CHUNK + tail) // 4, dtype=np.float32).tobytes()


def test_sequential_copy_completes_and_marks_valid_only_at_the_end(tmp_path):
    data = _payload(10)
    remote = Remote(data)
    done: list[str] = []
    dl = ProgressiveDownload(remote, tmp_path / "f.sin", chunk_bytes=CHUNK, on_complete=done.append)
    assert dl.wait(10)
    assert dl.complete and done == [dl.path]
    assert (tmp_path / "f.sin").read_bytes() == data
    assert dl.bytes_sequential == len(data) and dl.bytes_on_demand == 0
    dl.close()


def test_read_of_uncovered_range_fetches_it_now_and_the_copy_skips_it(tmp_path):
    data = _payload(16)
    remote = Remote(data)
    remote.gate.clear()  # the sequential copy is stuck before its first chunk
    dl = ProgressiveDownload(remote, tmp_path / "f.sin", chunk_bytes=CHUNK)
    src = ProgressiveMmapSource(dl)
    off = 12 * CHUNK + 8
    assert src.read(off, 16) == data[off : off + 16]  # served without waiting for the copy
    assert dl.is_covered(12 * CHUNK, CHUNK) and not dl.is_covered(0, CHUNK)
    assert [f[0] for f in remote.fetches] == [12 * CHUNK]
    # A gather over scattered words fetches exactly the chunks they land in.
    words = np.array([3 * CHUNK // 4, 3 * CHUNK // 4 + 1, 14 * CHUNK // 4 + 7])
    np.testing.assert_array_equal(src.gather_f32(words), np.frombuffer(data, np.float32)[words])
    assert sorted(f[0] for f in remote.fetches) == [3 * CHUNK, 12 * CHUNK, 14 * CHUNK]
    # The native kernels' whole-buffer view waits for completeness.
    assert src.u8_view() is None
    remote.gate.set()
    assert dl.wait(10)
    # Every chunk fetched once in all: the copy skipped the three the reader took.
    offsets = [f[0] for f in remote.fetches]
    assert len(offsets) == len(set(offsets)) == 17
    assert dl.bytes_on_demand == 3 * CHUNK
    assert src.u8_view() is not None
    assert (tmp_path / "f.sin").read_bytes() == data
    src.close()
    dl.close()


def test_reads_match_a_plain_mmap_of_the_whole_file(tmp_path):
    data = _payload(8, tail=12)
    full = MmapSource(data)
    dl = ProgressiveDownload(Remote(data, delay=0.001), tmp_path / "f.sin", chunk_bytes=CHUNK)
    src = ProgressiveMmapSource(dl)
    for off in (0, CHUNK - 2, 5 * CHUNK + 3, len(data) - 4):
        assert src.read(off, 4) == full.read(off, 4)
        assert src.u32(off) == full.u32(off)
    np.testing.assert_array_equal(src.frombuffer(np.float32, 100, CHUNK), full.frombuffer(np.float32, 100, CHUNK))
    assert src.find(data[7 * CHUNK : 7 * CHUNK + 8], 0, len(data)) == full.find(
        data[7 * CHUNK : 7 * CHUNK + 8], 0, len(data)
    )
    dl.wait(10)
    src.close()
    dl.close()


def test_interrupted_copy_is_never_complete(tmp_path):
    data = _payload(6)
    done: list[str] = []
    dl = ProgressiveDownload(
        Remote(data, fail_at=4 * CHUNK + 1), tmp_path / "f.sin", chunk_bytes=CHUNK, on_complete=done.append
    )
    with pytest.raises(OSError, match="went away"):
        dl.wait(10)
    assert not dl.complete and done == []
    dl.close()


def test_unverified_copy_is_never_complete(tmp_path):
    done: list[str] = []
    dl = ProgressiveDownload(
        Remote(_payload(3)), tmp_path / "f.sin", chunk_bytes=CHUNK, verify=lambda: False, on_complete=done.append
    )
    with pytest.raises(OSError, match="changed"):
        dl.wait(10)
    assert not dl.complete and done == []
    dl.close()


def test_cancel_stops_the_copy_and_fails_waiting_reads(tmp_path):
    remote = Remote(_payload(64))
    remote.gate.clear()
    dl = ProgressiveDownload(remote, tmp_path / "f.sin", chunk_bytes=CHUNK)
    dl.cancel()
    remote.gate.set()
    with pytest.raises(DownloadCancelled):
        dl.ensure(10 * CHUNK, 4)
    with pytest.raises(DownloadCancelled):
        dl.wait(10)
    assert not dl.complete
    dl.close()
    assert len(remote.fetches) <= 1


def test_open_sin_routes_a_registered_path_through_its_download(tmp_path):
    path = tmp_path / "f.sin"
    dl = ProgressiveDownload(Remote(_payload(2)), path, chunk_bytes=CHUNK)
    unregister = register_progressive_file(dl)
    try:
        assert progressive_file(str(path)) is dl
        assert progressive_file(os.path.join(str(tmp_path), ".", "f.sin")) is dl
    finally:
        unregister()
    assert progressive_file(path) is None
    dl.wait(10)
    dl.close()
