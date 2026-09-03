"""A worker that has lost the bus must stop, not go quiet.

The failure this guards against is not a disconnect — those are ordinary and
usually heal — but the state after one where the worker neither recovers nor
fails. A client library can stop reading without raising, leaving the connection
object claiming to be connected while every request times out. Nothing else in
the worker notices: the liveness file is touched by the poll loop, which keeps
iterating happily against a dead connection, and the registry row simply goes
stale. Meanwhile jobs route to pools this worker advertises and no longer
consumes, and they sit there.

The heartbeat is the only round-trip an idle worker makes whose failure means
anything (an idle pull fetch times out whether the queue is quiet or the socket
is dead), so it is what watches. These tests pin the three things that has to
get right: it gives up after a *run* of failures, a success anywhere in that run
clears it, and being asked to stop is not the same as losing the bus.
"""

import asyncio

import pytest

from ada.comms.rest.worker import BUS_HEARTBEAT_FAILURE_LIMIT, _heartbeat_until_stopped

# Far below the production cadence so these run instantly. The behaviour under
# test is the counting, not the waiting.
TICK = 0.001


async def _run_with(results, *, failure_limit=3, stop=None):
    """Drive the loop with a scripted sequence of publish outcomes.

    The loop is left to run until it returns on its own, or until the script is
    exhausted and the test stops it — whichever happens first.
    """
    stop = stop or asyncio.Event()
    bus_lost = asyncio.Event()
    calls = []
    pending = list(results)

    async def publish() -> bool:
        if not pending:
            # Script exhausted without the loop giving up: end the test rather
            # than spin. A test that hangs here is a test that found a bug.
            stop.set()
            return True
        outcome = pending.pop(0)
        calls.append(outcome)
        return outcome

    await asyncio.wait_for(
        _heartbeat_until_stopped(
            publish=publish,
            stop=stop,
            bus_lost=bus_lost,
            interval=TICK,
            failure_limit=failure_limit,
        ),
        timeout=5.0,
    )
    return bus_lost, calls


@pytest.mark.asyncio
async def test_a_run_of_failures_gives_up_and_says_which_kind_of_stop_it_was():
    bus_lost, calls = await _run_with([False, False, False], failure_limit=3)
    assert bus_lost.is_set()
    # Exactly at the limit — not one heartbeat later, not one earlier.
    assert calls == [False, False, False]


@pytest.mark.asyncio
async def test_one_success_anywhere_in_the_run_clears_the_count():
    # THE POINT OF "CONSECUTIVE". A worker that heartbeats fine for hours with
    # the occasional blip must never trip this; only an unbroken run does. With
    # a limit of 3, this script never has three failures in a row.
    bus_lost, calls = await _run_with([False, False, True, False, False], failure_limit=3)
    assert not bus_lost.is_set()
    assert calls == [False, False, True, False, False]


@pytest.mark.asyncio
async def test_being_asked_to_stop_is_not_losing_the_bus():
    # The two exits take different codes: a supervisor should restart one and
    # leave the other alone. A shutdown that reported bus_lost would put every
    # deliberate stop into a restart loop.
    stop = asyncio.Event()
    bus_lost = asyncio.Event()

    async def publish() -> bool:
        raise AssertionError("must not heartbeat after stop is set")

    stop.set()
    await asyncio.wait_for(
        _heartbeat_until_stopped(publish=publish, stop=stop, bus_lost=bus_lost, interval=TICK, failure_limit=3),
        timeout=5.0,
    )
    assert not bus_lost.is_set()


@pytest.mark.asyncio
async def test_giving_up_also_stops_the_rest_of_the_worker():
    # The poll loop watches `stop`; without setting it the heartbeat would exit
    # alone and leave the worker consuming from a connection it just declared
    # dead — the exact silent state this whole mechanism exists to end.
    stop = asyncio.Event()
    bus_lost, _ = await _run_with([False] * 3, failure_limit=3, stop=stop)
    assert bus_lost.is_set()
    assert stop.is_set()


def test_the_shipped_limit_is_a_meaningful_stretch_of_time():
    # Guards the constant itself. Dropping it to 1 would restart a worker on a
    # single blip and lose in-flight work that would have recovered.
    assert BUS_HEARTBEAT_FAILURE_LIMIT >= 3
