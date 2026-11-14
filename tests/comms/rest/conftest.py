"""Shared fixtures for the REST tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _a_fresh_local_job_queue(monkeypatch):
    """Each test gets its own local job queue (``local_queue``), roomy enough not to queue.

    The queue is module-level, like the job registry, so a job an earlier test left running (a
    plugin job that is meant never to finish, a cancelled sleeper) would otherwise hold a slot into
    every later test -- on a two-core runner the default cap is one, and the whole suite would wait
    behind it. Tests of the queue itself configure the limits they mean.
    """
    try:
        from ada.comms.rest import local_queue
    except ImportError:  # a test env without the REST stack
        yield
        return
    monkeypatch.setattr(local_queue, "queue", local_queue.LocalJobQueue(local_queue.limits_from(64)))
    yield
