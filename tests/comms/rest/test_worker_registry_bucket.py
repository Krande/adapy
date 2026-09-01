"""The worker registry can live in a KV bucket of its own.

WHY IT MATTERS. A worker must write arbitrary job ids to report progress, so
its NATS credential needs ``$KV.<jobs-bucket>.>``. Registry rows sit in that
same bucket under ``__meta_worker__<id>``, and a NATS subject wildcard matches a
WHOLE token — ``__meta_worker__<id>`` is one token, so the ``>`` that job
progress requires already covers every worker's registry row. No ``deny`` takes
it back either: ``__meta_worker__*`` is not a prefix pattern. So any credential
that can report job progress can overwrite any worker's registry row, including
one claiming a ``plugin_id`` it does not own — and the API believes what a
registry row claims.

Splitting the registry into its own bucket is what makes a per-worker
permission expressible at all. These tests cover the behaviour that split has
to preserve, and the changeover that would otherwise empty the admin panel.
"""

from __future__ import annotations

import json

import pytest
from nats.js.errors import KeyNotFoundError

from ada.comms.rest.config import QueueConfig
from ada.comms.rest.queue import JobQueue


class FakeEntry:
    def __init__(self, value: bytes | None):
        self.value = value


class FakeKV:
    """Enough of ``nats.js.kv.KeyValue`` for the registry methods."""

    def __init__(self, name: str):
        self.name = name
        self.store: dict[str, bytes] = {}

    async def put(self, key: str, value: bytes) -> None:
        self.store[key] = value

    async def get(self, key: str) -> FakeEntry:
        if key not in self.store:
            raise KeyNotFoundError()
        return FakeEntry(self.store[key])

    async def delete(self, key: str) -> None:
        if key not in self.store:
            raise KeyNotFoundError()
        del self.store[key]

    async def keys(self) -> list[str]:
        return list(self.store)


def _queue(registry_bucket: str = "") -> tuple[JobQueue, FakeKV, FakeKV]:
    q = JobQueue(
        QueueConfig(
            url=None,
            stream="s",
            subject="subj",
            kv_bucket="jobs",
            durable="d",
            registry_kv_bucket=registry_bucket,
        )
    )
    jobs, registry = FakeKV("jobs"), FakeKV("registry")
    # What connect()/_bind_existing() would have left behind.
    q._kv = jobs
    if q.registry_is_separate:
        q._registry_kv = registry
    return q, jobs, registry


def _row(kv: FakeKV, worker_id: str) -> dict:
    return json.loads(kv.store[f"__meta_worker__{worker_id}"].decode())


# --- the default: nothing changes -----------------------------------------


def test_unset_keeps_the_registry_in_the_jobs_bucket():
    q, _, _ = _queue()
    assert q.registry_is_separate is False
    assert q.registry_bucket == "jobs"


@pytest.mark.asyncio
async def test_unset_reads_and_writes_the_jobs_bucket():
    q, jobs, registry = _queue()
    await q.register_worker("w1", {"capabilities": ["base"]})
    assert _row(jobs, "w1")["capabilities"] == ["base"]
    assert registry.store == {}

    assert [w["worker_id"] for w in await q.list_workers()] == ["w1"]

    await q.unregister_worker("w1")
    assert jobs.store == {}


@pytest.mark.asyncio
async def test_unset_follows_kv_even_when_never_connected():
    """The un-split mode resolves through ``_kv`` rather than a second attribute
    somebody has to remember to point at it — a connect path or a test that sets
    only ``_kv`` must still register."""
    q = JobQueue(QueueConfig(url=None, stream="s", subject="subj", kv_bucket="jobs", durable="d"))
    q._kv = FakeKV("jobs")
    assert q._registry_kv is None
    await q.register_worker("w1", {"capabilities": []})
    assert "__meta_worker__w1" in q._kv.store


# --- split out -------------------------------------------------------------


def test_registry_bucket_is_reported_when_set():
    q, _, _ = _queue("workers")
    assert q.registry_is_separate is True
    assert q.registry_bucket == "workers"


