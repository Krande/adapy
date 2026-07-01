"""Streaming file upload (``Storage.put_path``).

``put_path`` is the disk-to-object-store mirror of ``put_bytes``: it
uploads a local file via obstore multipart so a large conversion output
never gets materialised as a whole ``bytes`` object on the upload side.
These tests pin the round-trip semantics (plain, identity, and both gzip
forms) against a LocalStore — the same backend shape the no-DB / local
deployments run — plus the gzip header/disk-cleanup invariants.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import pathlib

import pytest
from obstore.store import LocalStore

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


def test_put_path_plain_reports_no_compression(tmp_path):
    storage = _storage(tmp_path)
    scope = Scope.shared()
    payload = os.urandom(1_500_000)
    src = _src(tmp_path, "out.step", payload)

    timing = asyncio.run(storage.put_path(scope, "derived/out.step", src))

    assert timing["compress_ms"] is None  # no gzip pass on the plain path
    assert isinstance(timing["upload_ms"], int)
    assert timing["stored_bytes"] == len(payload)
