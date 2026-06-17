"""HTTP Range support on GET /scopes/{scope}/blobs/{key} (the FEA
per-step field fetch). Identity-stored objects serve a 206 byte range;
gzip-at-rest objects fall back to the whole object (200) because a range
over compressed bytes can't map to logical step offsets.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.scope import Scope  # noqa: E402


def _settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=None,
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="ada-viewer-jobs",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(
            enabled=False,
            issuer="",
            client_id="",
            audience="",
            admin_group="",
            cli_token_secret="",
        ),
        database_url="",
    )


def _storage(client: TestClient):
    for route in client.app.routes:
        ep = getattr(route, "endpoint", None)
        for c in getattr(ep, "__closure__", None) or ():
            v = c.cell_contents
            if v.__class__.__name__ == "Storage":
                return v
    raise RuntimeError("storage not found on app")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def test_range_returns_206_for_identity_object(app_client: TestClient):
    # AFBL-style payload (ASCII magic, never gzip-sniffed) stored identity.
    body = b"AFBL" + bytes(range(0, 252))  # 256 bytes, deterministic
    _run(_storage(app_client).put_bytes(Scope.shared(), "_derived/m.sin.fea/fea.U.bin", body))

    r = app_client.get(
        "/api/scopes/shared/blobs/_derived/m.sin.fea/fea.U.bin",
        headers={"Range": "bytes=8-15"},
    )
    assert r.status_code == 206, r.text
    assert r.content == body[8:16]
    assert r.headers["content-range"] == f"bytes 8-15/{len(body)}"
    assert r.headers["accept-ranges"] == "bytes"


def test_range_suffix_and_open_ended(app_client: TestClient):
    body = b"AFEL" + bytes(range(0, 60))  # 64 bytes
    _run(_storage(app_client).put_bytes(Scope.shared(), "_derived/m.sin.fea/fea.S.q.elements.bin", body))
    key = "/api/scopes/shared/blobs/_derived/m.sin.fea/fea.S.q.elements.bin"

    # open-ended: bytes=60- → tail from offset 60
    r = app_client.get(key, headers={"Range": "bytes=60-"})
    assert r.status_code == 206
    assert r.content == body[60:]

    # suffix: last 4 bytes
    r = app_client.get(key, headers={"Range": "bytes=-4"})
    assert r.status_code == 206
    assert r.content == body[-4:]


def test_range_unsatisfiable_returns_416(app_client: TestClient):
    body = b"AFBL" + bytes(16)
    _run(_storage(app_client).put_bytes(Scope.shared(), "_derived/m.sin.fea/fea.U.bin", body))
    r = app_client.get(
        "/api/scopes/shared/blobs/_derived/m.sin.fea/fea.U.bin",
        headers={"Range": f"bytes={len(body)}-{len(body) + 10}"},
    )
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(body)}"


def test_range_on_gzip_object_falls_back_to_whole(app_client: TestClient):
    # A gzip-at-rest object (the manifest, or a legacy field blob). A range
    # can't be honoured, so the server serves the whole object instead.
    raw = b'{"hello":"world","pad":"' + b"x" * 200 + b'"}'
    _run(
        _storage(app_client).put_bytes(
            Scope.shared(), "_derived/m.sin.fea/fea.manifest.json", raw, content_encoding="gzip"
        )
    )

    r = app_client.get(
        "/api/scopes/shared/blobs/_derived/m.sin.fea/fea.manifest.json",
        headers={"Range": "bytes=0-9"},
    )
    # Not a 206 — the whole (gzipped) object came back. The TestClient
    # transparently decodes Content-Encoding: gzip, so r.content is the
    # full decompressed payload, not a 10-byte slice.
    assert r.status_code == 200
    assert r.content == raw


def test_no_range_header_serves_whole(app_client: TestClient):
    body = b"AFBL" + bytes(range(0, 60))
    _run(_storage(app_client).put_bytes(Scope.shared(), "_derived/m.sin.fea/fea.U.bin", body))
    r = app_client.get("/api/scopes/shared/blobs/_derived/m.sin.fea/fea.U.bin")
    assert r.status_code == 200
    assert r.content == body
    assert r.headers.get("accept-ranges") == "bytes"
