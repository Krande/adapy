"""Streaming file upload (``Storage.put_path``).

``put_path`` is the disk-to-object-store mirror of ``put_bytes``: it
uploads a local file via obstore multipart so a large conversion output
never gets materialised as a whole ``bytes`` object on the upload side.
These tests pin the round-trip semantics (plain, identity, and both gzip
forms) against a LocalStore — the same backend shape the no-DB / local
deployments run — plus the gzip header/disk-cleanup invariants, that the
upload's memory does not grow with the file, and (when an endpoint is
configured) the same contract against an S3-compatible store.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import os
import pathlib
import tracemalloc
import uuid
from typing import TYPE_CHECKING

import obstore as obs
import pytest
from obstore.store import LocalStore

if TYPE_CHECKING:
    from obstore.store import S3Store

from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage, _gzip_file, _gzip_level


def _storage(tmp_path: pathlib.Path) -> Storage:
    return Storage(LocalStore(str(tmp_path)), prefix="")


def _src(tmp_path: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_put_path_round_trips_plain(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    payload = os.urandom(3_000_000)  # > one multipart part: exercises the streaming path
    src = _src(tmp_path, "out.step", payload)

    asyncio.run(storage.put_path(scope, "derived/out.step", src))
    got = asyncio.run(storage.get_bytes(scope, "derived/out.step"))

    assert got == payload


def test_put_path_empty_file(tmp_path):
    # Empty conversion outputs (seeded empty scenes etc.) must still upload.
    storage = _storage(tmp_path)
    scope = Scope.shared()
    src = _src(tmp_path, "empty.bin", b"")

    asyncio.run(storage.put_path(scope, "derived/empty.bin", src))
    assert asyncio.run(storage.get_bytes(scope, "derived/empty.bin")) == b""


def test_put_path_gzip_compresses_and_get_bytes_decompresses(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    payload = b"<xml>" + b"a" * 500_000 + b"</xml>"  # highly compressible, like Genie XML
    src = _src(tmp_path, "model.xml", payload)

    asyncio.run(storage.put_path(scope, "derived/model.xml", src, content_encoding="gzip"))

    # Stored bytes are gzip (magic header); get_bytes transparently inflates.
    stored = asyncio.run(storage.get_range(scope, "derived/model.xml", 0, 2))
    assert stored == b"\x1f\x8b"
    assert asyncio.run(storage.get_bytes(scope, "derived/model.xml")) == payload


def test_put_path_pre_compressed_uploads_as_is(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    payload = b"already gzipped content" * 1000
    gz = tmp_path / "blob.ifc.gz"
    gz.write_bytes(gzip.compress(payload))

    asyncio.run(storage.put_path(scope, "derived/blob.ifc", gz, content_encoding="gzip", pre_compressed=True))

    assert asyncio.run(storage.get_range(scope, "derived/blob.ifc", 0, 2)) == b"\x1f\x8b"
    assert asyncio.run(storage.get_bytes(scope, "derived/blob.ifc")) == payload


def test_put_path_gzip_does_not_leave_temp_file(tmp_path):
    # The on-disk gzip staging file must be cleaned up after upload.
    storage = _storage(tmp_path)
    scope = Scope.shared()
    src = _src(tmp_path, "leak.xml", b"x" * 10_000)

    asyncio.run(storage.put_path(scope, "derived/leak.xml", src, content_encoding="gzip"))

    assert not (tmp_path / "leak.xml.gz").exists()
    # The original source is left untouched (caller owns its lifecycle).
    assert src.exists()


def test_put_path_rejects_unknown_encoding(tmp_path):
    storage = _storage(tmp_path)
    src = _src(tmp_path, "x.bin", b"x")
    with pytest.raises(ValueError, match="unsupported content_encoding"):
        asyncio.run(storage.put_path(Scope.shared(), "derived/x.bin", src, content_encoding="br"))


def test_put_path_matches_put_bytes(tmp_path):
    # put_path and put_bytes must be interchangeable from the reader's side.
    storage = _storage(tmp_path)
    scope = Scope.shared()
    payload = b"interchangeable" * 4096
    src = _src(tmp_path, "a.glb", payload)

    asyncio.run(storage.put_path(scope, "via_path.glb", src))
    asyncio.run(storage.put_bytes(scope, "via_bytes.glb", payload))

    assert asyncio.run(storage.get_bytes(scope, "via_path.glb")) == asyncio.run(
        storage.get_bytes(scope, "via_bytes.glb")
    )


def test_gzip_file_helper_streams_round_trip(tmp_path):
    src = _src(tmp_path, "big.txt", os.urandom(2_500_000))
    dst = tmp_path / "big.txt.gz"
    _gzip_file(src, dst, chunk_size=64 * 1024)

    assert dst.read_bytes()[:2] == b"\x1f\x8b"
    assert gzip.decompress(dst.read_bytes()) == src.read_bytes()


def test_gzip_file_output_is_standard_gzip_regardless_of_engine(tmp_path):
    # pigz (parallel) and the zlib fallback must both emit a stream that plain
    # gzip.decompress reads — the Content-Encoding: gzip download path depends on it.
    payload = b"<obj>" + b"v 1.0 2.0 3.0\n" * 200_000  # verbose ASCII like an OBJ export
    src = _src(tmp_path, "mesh.obj", payload)
    dst = tmp_path / "mesh.obj.gz"
    _gzip_file(src, dst)

    assert dst.read_bytes()[:2] == b"\x1f\x8b"
    assert gzip.decompress(dst.read_bytes()) == payload


def test_gzip_level_defaults_to_6_and_honours_env(monkeypatch):
    monkeypatch.delenv("ADA_DERIVED_GZIP_LEVEL", raising=False)
    assert _gzip_level() == 6  # NOT the zlib/gzip.open default of 9
    monkeypatch.setenv("ADA_DERIVED_GZIP_LEVEL", "1")
    assert _gzip_level() == 1
    monkeypatch.setenv("ADA_DERIVED_GZIP_LEVEL", "99")  # clamped to 9
    assert _gzip_level() == 9
    monkeypatch.setenv("ADA_DERIVED_GZIP_LEVEL", "garbage")  # invalid -> default
    assert _gzip_level() == 6


def test_put_path_gzip_returns_compress_and_upload_timing(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    payload = b"<xml>" + b"a" * 400_000 + b"</xml>"
    src = _src(tmp_path, "model.xml", payload)

    timing = asyncio.run(storage.put_path(scope, "derived/model.xml", src, content_encoding="gzip"))

    assert isinstance(timing, dict)
    assert isinstance(timing["compress_ms"], int)  # a gzip pass ran
    assert isinstance(timing["upload_ms"], int)
    # highly compressible input, so the stored (gzipped) size is well under the raw size
    assert 0 < timing["stored_bytes"] < len(payload)


def _big_file(path: pathlib.Path, size: int) -> pathlib.Path:
    # Written a MiB at a time so the fixture itself never holds the payload.
    with open(path, "wb") as fh:
        for _ in range(size >> 20):
            fh.write(os.urandom(1 << 20))
    return path


def _traced_peak(fn) -> int:
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("content_encoding", [None, "gzip"])
def test_put_path_memory_is_not_proportional_to_file_size(tmp_path, content_encoding):
    # The reason put_path exists: a multi-GB artefact must go up a part at a time. The
    # read_bytes() baseline is measured alongside, so the bound is shown to be one tracemalloc
    # can actually see (the parts obstore reads are Python bytes objects).
    size = 64 << 20
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    storage = Storage(LocalStore(str(store_dir)), prefix="")
    scope = Scope.shared()
    src = _big_file(tmp_path / "field.bin", size)

    whole = _traced_peak(lambda: asyncio.run(storage.put_bytes(scope, "whole.bin", src.read_bytes())))
    streamed = _traced_peak(
        lambda: asyncio.run(storage.put_path(scope, "streamed.bin", src, content_encoding=content_encoding))
    )

    assert whole >= size
    assert streamed < size // 4, f"streamed upload peaked at {streamed >> 20} MiB for a {size >> 20} MiB file"
    with open(src, "rb") as fh:
        expected = hashlib.file_digest(fh, "sha256").hexdigest()
    assert hashlib.sha256(asyncio.run(storage.get_bytes(scope, "streamed.bin"))).hexdigest() == expected


def test_sync_facade_put_path_streams_through_the_loop(tmp_path):
    # The scope-bound facade handlers run in a thread: put_path must land the same object
    # put_bytes would, and hand back put_path's timing dict.
    from ada.comms.rest.worker.source_nodes import _SyncStorageFacade

    store_dir = tmp_path / "store"
    store_dir.mkdir()
    storage = Storage(LocalStore(str(store_dir)), prefix="")
    scope = Scope.shared()
    payload = os.urandom(6 << 20)  # past one part, so the multipart branch runs
    src = _src(tmp_path, "export.step", payload)
    manifest = _src(tmp_path, "manifest.json", b'{"steps": 3}' * 1000)

    async def _main():
        loop = asyncio.get_running_loop()
        facade = _SyncStorageFacade(storage, scope, loop)

        def _in_thread():
            return (
                facade.put_path("out/export.step", src),
                facade.put_path("out/manifest.json", manifest, content_encoding="gzip"),
            )

        return await loop.run_in_executor(None, _in_thread)

    plain, gz = asyncio.run(_main())

    assert plain["stored_bytes"] == len(payload) and plain["compress_ms"] is None
    assert gz["compress_ms"] is not None
    assert asyncio.run(storage.get_bytes(scope, "out/export.step")) == payload
    assert asyncio.run(storage.get_range(scope, "out/manifest.json", 0, 2)) == b"\x1f\x8b"
    assert asyncio.run(storage.get_bytes(scope, "out/manifest.json")) == manifest.read_bytes()


# --- S3-compatible backend -----------------------------------------------------------------------
# No in-process S3 fake ships with the test environment, so these run against a real endpoint
# when one is configured and skip otherwise:
#   ADA_TEST_S3_ENDPOINT, ADA_TEST_S3_BUCKET, ADA_TEST_S3_ACCESS_KEY, ADA_TEST_S3_SECRET_KEY
#   (and optionally ADA_TEST_S3_REGION, default "garage").
_S3_ENV = ("ADA_TEST_S3_ENDPOINT", "ADA_TEST_S3_BUCKET", "ADA_TEST_S3_ACCESS_KEY", "ADA_TEST_S3_SECRET_KEY")
s3_only = pytest.mark.skipif(
    not all(os.environ.get(k) for k in _S3_ENV), reason="no S3 endpoint configured (ADA_TEST_S3_*)"
)


def _s3_storage() -> "tuple[Storage, S3Store, str]":
    from obstore.store import S3Store

    endpoint = os.environ["ADA_TEST_S3_ENDPOINT"]
    store = S3Store(
        os.environ["ADA_TEST_S3_BUCKET"],
        endpoint=endpoint,
        region=os.environ.get("ADA_TEST_S3_REGION", "garage"),
        access_key_id=os.environ["ADA_TEST_S3_ACCESS_KEY"],
        secret_access_key=os.environ["ADA_TEST_S3_SECRET_KEY"],
        allow_http=endpoint.lower().startswith("http://"),
        virtual_hosted_style_request=False,
    )
    prefix = f"_test/put-path-{uuid.uuid4().hex[:12]}"
    return Storage(store, prefix=prefix), store, prefix


async def _s3_cleanup(store, prefix: str) -> None:
    async for page in obs.list(store, prefix=prefix):
        for meta in page:
            await store.delete_async(meta["path"])


async def _stored(store, full_key: str) -> "tuple[bytes, dict]":
    result = await obs.get_async(store, full_key)
    return bytes(await result.bytes_async()), dict(result.attributes or {})


@s3_only
@pytest.mark.parametrize("size", [1_000_000, 12 << 20])  # one single PUT, one multipart upload
def test_s3_put_path_matches_put_bytes(tmp_path, size):
    storage, store, prefix = _s3_storage()
    scope = Scope.shared()
    payload = os.urandom(size)
    src = _src(tmp_path, "field.bin", payload)
    manifest = _src(tmp_path, "manifest.json", b'{"fields": ["u", "s"]}' * 20_000)

    async def _main():
        try:
            await storage.put_path(scope, "a/field.bin", src)
            await storage.put_bytes(scope, "b/field.bin", payload)
            await storage.put_path(scope, "a/manifest.json", manifest, content_encoding="gzip")
            await storage.put_bytes(scope, "b/manifest.json", manifest.read_bytes(), content_encoding="gzip")
            full = storage._full_key
            return (
                await _stored(store, full(scope, "a/field.bin")),
                await _stored(store, full(scope, "b/field.bin")),
                await _stored(store, full(scope, "a/manifest.json")),
                await _stored(store, full(scope, "b/manifest.json")),
            )
        finally:
            await _s3_cleanup(store, prefix)

    (a_bin, a_bin_attrs), (b_bin, b_bin_attrs), (a_js, a_js_attrs), (b_js, b_js_attrs) = asyncio.run(_main())

    # Identity blobs: byte-identical, and neither carries an encoding (Range reads depend on it).
    assert a_bin == b_bin == payload
    assert a_bin_attrs.get("Content-Encoding") is None and b_bin_attrs.get("Content-Encoding") is None
    # gzip-at-rest: both labelled, both inflate to the same document (the compressed bytes may
    # differ between engines -- the gzip header carries a timestamp).
    assert a_js_attrs.get("Content-Encoding") == b_js_attrs.get("Content-Encoding") == "gzip"
    assert gzip.decompress(a_js) == gzip.decompress(b_js) == manifest.read_bytes()


@s3_only
def test_s3_put_path_memory_is_not_proportional_to_file_size(tmp_path):
    size = 64 << 20
    storage, store, prefix = _s3_storage()
    scope = Scope.shared()
    src = _big_file(tmp_path / "field.bin", size)
    try:
        streamed = _traced_peak(lambda: asyncio.run(storage.put_path(scope, "field.bin", src)))
        head = asyncio.run(storage.head(scope, "field.bin"))
    finally:
        asyncio.run(_s3_cleanup(store, prefix))
    assert head is not None and head["size"] == size
    assert streamed < size // 2, f"streamed upload peaked at {streamed >> 20} MiB for a {size >> 20} MiB file"


def test_put_path_plain_reports_no_compression(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    payload = os.urandom(1_500_000)
    src = _src(tmp_path, "out.step", payload)

    timing = asyncio.run(storage.put_path(scope, "derived/out.step", src))

    assert timing["compress_ms"] is None  # no gzip pass on the plain path
    assert isinstance(timing["upload_ms"], int)
    assert timing["stored_bytes"] == len(payload)
