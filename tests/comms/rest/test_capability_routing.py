"""Auto-routing by source extension must honour the live worker registry.

``JobQueue._capability_for_ext`` maps a source key's extension to the
capability tag of whichever online worker advertises it (``.odb`` →
``abaqus`` etc.). The registry entries carry a ``last_heartbeat`` timestamp
but NO ``online`` key — that boolean is derived from heartbeat staleness.
Reading ``online`` directly (the original bug) made every worker look
offline and routed every job to the default ``base`` pool, so ``.odb``
misrouted even with the abaqus worker up.
"""

import time

import pytest

from ada.comms.rest.config import QueueConfig
from ada.comms.rest.queue import JobQueue


def _queue() -> JobQueue:
    return JobQueue(
        QueueConfig(
            url=None,
            stream="s",
            subject="subj",
            kv_bucket="kv",
            durable="d",
        )
    )


def _with_workers(monkeypatch, q: JobQueue, workers: list[dict]) -> None:
    async def fake_list_workers():
        return workers

    monkeypatch.setattr(q, "list_workers", fake_list_workers)


@pytest.mark.asyncio
async def test_fresh_worker_routes_to_its_capability(monkeypatch):
    q = _queue()
    _with_workers(
        monkeypatch,
        q,
        [
            {
                "capabilities": ["abaqus"],
                "source_exts": [".odb", ".sqlite"],
                "last_heartbeat": time.time(),
            }
        ],
    )
    assert await q._capability_for_ext("fem/x.odb") == "abaqus"


@pytest.mark.asyncio
async def test_stale_worker_falls_back_to_default(monkeypatch):
    q = _queue()
    _with_workers(
        monkeypatch,
        q,
        [
            {
                "capabilities": ["abaqus"],
                "source_exts": [".odb"],
                # Older than the staleness window → treated as offline.
                "last_heartbeat": time.time() - (q.WORKER_STALE_AFTER_S + 30),
            }
        ],
    )
    assert await q._capability_for_ext("fem/x.odb") == q.DEFAULT_CAPABILITY


@pytest.mark.asyncio
async def test_unadvertised_ext_falls_back_to_default(monkeypatch):
    q = _queue()
    _with_workers(
        monkeypatch,
        q,
        [
            {
                "capabilities": ["abaqus"],
                "source_exts": [".odb"],
                "last_heartbeat": time.time(),
            }
        ],
    )
    assert await q._capability_for_ext("model.step") == q.DEFAULT_CAPABILITY


@pytest.mark.asyncio
async def test_missing_heartbeat_is_offline(monkeypatch):
    q = _queue()
    _with_workers(
        monkeypatch,
        q,
        [{"capabilities": ["abaqus"], "source_exts": [".odb"]}],
    )
    assert await q._capability_for_ext("x.odb") == q.DEFAULT_CAPABILITY
