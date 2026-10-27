"""The API answers bucket-wide questions from a live mirror, not from scans.

Scanning a KV bucket replays every key — delete markers included — through a
fresh server-side consumer. Routing scanned once per enqueued job, so a large
model load meant hundreds of full replays, and the consumers those scans left
behind OOM-killed the bus. A mirror replays once, then only receives changes.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from ada.comms.rest.config import QueueConfig
from ada.comms.rest.kv_mirror import KvMirror
from ada.comms.rest.queue import JobQueue


class Update:
    def __init__(self, key, value, revision, operation=None, created=None):
        self.key = key
        self.value = value
        self.revision = revision
        self.operation = operation
        self.created = created if created is not None else time.time()


class LiveWatcher:
    """A watcher that replays a snapshot, sends the end-of-replay ``None``,
    then delivers whatever the test pushes, until stopped."""

    def __init__(self, snapshot):
        self._q: asyncio.Queue = asyncio.Queue()
        for u in snapshot:
            self._q.put_nowait(u)
        self._q.put_nowait(None)
        self.stopped = False

    def push(self, update):
        self._q.put_nowait(update)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._q.get()
        if item is StopAsyncIteration:
            raise StopAsyncIteration
        return item

    async def stop(self):
        self.stopped = True
        self._q.put_nowait(StopAsyncIteration)


class WatchableKV:
    """A bucket whose only bucket-wide read is ``watch``. ``get`` counts calls,
    so a test can prove a read never went to the server."""

    def __init__(self, store: dict[str, bytes], markers: dict[str, float] | None = None):
        self.store = dict(store)
        self.markers = dict(markers or {})
        self.watchers: list[LiveWatcher] = []
        self.watch_kwargs: list[dict] = []
        self.gets = 0
        self._rev = 0

    def _snapshot(self):
        out = []
        for k, v in self.store.items():
            self._rev += 1
            out.append(Update(k, v, self._rev))
        for k, created in self.markers.items():
            self._rev += 1
            out.append(Update(k, None, self._rev, "PURGE", created))
        return out

    async def watch(self, keys, **kwargs):
        self.watch_kwargs.append(kwargs)
        w = LiveWatcher(self._snapshot())
        self.watchers.append(w)
        return w

    async def get(self, key):
        self.gets += 1
        raise AssertionError("a mirrored read must not fetch from the server")

    def write(self, key, value=None, operation=None, created=None):
        self._rev += 1
        for w in self.watchers:
            w.push(Update(key, value, self._rev, operation, created))
        return self._rev


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


def _worker_row(worker_id: str, **extra) -> tuple[str, bytes]:
    row = {"last_heartbeat": time.time(), "capabilities": ["base"], **extra}
    return f"__meta_worker__{worker_id}", json.dumps(row).encode()


@pytest.mark.asyncio
async def test_mirror_replays_once_then_follows_changes():
    kv = WatchableKV({"a": b"1"}, markers={"gone": time.time()})
    m = KvMirror(kv, "jobs")
    m.start()
    assert await m.wait_ready(1)
    assert dict(m.items()) == {"a": b"1"}
    assert m.size == (1, 1)

    kv.write("b", b"2")
    kv.write("a", None, "DEL")
    await _settle()
    assert dict(m.items()) == {"b": b"2"}
    assert m.size == (1, 2)

    kv.write("a", b"back")
    await _settle()
    assert m.get("a") == b"back"
    assert len(kv.watchers) == 1, "one watcher for the life of the mirror"
    await m.stop()
    assert kv.watchers[0].stopped


@pytest.mark.asyncio
async def test_mirror_watcher_is_reaped_quickly_if_the_process_dies():
    kv = WatchableKV({})
    m = KvMirror(kv, "jobs")
    m.start()
    await m.wait_ready(1)
    assert kv.watch_kwargs[0]["inactive_threshold"] <= 120
    await m.stop()


@pytest.mark.asyncio
async def test_an_older_or_repeated_update_does_not_win():
    m = KvMirror(WatchableKV({}), "jobs")
    m.apply("k", b"new", 5, None, time.time())
    m.apply("k", b"old", 3, None, time.time())
    m.apply("k", None, 5, "DEL", time.time())
    assert m.get("k") == b"new"


@pytest.mark.asyncio
async def test_a_replay_drops_keys_removed_while_the_watcher_was_away():
    kv = WatchableKV({"stays": b"1"})
    m = KvMirror(kv, "jobs")
    m.apply("vanished", b"x", 1, None, time.time())
    m.start()
    await m.wait_ready(1)
    assert dict(m.items()) == {"stays": b"1"}
    await m.stop()


def _queue(store, markers=None) -> tuple[JobQueue, WatchableKV]:
    q = JobQueue(QueueConfig(url=None, stream="s", subject="subj", kv_bucket="jobs", durable="d"))
    kv = WatchableKV(store, markers)
    q._kv = kv
    return q, kv


@pytest.mark.asyncio
async def test_routing_and_worker_list_read_the_mirror_not_the_server():
    key, row = _worker_row("w1", source_exts=[".odb"], capabilities=["abaqus"])
    q, kv = _queue({key: row, "job1": b"{}"})
    await q.start_mirrors()

    for _ in range(50):  # a burst of enqueues
        assert await q._capability_for_ext("scope/model.odb") == "abaqus"
    assert [w["worker_id"] for w in await q.list_workers()] == ["w1"]

    assert kv.gets == 0
    assert len(kv.watchers) == 1
    await q.close()


@pytest.mark.asyncio
async def test_worker_rows_handed_out_are_copies():
    key, row = _worker_row("w1")
    q, _ = _queue({key: row})
    await q.start_mirrors()
    (w,) = await q.list_workers()
    w["capabilities"].append("tampered")
    (again,) = await q.list_workers()
    assert again["capabilities"] == ["base"]
    await q.close()


class RecordingJS:
    def __init__(self):
        self.purges: list[tuple] = []

    async def purge_stream(self, name, seq=None, subject=None, keep=None):
        self.purges.append((name, seq, subject))
        return True


@pytest.mark.asyncio
async def test_old_delete_markers_are_removed_and_young_ones_kept():
    now = time.time()
    q, kv = _queue({"live": b"{}"}, markers={"old-job": now - 7200, "new-job": now - 60})
    q._js = RecordingJS()
    await q.start_mirrors()

    assert await q.purge_old_tombstones(older_than_s=3600) == 1
    [(stream, seq, subject)] = q._js.purges
    assert stream == "KV_jobs"
    assert subject == "$KV.jobs.old-job"
    # Bounded by the marker's own revision (2: the replay sends "live" first),
    # and ``seq`` is exclusive — so a later write to the same key can never
    # be removed with it.
    assert seq == 2 + 1
    assert q._mirrors["jobs"].size == (1, 1)

    # Nothing left that is old enough.
    assert await q.purge_old_tombstones(older_than_s=3600) == 0
    await q.close()


@pytest.mark.asyncio
async def test_completed_jobs_are_found_from_the_mirror():
    old = time.time() - 7200
    done = json.dumps(
        {"job_id": "j1", "source_key": "s", "derived_key": "d", "status": "done", "updated_at": old}
    ).encode()
    queued = json.dumps(
        {"job_id": "j2", "source_key": "s", "derived_key": "d", "status": "queued", "updated_at": old}
    ).encode()
    q, kv = _queue({"j1": done, "j2": queued})
    purged: list[str] = []

    async def purge(key):
        purged.append(key)

    kv.purge = purge
    await q.start_mirrors()
    assert await q.purge_completed_jobs(grace_s=600) == 1
    assert purged == ["j1"]
    assert kv.gets == 0
    await q.close()


@pytest.mark.asyncio
async def test_without_a_mirror_reads_still_work_by_scanning():
    """Workers never start a mirror. Their reads must behave as before."""
    from tests.comms.rest.test_worker_registry_bucket import FakeKV

    q = JobQueue(QueueConfig(url=None, stream="s", subject="subj", kv_bucket="jobs", durable="d"))
    q._kv = FakeKV("jobs")
    await q.register_worker("w1", {"capabilities": ["base"]})
    assert [w["worker_id"] for w in await q.list_workers()] == ["w1"]
