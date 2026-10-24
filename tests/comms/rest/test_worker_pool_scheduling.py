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


# ── an idle worker asks the stream instead of walking its pools ─────

from ada.comms.rest.worker import (  # noqa: E402
    CANDIDATE_FETCH_TIMEOUT,
    IDLE_WAIT_SECONDS,
    _fetch_plan,
)

POOLS = ["base", "alpha", "beta", "gamma"]


def test_without_a_probe_the_plan_is_the_old_round_robin():
    assert _fetch_plan(POOLS, 5, 0, None, per_fetch_timeout=1.25) == [(1, 1.25)]


def test_mid_streak_the_producing_pool_is_fetched_again_without_asking():
    assert _fetch_plan(POOLS, 2, 3, {"base": 4}, per_fetch_timeout=1.25) == [(2, 1.25)]


def test_an_idle_round_fetches_only_pools_holding_messages_then_waits_on_one():
    plan = _fetch_plan(POOLS, 2, 0, {"alpha": 1, "gamma": 2}, per_fetch_timeout=1.25)
    # Round-robin order from the cursor (2 = beta): gamma, then alpha; then the wait.
    assert plan == [(3, CANDIDATE_FETCH_TIMEOUT), (1, CANDIDATE_FETCH_TIMEOUT), (2, IDLE_WAIT_SECONDS)]


def test_nothing_pending_is_one_wait_however_many_pools():
    many = [f"pool-{i}" for i in range(74)]
    assert _fetch_plan(many, 0, 0, {}, per_fetch_timeout=0.5) == [(0, IDLE_WAIT_SECONDS)]


def test_the_pool_that_used_up_its_streak_goes_last():
    plan = _fetch_plan(POOLS, 0, 0, {"base": 9, "beta": 1}, per_fetch_timeout=1.0, yielded=0)
    assert [i for i, _ in plan] == [2, 0, 0]


def test_no_pools_no_plan():
    assert _fetch_plan([], 0, 0, {}, per_fetch_timeout=1.0) == []


SUBJECT = "jobs.convert"


def _queue_with_stream(subjects=None, error=None):
    from types import SimpleNamespace

    from ada.comms.rest.queue import JobQueue

    calls = []

    class _JS:
        async def stream_info(self, name, subjects_filter=None):
            calls.append((name, subjects_filter))
            if error is not None:
                raise error
            return SimpleNamespace(state=SimpleNamespace(subjects=subjects))

    q = JobQueue(SimpleNamespace(stream="JOBS", subject=SUBJECT))
    q._js = _JS()
    return q, calls


def test_pending_pools_is_one_stream_info_keyed_by_pool():
    import asyncio

    q, calls = _queue_with_stream({f"{SUBJECT}.alpha": 2, f"{SUBJECT}.beta": 0, "other.x": 5})
    assert asyncio.run(q.pending_pools()) == {"alpha": 2}
    assert calls == [("JOBS", f"{SUBJECT}.>")]


def test_pending_pools_is_none_when_the_stream_cannot_be_asked():
    import asyncio

    q, _ = _queue_with_stream(error=PermissionError("permissions violation"))
    assert asyncio.run(q.pending_pools()) is None
    q._js = None
    assert asyncio.run(q.pending_pools()) is None


# ── a plugin's preferred pools go first ────────────────────────────

from ada.comms.rest.worker import _preferred_indices  # noqa: E402


def test_preferred_names_map_to_served_pools_only():
    assert _preferred_indices(POOLS, ["Gamma", "nope", "gamma", "alpha"]) == [3, 1]


def test_a_preferred_pool_is_fetched_first_and_waited_on():
    plan = _fetch_plan(POOLS, 0, 0, {"alpha": 1, "gamma": 1}, per_fetch_timeout=1.0, preferred=[3])
    assert plan == [(3, CANDIDATE_FETCH_TIMEOUT), (1, CANDIDATE_FETCH_TIMEOUT), (3, IDLE_WAIT_SECONDS)]


def test_an_idle_preferred_pool_is_still_the_one_waited_on():
    # Nothing pending anywhere: the pull this worker keeps open is on ITS pool, so the next job
    # published there is delivered to it rather than to whichever worker fetches next.
    assert _fetch_plan(POOLS, 1, 0, {}, per_fetch_timeout=1.0, preferred=[2]) == [(2, IDLE_WAIT_SECONDS)]


def test_without_a_probe_the_preferred_pool_comes_before_the_round_robin_one():
    assert _fetch_plan(POOLS, 1, 0, None, per_fetch_timeout=1.0, preferred=[3]) == [(3, 1.0), (1, 1.0)]
    assert _fetch_plan(POOLS, 3, 0, None, per_fetch_timeout=1.0, preferred=[3]) == [(3, 1.0)]


def test_a_preferred_pool_that_used_up_its_streak_yields_a_round():
    plan = _fetch_plan(POOLS, 0, 0, {"gamma": 5, "beta": 1}, per_fetch_timeout=1.0, preferred=[3], yielded=3)
    assert [i for i, _ in plan] == [2, 3, 0]
    # Mid-streak the preference changes nothing.
    assert _fetch_plan(POOLS, 1, 2, {"gamma": 5}, per_fetch_timeout=1.0, preferred=[3]) == [(1, 1.0)]


def test_the_registry_hook_is_optional_and_tolerant(monkeypatch):
    import ada.plugins as plugins
    from ada.comms.rest.plugin_registry import locally_preferred_pools

    plugins.reset_registry()
    try:
        assert locally_preferred_pools() == []
        plugins.register_preferred_pools("p1", lambda: ["alpha", "", "alpha"])
        plugins.register_preferred_pools("p2", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        plugins.register_preferred_pools("p3", lambda: None)
        assert locally_preferred_pools() == ["alpha"]
        # An ada.plugins from before the hook reads as "no preference".
        monkeypatch.delattr(plugins, "preferred_worker_pools")
        assert locally_preferred_pools() == []
    finally:
        plugins.reset_registry()
