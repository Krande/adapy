"""Admin cancellation of a job nothing will ever finish.

The job this exists for is queued against a capability no live worker serves —
a retired pool, a renamed capability, a worker that never came back. Nothing
pulls it, so it never reaches a terminal status, so ``purge_completed_jobs``
(terminal entries only) never sweeps it: it sits in the KV bucket, is replayed
by every registry scan, and reads in the admin panel as work still pending.

Exercised against a STUBBED pool and queue rather than live Postgres and NATS.
Everything this route decides is above both — who may cancel, which of the two
halves it cleared, and when there was nothing to clear — and gating it behind
``ADA_TEST_POSTGRES_URL`` would mean it never ran where this suite normally
runs.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-admin-cancel-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

CANCEL = "/api/admin/jobs/{}/cancel"


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


class _FakeQueue:
    """Just the one method the route calls, plus a record of the calls."""

    def __init__(self, present: bool = True, raises: bool = False):
        self.present, self.raises, self.purged = present, raises, []

    async def purge_job(self, job_id: str) -> bool:
        self.purged.append(job_id)
        if self.raises:
            raise RuntimeError("nats is unhappy")
        return self.present


@pytest.fixture
def admin_client(tmp_path: pathlib.Path, monkeypatch):
    """Yields ``(client, state)``.

    ``state["cancelled"]`` is what the stubbed repository will report, and
    ``state["calls"]`` records what the route asked it to cancel.
    """
    from ada.comms.rest import db as db_mod

    state: dict = {"cancelled": True, "calls": []}

    async def fake_admin_cancel(pool, *, job_id, reason="x"):
        state["calls"].append({"job_id": job_id, "reason": reason})
        return state["cancelled"]

    monkeypatch.setattr(db_mod, "admin_cancel_audit_by_job", fake_admin_cancel)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.db_pool = object()
        client.app.state.queue = _FakeQueue()
        yield client, state


def test_an_admin_may_cancel_a_job_they_do_not_own(admin_client):
    """The whole point. The user-facing cancel filters on ``audit_log.user_sub``,
    so an operator cleaning up after a retired pool — who by definition did not
    start those jobs — could not clear them at all."""
    client, state = admin_client
    r = client.post(CANCEL.format("job-123"))
    assert r.status_code == 200
    assert r.json() == {"job_id": "job-123", "cancelled": True, "purged": True}

    (call,) = state["calls"]
    assert call["job_id"] == "job-123"
    # Who did it, in the row itself: an audit trail whose cancellations are
    # anonymous is one that cannot answer the question it is kept for.
    assert "local-dev" in call["reason"]


def test_the_queue_entry_is_dropped_not_merely_updated(admin_client):
    """An update leaves the entry to a grace-timer sweep that only touches
    TERMINAL rows — which is exactly how these accumulated. Purge is what makes
    the entry stop being replayed by every registry scan."""
    client, _ = admin_client
    client.post(CANCEL.format("job-123"))
    assert client.app.state.queue.purged == ["job-123"]


def test_both_halves_are_reported_independently(admin_client):
    """A job can be stuck in the audit row, in the queue entry, or in both.
    Collapsing that into one flag would leave an operator unable to tell
    whether the pending entry they were looking at actually went away."""
    client, state = admin_client
    state["cancelled"] = False           # row already terminal or missing …
    client.app.state.queue = _FakeQueue(present=True)  # … but the KV entry lingers

    r = client.post(CANCEL.format("job-123"))
    assert r.status_code == 200
    assert r.json() == {"job_id": "job-123", "cancelled": False, "purged": True}


def test_nothing_anywhere_is_a_404(admin_client):
    client, state = admin_client
    state["cancelled"] = False
    client.app.state.queue = _FakeQueue(present=False)

    r = client.post(CANCEL.format("nope"))
    assert r.status_code == 404
    assert "no such job" in r.json()["detail"]


def test_a_queue_that_errors_does_not_sink_the_cancellation(admin_client):
    """The audit row is the source of truth for user-visible state. Losing the
    KV purge to a NATS problem must not also lose the cancellation — the
    operator would retry, the row would already be terminal, and the answer
    would flip to 404."""
    client, _ = admin_client
    client.app.state.queue = _FakeQueue(raises=True)

    r = client.post(CANCEL.format("job-123"))
    assert r.status_code == 200
    assert r.json()["cancelled"] is True
    assert r.json()["purged"] is False


def test_a_deployment_without_a_pool_can_still_clear_the_queue(admin_client):
    """No Postgres is a supported way to run this, and a stuck KV entry is if
    anything more likely there — nothing is writing audit rows to notice it."""
    client, _ = admin_client
    client.app.state.db_pool = None

    r = client.post(CANCEL.format("job-123"))
    assert r.status_code == 200
    assert r.json() == {"job_id": "job-123", "cancelled": False, "purged": True}


# --- the worker's pre-run check ---------------------------------------------


@pytest.mark.asyncio
async def test_a_worker_drops_a_job_cancelled_before_it_started(monkeypatch):
    """Every other cancel check in the worker is MID-RUN, which assumes the job
    is running. A job cancelled precisely because nothing was going to serve it
    is the case where the worker that eventually appears must not start it."""
    from ada.comms.rest import worker as wk

    async def cancelled(pool, job_id):
        return True

    monkeypatch.setattr(wk.db_module, "audit_is_cancelled", cancelled)
    assert await wk._should_skip_cancelled(object(), "job-123") is True


@pytest.mark.asyncio
async def test_the_pre_run_check_fails_towards_running_the_job(monkeypatch):
    """The two failure directions are not symmetric. A false negative costs one
    job run that the mid-run checks then stop; refusing to run on a failed
    query would let a database hiccup silently drain the queue."""
    from ada.comms.rest import worker as wk

    async def boom(pool, job_id):
        raise RuntimeError("pool is gone")

    monkeypatch.setattr(wk.db_module, "audit_is_cancelled", boom)
    assert await wk._should_skip_cancelled(object(), "job-123") is False
    # And with no pool at all there is no row to consult.
    assert await wk._should_skip_cancelled(None, "job-123") is False
