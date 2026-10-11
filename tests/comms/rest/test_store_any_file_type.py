"""Storing a blob is not gated on its file type.

PUT blobs, upload-url and upload-complete take any key outside ``_derived/``:
raw solver output without an extension, logs, archives. Such a file is listed
with no conversion targets and served as an opaque download. Converting it is
still refused, because the accepted-source whitelist moved to the endpoints
that convert or bake a source.

Same env shim + LocalStore staging as test_published_asset_keys.py.
"""

from __future__ import annotations

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
from ada.comms.rest.storage import Storage  # noqa: E402

# A solver result set: extension-less animation frames and time history next
# to a plain-text listing and an input deck no converter knows.
RESULT_KEYS = (
    "runs/case-1/MODELA001",
    "runs/case-1/MODELT01",
    "runs/case-1/MODEL_0001.out",
    "runs/case-1/MODEL_0000.rad",
    "runs/case-1/results.zip",
)


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


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        yield client


@pytest.fixture
def presigning_client(app_client: TestClient, monkeypatch):
    """LocalStore can't presign; pretend it can so upload-url's gates run."""

    async def _fake_presign(self, scope, key, expires_in_seconds=3600):
        return f"https://object-store.invalid/{key}"

    monkeypatch.setattr(Storage, "supports_presigned_uploads", property(lambda self: True))
    monkeypatch.setattr(Storage, "presigned_put_url", _fake_presign)
    return app_client


def _files(client: TestClient) -> dict[str, dict]:
    r = client.get("/api/scopes/shared/files")
    assert r.status_code == 200, r.text
    return {f["key"]: f for f in r.json()["files"]}


def test_put_stores_any_file_type(app_client: TestClient):
    for key in RESULT_KEYS:
        r = app_client.put(f"/api/scopes/shared/blobs/{key}", content=b"payload " + key.encode())
        assert r.status_code == 201, f"{key}: {r.status_code} {r.text}"
    files = _files(app_client)
    for key in RESULT_KEYS:
        assert key in files
        # listed, but nothing to convert it to
        assert not files[key].get("available_targets"), files[key]


def test_unknown_type_is_served_as_an_opaque_download(app_client: TestClient):
    key = "runs/case-1/report.html"
    assert app_client.put(f"/api/scopes/shared/blobs/{key}", content=b"<script>x</script>").status_code == 201
    for headers in ({}, {"Range": "bytes=0-7"}):
        r = app_client.get(f"/api/scopes/shared/blobs/{key}", headers=headers)
        assert r.status_code in (200, 206), r.text
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_presign_and_complete_accept_any_file_type(presigning_client: TestClient):
    key = RESULT_KEYS[0]
    r = presigning_client.post("/api/scopes/shared/upload-url", json={"key": key})
    assert r.status_code == 200, r.text
    assert r.json()["key"] == key

    assert presigning_client.put(f"/api/scopes/shared/blobs/{key}", content=b"frame").status_code == 201
    r = presigning_client.post("/api/scopes/shared/upload-complete", json={"key": key})
    assert r.status_code == 201, r.text
    assert r.json()["size"] == len(b"frame")


def test_derived_is_still_refused(presigning_client: TestClient):
    key = "_derived/runs/case-1/MODELA001.glb"
    assert presigning_client.put(f"/api/scopes/shared/blobs/{key}", content=b"x").status_code == 403
    assert presigning_client.post("/api/scopes/shared/upload-url", json={"key": key}).status_code == 403
    assert presigning_client.post("/api/scopes/shared/upload-complete", json={"key": key}).status_code == 403


def test_converting_an_unknown_type_is_still_refused(app_client: TestClient):
    key = RESULT_KEYS[0]
    assert app_client.put(f"/api/scopes/shared/blobs/{key}", content=b"frame").status_code == 201
    r = app_client.post("/api/scopes/shared/convert", json={"source_key": key, "target_format": "glb"})
    assert r.status_code == 415, r.text
