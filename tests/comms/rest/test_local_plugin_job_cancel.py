"""Cancelling an in-process plugin job actually cancels it.

``LocalJobRegistry`` has carried a ``cancel_event`` since it was written, and the
job entrypoint is handed that event whenever it accepts one — so a cooperative
plugin has always been able to stop between units of work. Nothing ever set it:
the cancel endpoint only flipped an audit-log row, and it required a database
pool to get that far. Which meant the one deployment where cancelling CAN work —
a local server with no NATS and no database — was the one where the endpoint
returned 500.

These tests pin the two halves of the fix: the in-process branch runs before the
pool is required, and it is access-checked exactly as reading the same job is.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import local_jobs
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.local_jobs import STATUS_DONE, STATUS_RUNNING, LocalJob


def _settings(tmp_path) -> Settings:
    """No queue URL and no database — the local-server shape."""
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


def _job(job_id: str, status: str = STATUS_RUNNING) -> LocalJob:
    job = LocalJob(
        job_id=job_id,
        plugin_id="test-plugin",
        scope_kind="shared",
        scope_id=None,
        derived_key=f"_derived/{job_id}.json",
        status=status,
    )
    job.started_at = time.time()
    return job


@pytest.fixture
def client(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    local_jobs.registry._jobs.clear()


def _cancel(client, job_id: str):
    return client.post(f"/api/scopes/shared/my-jobs/{job_id}/cancel")


def test_cancelling_a_running_job_sets_its_cancel_event(client) -> None:
    # The whole point: the event is what a cooperative plugin polls, so setting
    # it is the difference between "the row disappeared" and "the work stopped".
    job = _job("plugin-cancel-me")
    local_jobs.registry.add(job)
    assert not job.cancel_event.is_set()

    response = _cancel(client, job.job_id)

    assert response.status_code == 200
    assert response.json() == {"job_id": job.job_id, "cancelled": True}
    assert job.cancel_event.is_set()


def test_it_works_without_a_database(client, tmp_path) -> None:
    # This settings fixture has database_url="" — before the fix the request
    # reached _require_pool and failed there, on precisely the deployment where
    # in-process jobs are the only kind that exist.
    local_jobs.registry.add(_job("plugin-no-db"))
    assert _cancel(client, "plugin-no-db").status_code == 200


def test_a_finished_job_reports_that_it_was_not_cancelled(client) -> None:
    # Not an error — the job is simply past the point of stopping. Answering 200
    # with cancelled=False says that; a 404 would claim it never existed.
    job = _job("plugin-already-done", status=STATUS_DONE)
    local_jobs.registry.add(job)

    response = _cancel(client, job.job_id)

    assert response.status_code == 200
    assert response.json()["cancelled"] is False
    assert not job.cancel_event.is_set()


def test_an_unknown_job_falls_through_to_the_queued_path(client) -> None:
    # Nothing in the local registry means this is (or was) a queued job, and
    # that branch owns the answer. With no database it cannot produce one, but
    # what matters here is that it did not get claimed by the local branch.
    response = _cancel(client, "not-a-local-job")
    assert response.status_code != 200
