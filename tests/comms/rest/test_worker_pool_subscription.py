"""A worker must consume every capability pool it advertises, not just the first.

``ADA_WORKER_CAPABILITIES`` has always parsed as a list, and the per-job
capability gate has always checked the whole set — but the worker opened a
subscription for ``capabilities[0]`` alone. An image advertising several pools
therefore served only the first: jobs for the rest sat in their subjects with
nothing consuming them, which reads as an idle pool rather than a broken one.

These cover the two decisions that turn a capability list into subscriptions.
``ada.comms.rest.worker`` pulls in ``fcntl`` transitively, so it is importable
on POSIX only.
"""

import pytest

worker = pytest.importorskip(
    "ada.comms.rest.worker",
    reason="ada.comms.rest.worker imports fcntl (POSIX only)",
)


def test_every_capability_becomes_a_pool():
    assert worker._pool_capabilities(["base", "capacity", "abaqus"]) == [
        "base",
        "capacity",
        "abaqus",
    ]


def test_pools_are_lowercased_and_deduplicated_preserving_order():
    # Two consumers on one subject would double-deliver, so neither casing nor
    # repetition in the env value may produce a duplicate pool.
    assert worker._pool_capabilities(["Capacity", "base", "capacity", " BASE "]) == [
        "capacity",
        "base",
    ]


def test_empty_capability_list_still_subscribes_to_base():
    # A worker subscribed to nothing would idle forever while looking healthy.
    assert worker._pool_capabilities([]) == ["base"]
    assert worker._pool_capabilities(["", "  "]) == ["base"]


def test_one_full_poll_cycle_stays_about_fetch_timeout():
    # Pools are polled one at a time, so an undivided timeout would make pickup
    # latency grow linearly with the number of capabilities served.
    for n in (1, 2, 4):
        assert worker._per_fetch_timeout(n) * n == pytest.approx(worker.FETCH_TIMEOUT)


def test_per_fetch_timeout_is_floored_and_survives_zero():
    # Many pools must not degenerate into a busy-loop of near-instant fetches.
    assert worker._per_fetch_timeout(100) == 0.5
    assert worker._per_fetch_timeout(0) == pytest.approx(worker.FETCH_TIMEOUT)
