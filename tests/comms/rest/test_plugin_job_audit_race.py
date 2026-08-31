"""The enqueue-then-audit ordering loses the worker's terminal status.

``POST /api/plugins/{plugin_id}/jobs`` publishes the job to NATS *first* and
only then inserts the ``audit_log`` row that records it as ``queued``
(``app.py``: ``job = await queue.enqueue(...)`` then ``await _audit(...,
status="queued")``).

The worker's terminal write is a bare ``UPDATE audit_log ... WHERE job_id = $1``
(``db.update_audit_by_job``) with no upsert — it silently affects zero rows when
the row is not there yet. A plugin_job that the worker finishes in tens of
milliseconds (the cached-derived-blob short circuit in ``worker._process_one``
is a single storage HEAD) can therefore complete *before* the API's INSERT
lands, and the row is then created as ``queued`` and stays that way forever:
``duration_ms`` NULL, ``error`` NULL, no NATS message left, no KV entry (the KV
row went terminal and the 15-minute cleanup sweep dropped it).

These tests drive the interleaving directly instead of relying on timing luck:
the fake JetStream ``publish`` runs the worker's audit transitions inline, which
is exactly "the worker won the race".
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-race-storage-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import worker as worker_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.queue import JobQueue  # noqa: E402

# ---------------------------------------------------------------- fake DB ---
#
# Just enough of ``audit_log`` to reproduce the two statements that matter:
# the INSERT the API issues and the ``WHERE job_id`` UPDATEs the worker issues.


class FakeAuditTable:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.missed_updates: list[str] = []

    def run(self, sql: str, args: tuple):
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO audit_log"):
            self.rows.append(
                {
                    "user_sub": args[0],
                    "action": args[3],
                    "key": args[4],
                    "target_format": args[5],
                    "status": args[6],
                    "error": args[7],
                    "duration_ms": args[8],
                    "job_id": args[9],
                    "audit_run_id": args[11],
                }
            )
            return None
        if s.startswith("UPDATE audit_log SET status = 'running'"):
            for r in self.rows:
                if r["job_id"] == args[0] and r["status"] == "queued":
                    r["status"] = "running"
                    return None
            self.missed_updates.append(f"running:{args[0]}")
            return None
        if s.startswith("UPDATE audit_log SET status = $2"):
            for r in self.rows:
                if r["job_id"] == args[0]:
                    r["status"] = args[1]
                    r["error"] = args[2] if args[2] is not None else r["error"]
                    r["duration_ms"] = args[3] if args[3] is not None else r["duration_ms"]
                    return {"audit_run_id": r["audit_run_id"]}
            # This is the bug's fingerprint: a terminal write with nothing to
            # write it to.
            self.missed_updates.append(f"terminal:{args[0]}")
            return None
        raise AssertionError(f"unmodelled SQL: {s[:120]}")


class _NullTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, table: FakeAuditTable) -> None:
        self._t = table

    def transaction(self):
        return _NullTx()

    async def fetchrow(self, sql, *args):
        return self._t.run(sql, args)

    async def execute(self, sql, *args):
        self._t.run(sql, args)


class _AcquireCtx:
    def __init__(self, table: FakeAuditTable) -> None:
        self._t = table

    async def __aenter__(self):
        return FakeConn(self._t)

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, table: FakeAuditTable) -> None:
        self.table = table

    async def execute(self, sql, *args):
        self.table.run(sql, args)

    def acquire(self):
        return _AcquireCtx(self.table)

    async def close(self):
        return None


# ------------------------------------------------------------- fake queue ---


class FakeKV:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def put(self, key, value):
        self.store[key] = value

    async def get(self, key):
        from nats.js.errors import KeyNotFoundError

        if key not in self.store:
            raise KeyNotFoundError()
        return type("Entry", (), {"value": self.store[key]})()

    async def keys(self):
        return list(self.store)


class FakeJS:
    """``publish`` is the race knob: ``on_publish`` runs in the exact window
    between the NATS publish and the API's audit INSERT."""

    def __init__(self, on_publish) -> None:
        self.published: list[tuple[str, str]] = []
        self._on_publish = on_publish

    async def publish(self, subject, payload):
        job_id = payload.decode("utf-8")
        self.published.append((subject, job_id))
        if self._on_publish is not None:
            await self._on_publish(job_id)


def _settings(tmp_path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            # Non-empty so ``queue.enabled`` is True and the endpoint takes the
            # NATS path rather than the in-process ``local_jobs`` one.
            url="nats://test-not-dialled",
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="ada-viewer-jobs",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(
            enabled=False,
            issuer="",
            client_id="",
            audience="",
            admin_group="",
            cli_token_secret="",
        ),
        database_url="",
    )


def _build(monkeypatch, tmp_path, on_publish):
    table = FakeAuditTable()
    pool = FakePool(table)
    holder: dict = {"pool": pool}

    async def _hook(job_id):
        if on_publish is not None:
            await on_publish(holder["pool"], job_id)

    async def fake_connect(self, **kwargs):
        self._js = FakeJS(_hook)
        self._kv = FakeKV()

    async def fake_list_workers(self):
        return []

    monkeypatch.setattr(JobQueue, "connect", fake_connect)
    monkeypatch.setattr(JobQueue, "list_workers", fake_list_workers)

    app = create_app(_settings(tmp_path))
    return app, table, pool


async def _worker_finishes_the_job(pool, job_id):
    """What the worker does on the fast path: no source download, the derived
    blob is already there, so ``_process_one`` short-circuits straight to
    ``_audit_done(..., "done", ...)``."""
    import time

    await worker_module._audit_done(pool, job_id, "done", None, time.monotonic() - 0.03)


def test_worker_terminal_write_before_the_audit_insert_strands_the_row(monkeypatch, tmp_path):
    """The failing case. The worker completes between publish and INSERT; its
    UPDATE hits nothing, and the row is then born ``queued`` and never moves."""
    app, table, pool = _build(monkeypatch, tmp_path, _worker_finishes_the_job)

    with TestClient(app) as client:
        client.app.state.db_pool = pool
        r = client.post("/api/plugins/external-models/jobs", json={"options": {"catalogue": "a"}})

    assert r.status_code == 200, r.text
    assert len(table.rows) == 1
    row = table.rows[0]

    # The row the operator sees in Postgres, and the toast restores from
    # /api/scopes/{scope}/my-jobs, forever.
    assert row["status"] == "done", (
        f"audit row stranded at {row['status']!r} (duration_ms={row['duration_ms']!r}, "
        f"error={row['error']!r}); lost worker writes: {table.missed_updates}"
    )


def test_worker_terminal_write_after_the_audit_insert_is_recorded(monkeypatch, tmp_path):
    """The control: this is what the five siblings did. Same code, the only
    difference is that the INSERT won."""
    captured: list = []

    async def defer(pool, job_id):
        captured.append((pool, job_id))

    app, table, pool = _build(monkeypatch, tmp_path, defer)

    with TestClient(app) as client:
        client.app.state.db_pool = pool
        r = client.post("/api/plugins/external-models/jobs", json={"options": {"catalogue": "b"}})
        assert r.status_code == 200, r.text

    # ...and only now does the worker land.
    import asyncio

    asyncio.run(_worker_finishes_the_job(*captured[0]))

    assert table.rows[0]["status"] == "done"
    assert table.missed_updates == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
