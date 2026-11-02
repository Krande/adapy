"""Extending a running job's time limit: the audit row, the worker's ``job_limits`` and a run's wait."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from ada.comms.rest import db as dbm
from ada.comms.rest.formats.plugin import JobLimits
from ada.comms.rest.run_driver import wait_for_job

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")


def _jobs(states: list[str]):
    """A queue whose job answers ``states`` in turn, then the last one for ever."""
    seen = iter(states)
    last = {"s": states[0]}

    async def status(job_id):
        last["s"] = next(seen, last["s"])
        return SimpleNamespace(status=last["s"], payload={})

    async def cancel(job_id):
        return True

    return SimpleNamespace(jobs=SimpleNamespace(status=status, cancel=cancel))


async def _running():
    return "running"


@pytest.mark.asyncio
async def test_a_run_waits_as_much_longer_as_the_job_was_extended(monkeypatch):
    clock = iter(range(0, 10**6, 10))
    monkeypatch.setattr("ada.comms.rest.run_driver.time.monotonic", lambda: float(next(clock)))
    ctx = _jobs(["running"] * 20 + ["done"])

    async def extended():
        return 3600.0

    await wait_for_job(ctx, "j", timeout_s=60, beat=_running, poll_s=0, extension=extended)


@pytest.mark.asyncio
async def test_without_an_extension_the_wait_ends_at_its_limit(monkeypatch):
    clock = iter(range(0, 10**6, 10))
    monkeypatch.setattr("ada.comms.rest.run_driver.time.monotonic", lambda: float(next(clock)))
    ctx = _jobs(["running"] * 20 + ["done"])

    async def none():
        return 0.0

    with pytest.raises(RuntimeError, match="after 1 min"):
        await wait_for_job(ctx, "j", timeout_s=60, beat=_running, poll_s=0, extension=none)


def test_job_limits_start_unextended():
    assert JobLimits().extra_seconds == 0


@needs_postgres
@pytest.mark.asyncio
async def test_extending_a_job_against_postgres():
    pool = await dbm.init_pool(POSTGRES_URL)
    job_id = f"test-{uuid.uuid4().hex[:12]}"
    try:
        await dbm.insert_audit(
            pool,
            user_sub="owner",
            scope_kind="shared",
            scope_id=None,
            action="plugin_job",
            job_id=job_id,
            status="running",
        )
        assert await dbm.audit_extra_seconds(pool, job_id) == 0
        # Someone else's job is not theirs to extend; the owner's and an admin's (None) are.
        assert await dbm.extend_audit_by_job(pool, job_id=job_id, seconds=600, user_sub="other") is None
        assert await dbm.extend_audit_by_job(pool, job_id=job_id, seconds=1800, user_sub="owner") == 1800
        assert await dbm.extend_audit_by_job(pool, job_id=job_id, seconds=1800, user_sub=None) == 3600
        assert await dbm.audit_extra_seconds(pool, job_id) == 3600
        # Bounded in total.
        for _ in range(30):
            total = await dbm.extend_audit_by_job(pool, job_id=job_id, seconds=6 * 3600, user_sub=None)
        assert total == dbm.audit_log.MAX_EXTRA_SECONDS
        # A finished job is not extended.
        await pool.execute("UPDATE audit_log SET status = 'done' WHERE job_id = $1", job_id)
        assert await dbm.extend_audit_by_job(pool, job_id=job_id, seconds=60, user_sub=None) is None
    finally:
        await pool.execute("DELETE FROM audit_log WHERE job_id = $1", job_id)
        await dbm.close_pool(pool)
