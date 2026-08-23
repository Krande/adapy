"""GET /api/settings/{key} — the publicly-readable settings namespace.

Writes stay admin-only; this is a read window so a plugin can publish one piece
of deployment configuration its UI needs for EVERY user, not just admins. The
whole contract is the key prefix, so these tests pin it: inside the namespace
behaves like the admin getter, outside it is 403 for admin and non-admin alike.

Same shape as test_admin.py — create_app with auth disabled, and the non-admin
case produced by monkeypatching User.local_dev.
"""

from __future__ import annotations

import os
import tempfile

# Importing ada.comms.rest.app evaluates a module-level `create_app()` which
# materializes a local Storage. Point it at a temp dir so the import succeeds in
# environments without `./viewer-data`.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import auth as auth_module
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings


def _settings(tmp_path) -> Settings:
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


def _non_admin(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_module.User,
        "local_dev",
        classmethod(
            lambda cls: cls(
                sub="non-admin",
                email="x@y",
                display_name="X",
                groups=frozenset(),
                is_admin=False,
            )
        ),
    )


@pytest.mark.parametrize(
    "key",
    [
        "audit.perf.thresholds.slow",
        "profile_conversions",
        "notpublic.thing",
        # A key that merely CONTAINS the prefix must not pass — the check is a
        # prefix, not a substring.
        "sneaky.public.thing",
    ],
)
def test_non_public_keys_are_refused(tmp_path, key):
    """403 even for an admin: this route is not an alternate admin getter."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        app.state.db_pool = object()  # past the 503 gate; prefix check comes first
        r = client.get(f"/api/settings/{key}")
        assert r.status_code == 403, r.text


def test_non_public_key_refused_for_non_admin_too(monkeypatch, tmp_path):
    _non_admin(monkeypatch)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        app.state.db_pool = object()
        assert client.get("/api/settings/profile_conversions").status_code == 403


def test_public_key_reaches_the_db_gate(tmp_path):
    """A key inside the namespace passes the prefix check and falls through to
    the pool requirement — 503, not 403. That ordering is the point: the prefix
    decides access, the DB decides availability."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/api/settings/public.some_plugin.binding")
        assert r.status_code == 503, r.text


def test_public_key_is_readable_by_a_non_admin(monkeypatch, tmp_path):
    """The whole reason this endpoint exists — a non-admin must get past the
    gate that /api/admin/settings would have refused them at."""
    _non_admin(monkeypatch)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/api/settings/public.some_plugin.binding")
        assert r.status_code == 503, r.text
        # ...whereas the admin getter refuses this user outright.
        assert client.get("/api/admin/settings/public.some_plugin.binding").status_code == 403


def test_there_is_no_public_setter(monkeypatch, tmp_path):
    """Read window only. A POST to the public path must not exist — otherwise
    any authenticated user could write deployment configuration."""
    _non_admin(monkeypatch)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        app.state.db_pool = object()
        r = client.post("/api/settings/public.some_plugin.binding", json={"value": "x"})
        assert r.status_code == 405, r.text
