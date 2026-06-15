"""Browser-baked FEA artefact upload (POST /scopes/{scope}/fea/artefacts).

The pyodide FEM stack bakes the streaming-FEA tree in the browser and
POSTs it as a single zip; the endpoint must drop each entry under
``_derived/<source>.fea/`` with the worker's gzip policy, and reject
crafted zips (missing manifest, path traversal, non-FEA source). No DB
needed — the endpoint is pure storage.
"""

from __future__ import annotations

import asyncio
import io
import os
import pathlib
import tempfile
import zipfile

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
    # A fresh loop per call: the ambient loop may be closed by
    # pytest-asyncio tests that ran earlier in the suite, so
    # ``get_event_loop().run_until_complete`` is order-dependent.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _stage(client: TestClient, key: str, data: bytes) -> None:
    _run(_storage(client).put_bytes(Scope.shared(), key, data))


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buf.getvalue()


_GOOD_TREE = {
    "fea.manifest.json": b'{"source": "models/wall.rmed", "version": 2, "fields": []}',
    "fea.mesh.glb": b"glTF-binary-bytes",
    "fea.DISPLACEMENT.bin": b"\x00\x01\x02\x03",
}


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def test_upload_writes_tree_under_prefix(app_client: TestClient):
    src = "models/wall.rmed"
    _stage(app_client, src, b"fake rmed source")
    r = app_client.post(
        f"/api/scopes/shared/fea/artefacts?source={src}",
        content=_make_zip(_GOOD_TREE),
        headers={"content-type": "application/zip"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 3
    assert body["manifest_key"] == "_derived/models/wall.rmed.fea/fea.manifest.json"

    storage = _storage(app_client)
    for name in _GOOD_TREE:
        key = f"_derived/models/wall.rmed.fea/{name}"
        assert _run(storage.exists(Scope.shared(), key)), key


def test_upload_rejects_missing_manifest(app_client: TestClient):
    src = "models/wall.rmed"
    _stage(app_client, src, b"fake rmed source")
    bad = {"fea.mesh.glb": b"x", "fea.DISPLACEMENT.bin": b"y"}
    r = app_client.post(
        f"/api/scopes/shared/fea/artefacts?source={src}",
        content=_make_zip(bad),
        headers={"content-type": "application/zip"},
    )
    assert r.status_code == 400
    assert "manifest" in r.text.lower()


def test_upload_rejects_path_traversal_entry(app_client: TestClient):
    src = "models/wall.rmed"
    _stage(app_client, src, b"fake rmed source")
    evil = dict(_GOOD_TREE)
    evil["../../etc/fea.evil"] = b"pwn"
    r = app_client.post(
        f"/api/scopes/shared/fea/artefacts?source={src}",
        content=_make_zip(evil),
        headers={"content-type": "application/zip"},
    )
    assert r.status_code == 400


def test_upload_rejects_non_fea_source(app_client: TestClient):
    src = "models/part.step"
    _stage(app_client, src, b"fake step")
    r = app_client.post(
        f"/api/scopes/shared/fea/artefacts?source={src}",
        content=_make_zip(_GOOD_TREE),
        headers={"content-type": "application/zip"},
    )
    assert r.status_code == 415


def test_upload_404_for_missing_source(app_client: TestClient):
    r = app_client.post(
        "/api/scopes/shared/fea/artefacts?source=models/ghost.rmed",
        content=_make_zip(_GOOD_TREE),
        headers={"content-type": "application/zip"},
    )
    assert r.status_code == 404


def test_upload_rejects_bad_zip(app_client: TestClient):
    src = "models/wall.rmed"
    _stage(app_client, src, b"fake rmed source")
    r = app_client.post(
        f"/api/scopes/shared/fea/artefacts?source={src}",
        content=b"this is not a zip",
        headers={"content-type": "application/zip"},
    )
    assert r.status_code == 400
