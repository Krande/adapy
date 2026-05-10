"""Integration tests for the admin batch-move endpoint.

Verifies the rename-with-derived-siblings flow end-to-end against
the in-process API + LocalStore. Same env shim as
test_fea_manifest.py.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault(
    "ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-")
)

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
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
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def _put(client: TestClient, key: str, data: bytes) -> None:
    """Direct write into the shared scope, bypassing the public PUT
    so we can stage arbitrary derived-blob layouts in the test
    storage without going through the upload validator."""
    # The blob_put endpoint rejects writes to _derived/, so for tests
    # we reach into Storage directly.
    import asyncio

    from ada.comms.rest.scope import Scope

    storage = client.app.state.__dict__.get("_test_storage")
    if storage is None:
        # Pull the storage instance the API was built with.
        for route in client.app.routes:
            ep = getattr(route, "endpoint", None)
            if ep is None:
                continue
            cell = getattr(ep, "__closure__", None)
            if not cell:
                continue
            for c in cell:
                v = c.cell_contents
                if v.__class__.__name__ == "Storage":
                    storage = v
                    break
            if storage is not None:
                break
        client.app.state._test_storage = storage

    asyncio.get_event_loop().run_until_complete(
        storage.put_bytes(Scope.shared(), key, data)
    )


def test_move_to_folder_renames_source_and_derived_siblings(
    app_client: TestClient,
):
    """Source and every derived sibling under _derived/<src>.* must
    follow the rename so the convert / bake cache stays warm."""

    src = "models/wall.rmed"
    _put(app_client, src, b"rmed-bytes")
    _put(app_client, "_derived/models/wall.rmed.glb", b"glb-bytes")
    _put(
        app_client,
        "_derived/models/wall.rmed.fea/fea.manifest.json",
        b'{"version":1}',
    )
    _put(
        app_client,
        "_derived/models/wall.rmed.fea/fea.DEPL.bin",
        b"\x00" * 16,
    )

    r = app_client.post(
        "/api/admin/scopes/shared/keys/move-to-folder",
        json={"keys": [src], "folder": "fea-examples"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed"] == []
    assert len(body["moved"]) == 1
    move = body["moved"][0]
    assert move["old"] == "models/wall.rmed"
    assert move["new"] == "fea-examples/wall.rmed"
    assert move["siblings_moved"] == 3
    assert move["siblings_failed"] == []

    # Old keys are gone; new keys exist.
    files = app_client.get("/api/scopes/shared/files").json()["files"]
    keys = {f["key"] for f in files}
    assert "models/wall.rmed" not in keys
    assert "fea-examples/wall.rmed" in keys
    # Derived siblings only show through the admin-scoped endpoint
    # because /api/scopes/<>/files filters _derived. Hit the admin
    # endpoint to verify they followed.
    admin_files = app_client.get("/api/admin/scopes/shared/files").json()["files"]
    sources = {f["key"]: f for f in admin_files}
    new_entry = sources.get("fea-examples/wall.rmed")
    assert new_entry is not None
    derived_keys = {d["key"] for d in new_entry["derived"]}
    assert "_derived/fea-examples/wall.rmed.glb" in derived_keys


def test_move_to_folder_skips_target_collisions(app_client: TestClient):
    """If the destination key already exists, the source stays put
    and gets reported as failed — clobbering would lose data."""

    _put(app_client, "models/a.rmed", b"first")
    _put(app_client, "fea-examples/a.rmed", b"already-there")

    r = app_client.post(
        "/api/admin/scopes/shared/keys/move-to-folder",
        json={"keys": ["models/a.rmed"], "folder": "fea-examples"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moved"] == []
    assert len(body["failed"]) == 1
    assert "already exists" in body["failed"][0]["reason"]

    # Both keys still present.
    files = app_client.get("/api/scopes/shared/files").json()["files"]
    keys = {f["key"] for f in files}
    assert "models/a.rmed" in keys
    assert "fea-examples/a.rmed" in keys


def test_move_to_folder_rejects_derived_key_in_input(app_client: TestClient):
    """Refuse to move a derived blob directly — that path should go
    through bake regeneration, not a sideways rename."""

    _put(app_client, "_derived/x.glb", b"derived")

    r = app_client.post(
        "/api/admin/scopes/shared/keys/move-to-folder",
        json={"keys": ["_derived/x.glb"], "folder": "trash"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moved"] == []
    assert "cannot move derived" in body["failed"][0]["reason"]


def test_move_to_folder_validates_input(app_client: TestClient):
    # Empty keys list.
    r = app_client.post(
        "/api/admin/scopes/shared/keys/move-to-folder",
        json={"keys": [], "folder": "x"},
    )
    assert r.status_code == 400, r.text

    # Missing folder.
    r = app_client.post(
        "/api/admin/scopes/shared/keys/move-to-folder",
        json={"keys": ["a"], "folder": ""},
    )
    assert r.status_code == 400, r.text

    # Folder is just slashes.
    r = app_client.post(
        "/api/admin/scopes/shared/keys/move-to-folder",
        json={"keys": ["a"], "folder": "///"},
    )
    assert r.status_code == 400, r.text


def test_move_to_folder_does_not_crosstalk_prefixed_siblings(
    app_client: TestClient,
):
    """Two sources whose names share a string prefix must not have
    their derived blobs crossed when one is moved.

    `wall.rmed` and `wall.rmed.bak` both produce derived keys under
    `_derived/wall.rmed*` — the discriminator is the dot after the
    source key, which a naive prefix match would miss.
    """

    _put(app_client, "wall.rmed", b"a")
    _put(app_client, "wall.rmed.bak", b"b")
    _put(app_client, "_derived/wall.rmed.glb", b"derived-of-a")
    _put(app_client, "_derived/wall.rmed.bak.glb", b"derived-of-bak")

    r = app_client.post(
        "/api/admin/scopes/shared/keys/move-to-folder",
        json={"keys": ["wall.rmed"], "folder": "moved"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed"] == []

    admin_files = app_client.get("/api/admin/scopes/shared/files").json()["files"]
    by_key = {f["key"]: f for f in admin_files}

    # `wall.rmed` and its derived moved.
    assert "moved/wall.rmed" in by_key
    moved_derived = {d["key"] for d in by_key["moved/wall.rmed"]["derived"]}
    assert "_derived/moved/wall.rmed.glb" in moved_derived

    # `wall.rmed.bak` and ITS derived stayed put — no crosstalk.
    assert "wall.rmed.bak" in by_key
    bak_derived = {d["key"] for d in by_key["wall.rmed.bak"]["derived"]}
    assert "_derived/wall.rmed.bak.glb" in bak_derived
