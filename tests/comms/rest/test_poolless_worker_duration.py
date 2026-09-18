"""A pool-less worker must report an elapsed time, not a clock reading.

THE BUG THIS PINS. ``_audit_done`` has two branches. The one that writes
through a database pool computed ``time.monotonic() - started_at``; the one
that reports over the API computed ``time.time() - started_at``. Both take the
SAME ``started_at``, and every caller passes ``time.monotonic()``
(worker/process.py, worker/loop.py) -- so the API branch was not measuring a
duration at all. It returned the wall clock: seconds since the epoch, less a
little uptime, times a thousand.

``audit_log.duration_ms`` is int32, so the API refused the write rather than
storing the nonsense:

    asyncpg.exceptions.DataError: invalid input for query argument $4:
    1789557360408 (value out of int32 range)

which reached the worker as a bare 500, three retries and "gave up reporting",
for a job that had finished correctly in 7.0 seconds. The row then stays
non-terminal for ever -- worse than a wrong number, because the plugin-job
concurrent-fire guard blocks on jobs that ended minutes ago.

WHY IT SURVIVED. Only the pool-less path was wrong, and a pool-less worker is
the deployment nobody watches an audit tab on. Every environment with a
database took the other branch and was correct.

The test is BEHAVIOURAL -- it calls the real function and reads the payload --
rather than a source check, because the defect was a plausible-looking line
that a reader had already accepted twice.
"""

from __future__ import annotations

import time

import pytest


@pytest.mark.asyncio
async def test_a_poolless_report_sends_elapsed_time_not_the_wall_clock(monkeypatch):
    from ada.comms.rest.worker import audit as audit_module

    sent: dict = {}

    async def fake_report(job_id, payload):
        sent["job_id"] = job_id
        sent["payload"] = payload

    monkeypatch.setattr(audit_module, "_report_job_status_over_api", fake_report)

    started_at = time.monotonic()
    await audit_module._audit_done(
        None,  # db_pool is None -> the API-reporting branch, the one that was wrong
        "job-1",
        "done",
        None,
        started_at,
    )

    duration = sent["payload"]["duration_ms"]

    # The hard floor: int32. This is what the database enforced and what the
    # 500 was. Anything at or above it cannot be stored at all.
    assert duration < 2**31, (
        f"duration_ms={duration} exceeds int32, so the API cannot store it. "
        "That is the wall clock, not an elapsed time -- check which clock "
        "`started_at` is measured on."
    )
    # And the real bound: this job took no time at all.
    assert 0 <= duration < 60_000, f"expected a near-zero elapsed time, got {duration} ms"


@pytest.mark.asyncio
async def test_both_branches_measure_against_the_same_clock(monkeypatch):
    """The two branches of one function must agree about what `started_at` is.

    They disagreed for real, which is why this is asserted rather than assumed.
    Driving both with one `started_at` and comparing the numbers is the check
    that the next person cannot silently break by editing one branch.
    """
    from ada.comms.rest.worker import audit as audit_module

    sent: dict = {}

    async def fake_report(job_id, payload):
        sent["payload"] = payload

    captured: dict = {}

    async def fake_update(pool, **kwargs):
        captured.update(kwargs)

    class _Pool:  # a stand-in; nothing here touches it beyond `is None`
        pass

    monkeypatch.setattr(audit_module, "_report_job_status_over_api", fake_report)
    monkeypatch.setattr(audit_module.db_module, "update_audit_by_job", fake_update)
    monkeypatch.setattr(audit_module.state, "_WORKER_STORAGE", None, raising=False)

    started_at = time.monotonic()
    await audit_module._audit_done(None, "job-1", "done", None, started_at)
    await audit_module._audit_done(_Pool(), "job-1", "done", None, started_at)

    api_ms = sent["payload"]["duration_ms"]
    pool_ms = captured["duration_ms"]

    # Same instant, same start: the two must be within a second of each other.
    # They differed by ~1.79e12 before the fix.
    assert abs(api_ms - pool_ms) < 1000, (
        f"the two branches disagree: API path says {api_ms} ms, pool path says "
        f"{pool_ms} ms, from one `started_at`. They are using different clocks."
    )
