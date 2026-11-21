"""Several jobs in flight on one worker (``ADA_WORKER_MAX_CONCURRENT_JOBS``).

The poll loop leases a message only while a slot is free and runs each job as its own task,
with its own lease keep-alive. One slot -- the default -- is the worker as it always was: the
next fetch waits for the job before it.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from ada.comms.rest.worker.pools import _JobSlots, _max_concurrent_jobs  # noqa: E402


def test_one_job_at_a_time_unless_asked_for_more():
    assert _max_concurrent_jobs({}) == 1
    assert _max_concurrent_jobs({"ADA_WORKER_MAX_CONCURRENT_JOBS": ""}) == 1
    assert _max_concurrent_jobs({"ADA_WORKER_MAX_CONCURRENT_JOBS": "4"}) == 4
    assert _max_concurrent_jobs({"ADA_WORKER_MAX_CONCURRENT_JOBS": " 8 "}) == 8
    # Nonsense never stops the worker, and never means "no limit".
    assert _max_concurrent_jobs({"ADA_WORKER_MAX_CONCURRENT_JOBS": "0"}) == 1
    assert _max_concurrent_jobs({"ADA_WORKER_MAX_CONCURRENT_JOBS": "-3"}) == 1
    assert _max_concurrent_jobs({"ADA_WORKER_MAX_CONCURRENT_JOBS": "many"}) == 1


def test_two_jobs_run_at_once_and_a_third_waits_for_a_slot():
    async def scenario() -> list[str]:
        slots = _JobSlots(2)
        stop = asyncio.Event()
        events: list[str] = []
        release = {name: asyncio.Event() for name in ("a", "b")}

        async def job(name: str) -> None:
            events.append(f"start {name}")
            await release[name].wait()
            events.append(f"end {name}")

        assert await slots.wait_for_free(stop, timeout=0.01)
        slots.start(job("a"), name="a")
        assert await slots.wait_for_free(stop, timeout=0.01)
        slots.start(job("b"), name="b")
        await asyncio.sleep(0)
        # Both are going: no slot for a third, so the loop would not fetch.
        assert events == ["start a", "start b"]
        assert not slots.free
        assert not await slots.wait_for_free(stop, timeout=0.05)

        # One ends: its slot frees and the wait returns at once, the other still going.
        release["b"].set()
        assert await slots.wait_for_free(stop, timeout=1.0)
        assert len(slots.tasks) == 1
        release["a"].set()
        await slots.drain()
        assert not slots.tasks
        return events

    assert asyncio.run(scenario()) == ["start a", "start b", "end b", "end a"]


def test_one_slot_is_the_serial_worker():
    async def scenario() -> list[str]:
        slots = _JobSlots(1)
        stop = asyncio.Event()
        events: list[str] = []

        async def job(name: str) -> None:
            events.append(f"start {name}")
            await asyncio.sleep(0.01)
            events.append(f"end {name}")

        for name in ("a", "b", "c"):
            while not await slots.wait_for_free(stop, timeout=1.0):
                pass
            slots.start(job(name), name=name)
        await slots.drain()
        return events

    assert asyncio.run(scenario()) == ["start a", "end a", "start b", "end b", "start c", "end c"]


def test_a_failing_job_frees_its_slot_and_leaves_the_others_going():
    async def scenario() -> tuple[bool, bool]:
        slots = _JobSlots(2)
        stop = asyncio.Event()
        other_done = asyncio.Event()
        go_on = asyncio.Event()

        async def fails() -> None:
            raise RuntimeError("this run broke")

        async def survives() -> None:
            await go_on.wait()
            other_done.set()

        slots.start(survives(), name="ok")
        slots.start(fails(), name="broken")
        assert await slots.wait_for_free(stop, timeout=1.0)  # the failed one's slot
        go_on.set()
        await slots.drain()
        return other_done.is_set(), slots.free

    assert asyncio.run(scenario()) == (True, True)


def test_stop_wakes_a_worker_waiting_for_a_slot():
    async def scenario() -> bool:
        slots = _JobSlots(1)
        stop = asyncio.Event()
        never = asyncio.Event()
        slots.start(never.wait(), name="long")
        asyncio.get_running_loop().call_later(0.02, stop.set)
        free = await slots.wait_for_free(stop, timeout=5.0)
        never.set()
        await slots.drain()
        return free

    assert asyncio.run(scenario()) is False
