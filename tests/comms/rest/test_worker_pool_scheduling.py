"""How the worker's round-robin cursor moves between capability pools.

WHY THIS MATTERS. The pools are polled one at a time with a BLOCKING fetch, so
a worker serving N capabilities walks N-1 empty pools between consecutive jobs
from the one pool that is busy. On a six-capability combined worker that is
``5 * max(0.5, 5.0/6) ~= 4.2s`` worst case and ~2.1s on average — per job.

Measured on a real 907-cell sweep: 20 minutes of dead time in a 69-minute run,
against 40 minutes for the same corpus on a single-pool image. Per-cell
durations were identical; the pool was simply idle 39% of the wall clock, and
both workers were pulling evenly the whole time. It reads as "parallelism
broke" and is nothing of the kind.

The fix is to stay on a pool that is producing. The cap is what keeps that
honest, so both halves are pinned here.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from ada.comms.rest.worker import (  # noqa: E402
    FETCH_TIMEOUT,
    _advance_pool_cursor,
    _per_fetch_timeout,
)

# ── the cost this exists to remove ─────────────────────────────────


def test_per_fetch_timeout_splits_the_cycle_across_pools():
    # A full cycle stays ~FETCH_TIMEOUT, so an IDLE worker's pickup latency does
    # not grow with the number of capabilities it serves.
    assert _per_fetch_timeout(1) == FETCH_TIMEOUT
    assert _per_fetch_timeout(5) == pytest.approx(1.0)
    # Floored, so many pools cannot degenerate into a busy-loop.
    assert _per_fetch_timeout(100) == 0.5


def test_walking_empty_pools_is_what_costs_a_busy_worker():
    """The arithmetic behind the regression, stated so it cannot be re-derived
    wrongly: six capabilities, five of them idle, one blocking fetch each."""
    n_pools = 6
    per_pool = _per_fetch_timeout(n_pools)
    worst_case_between_jobs = (n_pools - 1) * per_pool
    assert worst_case_between_jobs == pytest.approx(4.17, abs=0.01)


# ── the policy ─────────────────────────────────────────────────────


def test_an_empty_pool_advances_immediately():
    assert _advance_pool_cursor(rr=3, streak=0, produced=False) == (4, 0)


def test_an_empty_pool_resets_a_streak():
    # Otherwise a pool that goes quiet would keep its credit and jump the queue
    # again the next time round.
    assert _advance_pool_cursor(rr=3, streak=5, produced=True, limit=8)[1] == 6
    assert _advance_pool_cursor(rr=3, streak=5, produced=False) == (4, 0)


def test_a_producing_pool_is_kept():
    """The fix, in one assertion: a pool that just yielded work is polled again
    rather than the cursor moving on to five empty ones."""
    rr, streak = _advance_pool_cursor(rr=2, streak=0, produced=True, limit=8)
    assert rr == 2, "cursor moved off a productive pool"
    assert streak == 1


def test_a_producing_pool_yields_after_its_streak():
    """The fairness bound. Without it a permanently-busy pool would starve every
    other capability the worker advertises — worse than the latency it fixes."""
    rr, streak = 0, 0
    for _ in range(7):
        rr, streak = _advance_pool_cursor(rr, streak, produced=True, limit=8)
    assert (rr, streak) == (0, 7), "yielded early"

    rr, streak = _advance_pool_cursor(rr, streak, produced=True, limit=8)
    assert (rr, streak) == (1, 0), "did not yield at the limit"


def test_a_limit_of_one_is_the_old_strict_round_robin():
    # The escape hatch: ADA_WORKER_POOL_STREAK_LIMIT=1 restores the previous
    # behaviour exactly, without a rebuild.
    rr, streak = _advance_pool_cursor(rr=4, streak=0, produced=True, limit=1)
    assert (rr, streak) == (5, 0)


def test_a_zero_or_negative_limit_cannot_wedge_the_cursor():
    # A misconfigured limit must not mean "never advance", which would be a
    # worker that serves one pool forever and looks healthy doing it.
    for bad in (0, -1):
        rr, streak = _advance_pool_cursor(rr=4, streak=0, produced=True, limit=bad)
        assert rr == 5, f"limit={bad} pinned the cursor"


# ── the shape of the win ───────────────────────────────────────────


def test_a_saturated_single_pool_stops_paying_the_walk():
    """Simulate 24 jobs arriving on one pool of six, and count how often the
    cursor leaves it — each departure is a walk past five empty pools."""
    rr, streak, departures = 0, 0, 0
    for _ in range(24):
        new_rr, streak = _advance_pool_cursor(rr, streak, produced=True, limit=8)
        if new_rr != rr:
            departures += 1
        rr = new_rr

    assert departures == 3, "expected one walk per 8 jobs"
    # Strict round-robin would have walked after every single job.
    assert departures < 24 / 4