def test_naming_the_same_bucket_is_not_a_split():
    """Pointing the registry at the jobs bucket by name must behave exactly like
    leaving it unset — otherwise the merge path below would scan one bucket
    twice and report every worker as its own duplicate."""
    q = JobQueue(
        QueueConfig(
            url=None, stream="s", subject="subj", kv_bucket="jobs", durable="d", registry_kv_bucket="jobs"
        )
    )
    assert q.registry_is_separate is False


@pytest.mark.asyncio
async def test_split_writes_registry_rows_away_from_job_rows():
    """The whole point: no registry row lands in the bucket whose ``>`` every
    worker credential holds."""
    q, jobs, registry = _queue("workers")
    await q.register_worker("w1", {"capabilities": ["cad"]})
    assert _row(registry, "w1")["capabilities"] == ["cad"]
    assert jobs.store == {}


@pytest.mark.asyncio
async def test_registry_key_stays_one_subject_token():
    """``$KV.<bucket>.<key>`` is the subject a permission names. The key must
    stay a single token, or an exact per-worker publish permission stops being
    expressible and this whole change buys nothing."""
    q, _, registry = _queue("workers")
    await q.register_worker("ext-01", {})
    (key,) = registry.store
    assert "." not in key
    assert ">" not in key and "*" not in key


# --- the changeover --------------------------------------------------------


@pytest.mark.asyncio
async def test_split_still_lists_workers_that_have_not_moved_yet():
    """Turning the split on must not empty the admin panel — and with it
    /api/plugins and extension routing — for workers still writing to the old
    bucket because they have not restarted."""
    q, jobs, registry = _queue("workers")
    jobs.store["__meta_worker__old"] = json.dumps({"capabilities": ["base"]}).encode()
    await q.register_worker("new", {"capabilities": ["cad"]})

    assert sorted(w["worker_id"] for w in await q.list_workers()) == ["new", "old"]


@pytest.mark.asyncio
async def test_the_moved_row_wins_over_the_stale_one():
    """Same worker, both buckets: the dedicated bucket's row is the one written
    by the worker that has already restarted, so it is the fresher of the two."""
    q, jobs, registry = _queue("workers")
    jobs.store["__meta_worker__w1"] = json.dumps({"capabilities": ["base"], "where": "old"}).encode()
    await q.register_worker("w1", {"capabilities": ["cad"], "where": "new"})

    workers = await q.list_workers()
    assert len(workers) == 1
    assert workers[0]["where"] == "new"


@pytest.mark.asyncio
async def test_unregister_clears_both_buckets():
    """Otherwise a worker that shuts down during the changeover lingers in the
    listing for the full prune horizon, from the row nobody deletes."""
    q, jobs, registry = _queue("workers")
    jobs.store["__meta_worker__w1"] = json.dumps({"capabilities": ["base"]}).encode()
    await q.register_worker("w1", {"capabilities": ["cad"]})

    await q.unregister_worker("w1")
    assert jobs.store == {}
    assert registry.store == {}


@pytest.mark.asyncio
async def test_unregister_survives_a_credential_that_cannot_touch_the_old_bucket():
    """A narrowed worker credential may no longer write the jobs bucket's
    registry rows at all. That is the intended end state, not a shutdown
    failure."""

    class Forbidden(FakeKV):
        async def delete(self, key):
            raise PermissionError("permissions violation")

    q, _, registry = _queue("workers")
    q._kv = Forbidden("jobs")
    await q.register_worker("w1", {})

    await q.unregister_worker("w1")  # must not raise
    assert registry.store == {}


@pytest.mark.asyncio
async def test_a_missing_registry_bucket_is_empty_not_an_error():
    """A worker whose API has not been upgraded to create the bucket binds
    nothing. It must still run — a worker missing from a listing beats a worker
    that will not start."""
    q, _, _ = _queue("workers")
    q._registry_kv = None
    await q.register_worker("w1", {})  # quietly does nothing
    assert await q.list_workers() == []
