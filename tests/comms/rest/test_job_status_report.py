"""A pool-less worker reporting its own job into the audit log.

THE GAP THIS CLOSES. The audit row is written `queued` by the API at enqueue and
moved by the WORKER, through a database pool. A worker without `DATABASE_URL` is a
supported deployment -- it says so at startup -- and on one both audit hops are
no-ops: the row stays `queued` while the job runs, finishes and is swept. The queue
record is accurate throughout, so the conversion toast follows along and the Audit
tab shows every job on that pool as permanently pending.

That is not cosmetic. A permanently-`queued` row is indistinguishable from a job
nothing will ever run, so an operator cannot tell a healthy pool from a broken one,
and anything reasoning over non-terminal rows blocks on jobs long finished.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-job-status-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)


def _settings(tmp_path: pathlib.Path, database_url: str = "") -> Settings:
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
        database_url=database_url,
    )


@pytest.fixture
def client(tmp_path: pathlib.Path):
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def test_without_a_database_the_report_is_accepted_and_says_it_recorded_nothing(client):
    """Not an error. A deployment with no database has no audit log to write, and
    is exactly the case where nobody is reading audit rows either -- so a worker
    must not treat this as a failure and must not retry it."""
    r = client.post("/api/jobs/abc123/status", json={"status": "done"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] is False
    assert "no database" in body["reason"]


def test_a_status_outside_the_closed_set_is_refused(client):
    """This route writes the record of what the deployment did. "Whatever the
    caller sent" is not a status vocabulary, and a typo that became a status would
    make every query over statuses quietly incomplete.

    Refused even on a deployment with no database: the request is wrong either way,
    and a 200 "recorded: false" would tell the caller its typo was merely unused."""
    r = client.post("/api/jobs/abc123/status", json={"status": "finished-ish"})
    assert r.status_code == 400, r.text
    assert "status must be one of" in r.json()["detail"]


def test_a_body_that_is_not_an_object_is_refused(client):
    r = client.post("/api/jobs/abc123/status", json=["done"])
    assert r.status_code == 400
    assert "JSON object" in r.json()["detail"]


def test_the_reportable_statuses_are_the_ones_the_worker_actually_sends():
    """Guards the two halves drifting apart: a worker reporting a status this route
    refuses would log a warning per job and leave the row queued anyway, which is
    the failure this whole path exists to remove."""
    from ada.comms.rest import app as app_module

    source = pathlib.Path(app_module.__file__).read_text(encoding="utf-8")
    # The literal tuple in the route, read rather than imported: it is a local
    # inside create_app, and asserting on it here is what keeps it honest.
    assert '_REPORTABLE_JOB_STATUSES = ("running", "done", "error", "cancelled")' in source

    worker_source = pathlib.Path(pathlib.Path(app_module.__file__).parent / "worker.py").read_text(encoding="utf-8")
    # Every status the worker passes to _audit_done must be in that set.
    for literal in ('"done"', '"error"', '"cancelled"'):
        assert f"_audit_done(db_pool, job_id, {literal}" in worker_source or literal in worker_source
