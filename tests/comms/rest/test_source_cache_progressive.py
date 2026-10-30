"""Decode while the source downloads: the worker's source cache side.

``SourceBlobCache.open_progressive`` starts a background download into the cache
entry's file that a decoder can read at once; the entry becomes a hit only once
every chunk is on disk and the object is unchanged -- an interrupted or
cancelled download never does. A gzip-at-rest object cannot be ranged and is
left to the plain download.
"""

from __future__ import annotations

import asyncio
import pathlib
import threading

import pytest
from obstore.store import LocalStore

from ada.comms.rest.scope import Scope
from ada.comms.rest.source_cache import MODE_HIT, SourceBlobCache
from ada.comms.rest.storage import Storage


def _storage(tmp_path: pathlib.Path) -> Storage:
    bucket = tmp_path / "bucket"
    bucket.mkdir(exist_ok=True)
    return Storage(LocalStore(str(bucket)), prefix="")


PAYLOAD = bytes(range(256)) * (4096 * 3 // 256 * 10 + 7)  # ~30 chunks of 4 KiB, ragged tail


def test_progressive_download_becomes_a_cache_hit_only_when_complete(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = SourceBlobCache(tmp_path / "cache", 64 << 20)

    async def run():
        await storage.put_bytes(scope, "deck.sin", PAYLOAD)
        dl = await cache.open_progressive(storage, scope, "deck.sin", tmp_path / "job.sin", chunk_bytes=4096)
        assert dl is not None
        meta = pathlib.Path(dl.path).with_name(pathlib.Path(dl.path).name + ".meta")
        # A read in the middle is served at once, from a ranged GET.
        await asyncio.to_thread(dl.ensure, 20 * 4096 + 5, 100)
        assert (
            pathlib.Path(dl.path).read_bytes()[20 * 4096 + 5 : 20 * 4096 + 105]
            == PAYLOAD[20 * 4096 + 5 : 20 * 4096 + 105]
        )
        assert await asyncio.to_thread(dl.wait, 30)
        assert meta.exists()
        await asyncio.to_thread(dl.close)
        # The next job of the same version: a plain hit, nothing to download.
        assert await cache.open_progressive(storage, scope, "deck.sin", tmp_path / "job2.sin") is None
        assert await cache.fetch(storage, scope, "deck.sin", tmp_path / "job3.sin") == MODE_HIT
        assert (tmp_path / "job3.sin").read_bytes() == PAYLOAD

    asyncio.run(run())


def test_cancelled_progressive_download_is_never_a_hit(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = SourceBlobCache(tmp_path / "cache", 64 << 20)
    gate = threading.Event()

    async def run():
        await storage.put_bytes(scope, "deck.sin", PAYLOAD)
        real = storage.get_range

        async def slow(scope_, key, start, length):
            await asyncio.to_thread(gate.wait, 5)
            return await real(scope_, key, start, length)

        storage.get_range = slow  # type: ignore[method-assign]
        dl = await cache.open_progressive(storage, scope, "deck.sin", tmp_path / "job.sin", chunk_bytes=4096)
        dl.cancel()
        gate.set()
        await asyncio.to_thread(dl.close)
        assert not dl.complete
        entry = pathlib.Path(dl.path)
        assert not entry.with_name(entry.name + ".meta").exists()
        storage.get_range = real  # type: ignore[method-assign]
        # Not a hit: the next open starts a fresh download.
        again = await cache.open_progressive(storage, scope, "deck.sin", tmp_path / "job2.sin", chunk_bytes=4096)
        assert again is not None
        assert await asyncio.to_thread(again.wait, 30)
        await asyncio.to_thread(again.close)
        assert pathlib.Path(again.path).read_bytes() == PAYLOAD

    asyncio.run(run())


def test_gzip_at_rest_source_is_not_progressive(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = SourceBlobCache(tmp_path / "cache", 64 << 20)

    async def run():
        await storage.put_bytes(scope, "deck.sin", PAYLOAD, content_encoding="gzip")
        assert await cache.open_progressive(storage, scope, "deck.sin", tmp_path / "job.sin") is None
        with pytest.raises(FileNotFoundError):
            await cache.open_progressive(storage, scope, "missing.sin", tmp_path / "x.sin")

    asyncio.run(run())
