"""Cross-job source-blob cache (``source_cache.SourceBlobCache``).

An audit sweep converts one source to many targets; the worker used to
re-download the source per target. These tests pin the cache contract
against a LocalStore-backed Storage — the same backend shape the other
storage tests use: versioned hits, no stale serves after an overwrite,
LRU eviction under the cap, torn-entry recovery, fail-open fallback, and
the FileNotFoundError contract for missing sources.
"""

from __future__ import annotations

import asyncio
import gzip
import pathlib

from obstore.store import LocalStore

from ada.comms.rest.scope import Scope
from ada.comms.rest.source_cache import (
    MODE_DIRECT,
    MODE_HIT,
    MODE_MISS,
    SourceBlobCache,
)
from ada.comms.rest.storage import Storage


def _storage(tmp_path: pathlib.Path) -> Storage:
    bucket = tmp_path / "bucket"
    bucket.mkdir(exist_ok=True)
    return Storage(LocalStore(str(bucket)), prefix="")


def _cache(tmp_path: pathlib.Path, cap_bytes: int = 64 << 20) -> SourceBlobCache:
    return SourceBlobCache(tmp_path / "cache", cap_bytes)


def _count_downloads(storage: Storage) -> list:
    """Instrument ``stream_to_path`` so a test can assert whether the
    cache actually skipped the object-store download."""
    calls: list = []
    orig = storage.stream_to_path

    async def counting(scope, key, dest):
        calls.append(key)
        return await orig(scope, key, dest)

    storage.stream_to_path = counting  # type: ignore[method-assign]
    return calls


def test_miss_downloads_and_caches_then_hit_skips_download(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path)
    payload = b"ISO-10303-21;" + b"x" * 50_000
    asyncio.run(storage.put_bytes(scope, "model.step", payload))
    calls = _count_downloads(storage)

    dest1 = tmp_path / "job1.step"
    mode1 = asyncio.run(cache.fetch(storage, scope, "model.step", dest1))
    assert mode1 == MODE_MISS
    assert dest1.read_bytes() == payload
    assert len(calls) == 1

    # Second job, fresh dest: served from cache — no second download.
    dest2 = tmp_path / "job2.step"
    mode2 = asyncio.run(cache.fetch(storage, scope, "model.step", dest2))
    assert mode2 == MODE_HIT
    assert dest2.read_bytes() == payload
    assert len(calls) == 1


def test_job_unlinking_its_dest_does_not_break_the_cache(tmp_path):
    # The worker unlinks its temp source path after every job; with a
    # hard-linked hand-off that must only drop the job's own link.
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path)
    payload = b"solid content" * 1000
    asyncio.run(storage.put_bytes(scope, "part.stp", payload))

    dest1 = tmp_path / "j1.stp"
    asyncio.run(cache.fetch(storage, scope, "part.stp", dest1))
    dest1.unlink()

    dest2 = tmp_path / "j2.stp"
    assert asyncio.run(cache.fetch(storage, scope, "part.stp", dest2)) == MODE_HIT
    assert dest2.read_bytes() == payload


def test_version_change_is_never_served_stale(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path)
    asyncio.run(storage.put_bytes(scope, "model.xml", b"<v1/>" * 100))
    dest1 = tmp_path / "j1.xml"
    asyncio.run(cache.fetch(storage, scope, "model.xml", dest1))

    # Overwrite the source (different size so the version token changes on
    # every backend, etag or not).
    new_payload = b"<v2 attr='y'/>" * 200
    asyncio.run(storage.put_bytes(scope, "model.xml", new_payload))

    dest2 = tmp_path / "j2.xml"
    mode = asyncio.run(cache.fetch(storage, scope, "model.xml", dest2))
    assert mode == MODE_MISS
    assert dest2.read_bytes() == new_payload


def test_gzip_stored_source_is_cached_decompressed(tmp_path):
    # stream_to_path inflates gzip-at-rest sources; the cache must keep the
    # inflated form so hits hand the converter valid plain bytes.
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path)
    payload = b"<xml>" + b"a" * 200_000 + b"</xml>"
    asyncio.run(storage.put_bytes(scope, "big.xml", gzip.compress(payload), pre_compressed=True))

    dest1 = tmp_path / "j1.xml"
    assert asyncio.run(cache.fetch(storage, scope, "big.xml", dest1)) == MODE_MISS
    assert dest1.read_bytes() == payload
    dest2 = tmp_path / "j2.xml"
    assert asyncio.run(cache.fetch(storage, scope, "big.xml", dest2)) == MODE_HIT
    assert dest2.read_bytes() == payload


