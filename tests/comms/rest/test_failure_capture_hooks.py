"""Every path that records a terminal failure must capture its input.

Four write rows, and they are genuinely separate: ``_audit`` inserts for
in-process work, the worker patches by job id, in-browser conversions patch
through ``audit/local``, and browser load/render failures insert through
``audit/view``. The last two call the db layer directly rather than going
through either of the first two, so a hook that covers only those would
silently exempt the whole browser side.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import db as dbm  # noqa: E402
from ada.comms.rest import failure_capture  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

JOB_ID = "wasm-abc123"
DERIVED = "_derived/m.ifc.glb"
PRESERVED = "cafebabe.glb"


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
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Client with the db layer stubbed and capture spied."""
    captured: list[dict] = []
    written: list[dict] = []

    async def fake_owner(pool, job_id):
        from ada.comms.rest.auth import User

        return {"user_sub": User.local_dev().sub}

    async def fake_update(pool, **kw):
        written.append(kw)

    async def fake_insert(pool, **kw):
        written.append(kw)

    async def fake_capture(storage, pool, db_module, *, scope, key, action=None):
        captured.append({"key": key, "action": action})
        return PRESERVED

    async def fake_capture_for_job(storage, pool, db_module, job_id):
        captured.append({"job_id": job_id})
        return PRESERVED

    monkeypatch.setattr(dbm, "get_audit_owner_by_job", fake_owner)
    monkeypatch.setattr(dbm, "update_audit_by_job", fake_update)
    monkeypatch.setattr(dbm, "insert_audit", fake_insert)
    monkeypatch.setattr(failure_capture, "capture", fake_capture)
    monkeypatch.setattr(failure_capture, "capture_for_job", fake_capture_for_job)

    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    client.__enter__()
    app.state.db_pool = object()
    return client, captured, written


# ── in-browser conversions (audit/local) ──────────────────────────────────


def test_a_browser_conversion_failure_captures(harness):
    client, captured, written = harness
    r = client.post(f"/api/scopes/user:me/audit/local/{JOB_ID}", json={"status": "error"})
    assert r.status_code == 200, r.text
    assert captured == [{"job_id": JOB_ID}], "the WASM pipeline must not be exempt"
    assert written[0]["failure_key"] == PRESERVED


@pytest.mark.parametrize("status", ["done", "ok", "skipped", "cancelled"])
def test_a_browser_conversion_success_captures_nothing(harness, status):
    client, captured, written = harness
    assert client.post(f"/api/scopes/user:me/audit/local/{JOB_ID}", json={"status": status}).status_code == 200
    assert captured == []
    assert written[0]["failure_key"] is None


# ── browser load / render (audit/view) ────────────────────────────────────


@pytest.mark.parametrize("status", ["error", "failed"])
def test_a_failed_model_load_captures_the_blob_it_read(harness, status):
    client, captured, written = harness
    r = client.post("/api/scopes/user:me/audit/view", json={"key": DERIVED, "status": status})
    assert r.status_code == 201, r.text
    # Derived, and captured anyway: for a render the artifact IS the input.
    assert captured == [{"key": DERIVED, "action": "view"}]
    assert written[0]["failure_key"] == PRESERVED


def test_a_failed_render_window_is_captured_under_the_render_action(harness):
    client, captured, written = harness
    r = client.post(
        "/api/scopes/user:me/audit/view",
        json={"key": DERIVED, "status": "error", "client_metrics": {"kind": "render"}},
    )
    assert r.status_code == 201, r.text
    assert captured == [{"key": DERIVED, "action": "render"}]


def test_a_successful_view_captures_nothing(harness):
    client, captured, written = harness
    assert client.post("/api/scopes/user:me/audit/view", json={"key": DERIVED, "status": "ok"}).status_code == 201
    assert captured == []
    assert written[0]["failure_key"] is None
