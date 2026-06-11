"""Integration tests for user-level (personal-scope) file management.

Regular users can delete / rename / move files in their own personal
scope only; shared and project scopes stay admin-managed, and CI
``versions/`` blobs plus the ``_derived/`` bake cache are protected
even inside the personal scope. Same env shim + LocalStore staging as
test_admin_move_to_folder.py; non-admin principal via the
User.local_dev monkeypatch from test_admin.py.
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

from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.scope import Scope  # noqa: E402

USER_SUB = "demo-user"


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
def app_client(tmp_path: pathlib.Path, monkeypatch):
    """API client whose principal is a NON-admin user with a fixed sub."""
    monkeypatch.setattr(
        auth_module.User,
        "local_dev",
        classmethod(
            lambda cls: cls(
                sub=USER_SUB,
                email="demo@x.invalid",
                display_name="Demo",
                groups=frozenset(),
                is_admin=False,
            )
        ),
    )
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def _storage(client: TestClient):
    storage = client.app.state.__dict__.get("_test_storage")
    if storage is None:
        for route in client.app.routes:
            ep = getattr(route, "endpoint", None)
            if ep is None:
                continue
            cells = getattr(ep, "__closure__", None)
            if not cells:
                continue
            for c in cells:
                v = c.cell_contents
                if v.__class__.__name__ == "Storage":
                    storage = v
                    break
            if storage is not None:
                break
        client.app.state._test_storage = storage
    return storage


def _put(client: TestClient, key: str, data: bytes, scope: Scope | None = None) -> None:
    """Stage a blob directly in storage (bypasses upload validation so
    we can lay out _derived/ and versions/ fixtures)."""
    scope = scope or Scope.user(USER_SUB)
    asyncio.run(_storage(client).put_bytes(scope, key, data))


def _keys(client: TestClient, scope: Scope | None = None) -> set[str]:
    scope = scope or Scope.user(USER_SUB)
    files = asyncio.run(_storage(client).list(scope))
    return {f.key for f in files}


def test_user_delete_cascades_derived(app_client: TestClient):
    """Deleting an own personal-scope source reaps its derived blobs."""
    _put(app_client, "models/wall.rmed", b"rmed")
    _put(app_client, "_derived/models/wall.rmed.glb", b"glb")
    _put(app_client, "_derived/models/wall.rmed.fea/fea.manifest.json", b"{}")
    _put(app_client, "_derived/models/wall.rmed.fea/fea.DEPL.bin", b"\x00")
    _put(app_client, "models/other.ifc", b"keep-me")

    r = app_client.delete("/api/scopes/user:me/blobs/models/wall.rmed")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "models/wall.rmed" in body["deleted"]
    assert body["errors"] == []

    keys = _keys(app_client)
    assert "models/wall.rmed" not in keys
    assert not any(k.startswith("_derived/models/wall.rmed") for k in keys)
    assert "models/other.ifc" in keys


def test_user_move_to_folder_cascades_and_reports_collisions(app_client: TestClient):
    _put(app_client, "a.rmed", b"a")
    _put(app_client, "_derived/a.rmed.glb", b"derived-a")
    _put(app_client, "b.ifc", b"b")
    _put(app_client, "dest/b.ifc", b"already-there")

    r = app_client.post(
        "/api/scopes/user:me/keys/move-to-folder",
        json={"keys": ["a.rmed", "b.ifc"], "folder": "dest"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["moved"]) == 1
    assert body["moved"][0]["new"] == "dest/a.rmed"
    assert body["moved"][0]["siblings_moved"] == 1
    assert len(body["failed"]) == 1
    assert "already exists" in body["failed"][0]["reason"]

    keys = _keys(app_client)
    assert "dest/a.rmed" in keys
    assert "_derived/dest/a.rmed.glb" in keys
    assert "b.ifc" in keys  # collision left in place


def test_user_rename_cascades_derived(app_client: TestClient):
    _put(app_client, "old.ifc", b"x")
    _put(app_client, "_derived/old.ifc.glb", b"derived")

    r = app_client.post(
        "/api/scopes/user:me/keys/rename",
        json={"old_key": "old.ifc", "new_key": "new-name.ifc"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["old"] == "old.ifc"
    assert body["new"] == "new-name.ifc"
    assert body["siblings_moved"] == 1

    keys = _keys(app_client)
    assert "old.ifc" not in keys
    assert "new-name.ifc" in keys
    assert "_derived/new-name.ifc.glb" in keys


def test_user_rename_conflict_and_missing(app_client: TestClient):
    _put(app_client, "a.ifc", b"a")
    _put(app_client, "b.ifc", b"b")

    r = app_client.post(
        "/api/scopes/user:me/keys/rename",
        json={"old_key": "a.ifc", "new_key": "b.ifc"},
    )
    assert r.status_code == 409, r.text

    r = app_client.post(
        "/api/scopes/user:me/keys/rename",
        json={"old_key": "ghost.ifc", "new_key": "real.ifc"},
    )
    assert r.status_code == 404, r.text

    r = app_client.post(
        "/api/scopes/user:me/keys/rename",
        json={"old_key": "a.ifc", "new_key": "a.ifc"},
    )
    assert r.status_code == 400, r.text


def test_user_mutations_personal_scope_only(app_client: TestClient):
    """Shared scope (and any non-personal scope) → 403 even though the
    user could read/upload there."""
    _put(app_client, "shared.ifc", b"s", scope=Scope.shared())

    r = app_client.delete("/api/scopes/shared/blobs/shared.ifc")
    assert r.status_code == 403, r.text

    r = app_client.post(
        "/api/scopes/shared/keys/move-to-folder",
        json={"keys": ["shared.ifc"], "folder": "x"},
    )
    assert r.status_code == 403, r.text

    r = app_client.post(
        "/api/scopes/shared/keys/rename",
        json={"old_key": "shared.ifc", "new_key": "y.ifc"},
    )
    assert r.status_code == 403, r.text

    # Untouched.
    assert "shared.ifc" in _keys(app_client, Scope.shared())


def test_user_cannot_reach_other_users_scope(app_client: TestClient):
    """Explicit user:<other-sub> is rejected outright."""
    _put(app_client, "theirs.ifc", b"t", scope=Scope.user("someone-else"))

    r = app_client.delete("/api/scopes/user:someone-else/blobs/theirs.ifc")
    assert r.status_code in (400, 403), r.text
    assert "theirs.ifc" in _keys(app_client, Scope.user("someone-else"))


def test_user_protected_keys_rejected(app_client: TestClient):
    """versions/ and _derived/ are admin-managed even in personal scope."""
    _put(app_client, "versions/main/abc123/model.glb", b"ci")
    _put(app_client, "_derived/some.ifc.glb", b"derived")
    _put(app_client, "mine.ifc", b"m")

    r = app_client.delete("/api/scopes/user:me/blobs/versions/main/abc123/model.glb")
    assert r.status_code == 400, r.text

    r = app_client.delete("/api/scopes/user:me/blobs/_derived/some.ifc.glb")
    assert r.status_code == 400, r.text

    r = app_client.post(
        "/api/scopes/user:me/keys/move-to-folder",
        json={"keys": ["versions/main/abc123/model.glb"], "folder": "x"},
    )
    assert r.status_code == 400, r.text

    r = app_client.post(
        "/api/scopes/user:me/keys/move-to-folder",
        json={"keys": ["mine.ifc"], "folder": "versions/main"},
    )
    assert r.status_code == 400, r.text

    r = app_client.post(
        "/api/scopes/user:me/keys/rename",
        json={"old_key": "mine.ifc", "new_key": "_derived/mine.ifc"},
    )
    assert r.status_code == 400, r.text

    keys = _keys(app_client)
    assert "versions/main/abc123/model.glb" in keys
    assert "_derived/some.ifc.glb" in keys
    assert "mine.ifc" in keys


def test_admin_rename_endpoint_exists_for_admins(tmp_path: pathlib.Path):
    """The admin rename variant works in any scope (default local_dev
    principal is admin)."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _put(client, "x.ifc", b"x", scope=Scope.shared())
        _put(client, "_derived/x.ifc.glb", b"d", scope=Scope.shared())
        r = client.post(
            "/api/admin/scopes/shared/keys/rename",
            json={"old_key": "x.ifc", "new_key": "renamed.ifc"},
        )
        assert r.status_code == 200, r.text
        keys = _keys(client, Scope.shared())
        assert "renamed.ifc" in keys
        assert "_derived/renamed.ifc.glb" in keys