def test_cap_eviction_drops_oldest_entry(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path, cap_bytes=150_000)
    asyncio.run(storage.put_bytes(scope, "a.step", b"A" * 100_000))
    asyncio.run(storage.put_bytes(scope, "b.step", b"B" * 100_000))

    asyncio.run(cache.fetch(storage, scope, "a.step", tmp_path / "ja.step"))
    # Age entry A so the LRU order is deterministic regardless of fs
    # timestamp resolution.
    import os

    (entry_a,) = cache._entries()
    os.utime(entry_a, (1, 1))

    asyncio.run(cache.fetch(storage, scope, "b.step", tmp_path / "jb.step"))
    names = {p.name for p in cache._entries()}
    assert entry_a.name not in names  # oldest evicted
    assert len(names) == 1

    # Evicted entry re-fetches as a miss and still serves correct bytes.
    dest = tmp_path / "ja2.step"
    assert asyncio.run(cache.fetch(storage, scope, "a.step", dest)) == MODE_MISS
    assert dest.read_bytes() == b"A" * 100_000


def test_blob_larger_than_cap_is_served_but_not_kept(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path, cap_bytes=10_000)
    payload = b"Z" * 50_000
    asyncio.run(storage.put_bytes(scope, "huge.sat", payload))

    dest = tmp_path / "j.sat"
    assert asyncio.run(cache.fetch(storage, scope, "huge.sat", dest)) == MODE_MISS
    assert dest.read_bytes() == payload
    assert cache._entries() == []  # over-cap blob not retained


def test_truncated_cache_entry_is_a_miss_not_a_corrupt_source(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path)
    payload = b"P" * 40_000
    asyncio.run(storage.put_bytes(scope, "m.ifc", payload))
    asyncio.run(cache.fetch(storage, scope, "m.ifc", tmp_path / "j1.ifc"))

    (entry,) = cache._entries()
    entry.write_bytes(payload[: len(payload) // 2])  # simulate torn/partial write

    dest = tmp_path / "j2.ifc"
    assert asyncio.run(cache.fetch(storage, scope, "m.ifc", dest)) == MODE_MISS
    assert dest.read_bytes() == payload


def test_missing_meta_sidecar_is_a_miss(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path)
    asyncio.run(storage.put_bytes(scope, "m.step", b"S" * 10_000))
    asyncio.run(cache.fetch(storage, scope, "m.step", tmp_path / "j1.step"))

    (entry,) = cache._entries()
    entry.with_name(entry.name + ".meta").unlink()

    dest = tmp_path / "j2.step"
    assert asyncio.run(cache.fetch(storage, scope, "m.step", dest)) == MODE_MISS
    assert dest.read_bytes() == b"S" * 10_000


def test_cap_zero_disables_cache_entirely(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path, cap_bytes=0)
    asyncio.run(storage.put_bytes(scope, "m.step", b"D" * 1000))

    dest = tmp_path / "j.step"
    assert asyncio.run(cache.fetch(storage, scope, "m.step", dest)) == MODE_DIRECT
    assert dest.read_bytes() == b"D" * 1000
    assert not cache.cache_dir.exists()


def test_missing_source_raises_file_not_found(tmp_path):
    import pytest

    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path)
    with pytest.raises(FileNotFoundError):
        asyncio.run(cache.fetch(storage, scope, "nope.step", tmp_path / "j.step"))


def test_cache_error_falls_back_to_direct_download(tmp_path):
    # A broken cache must never fail the job: sabotage the version probe
    # and the fetch must still deliver the bytes via the direct path.
    storage = _storage(tmp_path)
    scope = Scope.shared()
    cache = _cache(tmp_path)
    payload = b"F" * 5000
    asyncio.run(storage.put_bytes(scope, "m.step", payload))

    async def broken_head(scope_, key_):
        raise RuntimeError("metadata service down")

    storage.head = broken_head  # type: ignore[method-assign]
    dest = tmp_path / "j.step"
    assert asyncio.run(cache.fetch(storage, scope, "m.step", dest)) == MODE_DIRECT
    assert dest.read_bytes() == payload


def test_scopes_do_not_collide(tmp_path):
    # Same key in two scopes must be two distinct cache entries.
    storage = _storage(tmp_path)
    cache = _cache(tmp_path)
    scope_a, scope_b = Scope.user("alice"), Scope.user("bob")
    asyncio.run(storage.put_bytes(scope_a, "m.step", b"alice-model"))
    asyncio.run(storage.put_bytes(scope_b, "m.step", b"bob-model"))

    dest_a, dest_b = tmp_path / "ja.step", tmp_path / "jb.step"
    asyncio.run(cache.fetch(storage, scope_a, "m.step", dest_a))
    asyncio.run(cache.fetch(storage, scope_b, "m.step", dest_b))
    assert dest_a.read_bytes() == b"alice-model"
    assert dest_b.read_bytes() == b"bob-model"
    assert len(cache._entries()) == 2
