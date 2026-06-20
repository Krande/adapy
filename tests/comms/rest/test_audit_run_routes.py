"""Audit-run admin routes + their DB helpers (live Postgres).

Covers the run-management features the audit panel grew: friendly ``seq``,
idle-aware duration, the manual validate claim, re-dispatch, delete, and the
per-cell history query. Live-Postgres only (opt-in via ``ADA_TEST_POSTGRES_URL``)
— audit runs are DB-backed.

The test environment has no NATS, so worker-pool conversion runs can't be
created through the API (the create route 503s without a queue). The route
tests therefore use the ``wasm`` pool (accepted without NATS) and seed
finished runs directly through the DB helpers; the parity dispatch that a real
``validate`` would enqueue is exercised at the DB-helper level instead.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.scope import Scope  # noqa: E402

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)

pytestmark = needs_postgres


def _settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(url=None, stream="ada", subject="s", kv_bucket="kv", durable="d"),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url=POSTGRES_URL,
    )


@pytest.fixture
def db():
    """Yield ``(pool, run)`` — an asyncpg pool and a runner bound to the *same*
    event loop (asyncpg connections can't cross loops). Truncates the audit
    tables for a clean slate."""
    loop = asyncio.new_event_loop()

    def run(coro):
        return loop.run_until_complete(coro)

    p = run(db_module.init_pool(POSTGRES_URL))
    assert p is not None, "init_pool returned None for ADA_TEST_POSTGRES_URL"
    run(p.execute("TRUNCATE audit_log, audit_parity, audit_runs RESTART IDENTITY CASCADE"))
    try:
        yield p, run
    finally:
        run(p.close())
        loop.close()


def _storage(client: TestClient):
    """Pull the app's Storage out of a route closure (same trick as the wasm test)."""
    for route in client.app.routes:
        ep = getattr(route, "endpoint", None)
        for c in getattr(ep, "__closure__", None) or ():
            v = c.cell_contents
            if v.__class__.__name__ == "Storage":
                return v
    raise RuntimeError("storage not found on app")


async def _finish_run(p, *, scope="shared", worker_pool=None, auto_validate=False, n=1):
    """Create a run, set its total, and close ``n`` cells so it finishes."""
    run = await db_module.create_audit_run(p, scope=scope, worker_pool=worker_pool, auto_validate=auto_validate)
    await db_module.set_audit_run_total(p, run["id"], n)
    for i in range(n):
        await db_module.insert_audit(
            p,
            user_sub=None,
            scope_kind="shared",
            scope_id=None,
            action="convert",
            key=f"models/f{i}.step",
            target_format="glb",
            status="done",
            duration_ms=10,
            audit_run_id=run["id"],
        )
    return await db_module.get_audit_run(p, run["id"])


# ── DB helpers ─────────────────────────────────────────────────────


def test_create_run_has_seq_and_idle_defaults(db):
    pool, run = db
    r = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None, auto_validate=True))
    assert isinstance(r["seq"], int) and r["seq"] >= 1
    assert r["idle_ms"] == 0
    assert r["auto_validate"] is True
    assert r["auto_validate_dispatched_at"] is None


def test_seq_is_monotonic(db):
    pool, run = db
    a = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None))
    b = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None))
    assert b["seq"] > a["seq"]


def test_extend_folds_idle_gap(db):
    pool, run = db
    r = run(_finish_run(pool, n=1))
    # Backdate the finish so the reopen sees a ~1h idle gap.
    run(pool.execute("UPDATE audit_runs SET finished_at = NOW() - INTERVAL '1 hour' WHERE id = $1", r["id"]))
    run(db_module.extend_audit_run_total(pool, r["id"], 1))
    after = run(db_module.get_audit_run(pool, r["id"]))
    assert after["status"] == "running"
    assert after["finished_at"] is None
    assert after["total"] == 2
    # ~3.6M ms with generous tolerance for clock + execution skew.
    assert 3_400_000 < after["idle_ms"] < 3_800_000


def test_claim_run_for_validation_is_once(db):
    pool, run = db
    r = run(_finish_run(pool, n=1))
    first = run(db_module.claim_run_for_validation(pool, r["id"]))
    assert first is not None and first["auto_validate_dispatched_at"] is not None
    assert run(db_module.claim_run_for_validation(pool, r["id"])) is None  # already claimed


def test_claim_run_for_validation_skips_running(db):
    pool, run = db
    r = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None))
    run(db_module.set_audit_run_total(pool, r["id"], 2))  # 2 cells, none closed → running
    assert run(db_module.claim_run_for_validation(pool, r["id"])) is None


def test_claim_auto_validate_only_flagged_runs(db):
    pool, run = db
    flagged = run(_finish_run(pool, auto_validate=True, n=1))
    run(_finish_run(pool, auto_validate=False, n=1))  # must NOT be claimed
    claimed = run(db_module.claim_audit_run_for_auto_validate(pool))
    assert claimed is not None and claimed["id"] == flagged["id"]
    assert run(db_module.claim_audit_run_for_auto_validate(pool)) is None


def test_delete_audit_run_removes_log_rows(db):
    pool, run = db
    r = run(_finish_run(pool, n=2))
    assert run(db_module.delete_audit_run(pool, r["id"])) is True
    assert run(db_module.get_audit_run(pool, r["id"])) is None
    left = run(pool.fetchval("SELECT count(*) FROM audit_log WHERE audit_run_id = $1", r["id"]))
    assert left == 0
    assert run(db_module.delete_audit_run(pool, r["id"])) is False  # already gone


def test_cell_history_newest_first(db):
    pool, run = db
    r1 = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None))
    r2 = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None))
    for rr, status, dur in ((r1, "done", 11), (r2, "error", 22)):
        run(
            db_module.insert_audit(
                pool,
                user_sub=None,
                scope_kind="shared",
                scope_id=None,
                action="convert",
                key="models/a.step",
                target_format="ifc",
                status=status,
                duration_ms=dur,
                error=("boom" if status == "error" else None),
                audit_run_id=rr["id"],
            )
        )
    # A different target must not leak into the history.
    run(
        db_module.insert_audit(
            pool,
            user_sub=None,
            scope_kind="shared",
            scope_id=None,
            action="convert",
            key="models/a.step",
            target_format="xml",
            status="done",
            audit_run_id=r1["id"],
        )
    )
    hist = run(db_module.audit_log_history_for_cell(pool, "models/a.step", "ifc"))
    assert [h["status"] for h in hist] == ["error", "done"]  # newest first
    assert hist[0]["error"] == "boom" and hist[0]["duration_ms"] == 22


# ── Routes (TestClient) ────────────────────────────────────────────


def test_route_create_exposes_seq(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        r = client.post("/api/admin/audit/runs", json={"scope": "shared", "worker_pool": "wasm"})
        assert r.status_code == 202, r.text
        run = r.json()
        assert isinstance(run["seq"], int)
        assert run["auto_validate"] is False  # gated off for wasm


def test_route_delete_finished_and_409_running(tmp_path, db):
    pool, run = db
    finished = run(_finish_run(pool, worker_pool="wasm", n=1))
    running = run(db_module.create_audit_run(pool, scope="shared", worker_pool="wasm"))
    run(db_module.set_audit_run_total(pool, running["id"], 2))  # running

    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.delete(f"/api/admin/audit/runs/{running['id']}").status_code == 409
        assert client.delete(f"/api/admin/audit/runs/{finished['id']}").status_code == 200
        assert client.get(f"/api/admin/audit/runs/{finished['id']}").status_code == 404


def test_route_validate_appends_and_dedups(tmp_path, db):
    pool, run = db
    r = run(_finish_run(pool, worker_pool="wasm", n=1))
    before_total = r["total"]
    with TestClient(create_app(_settings(tmp_path))) as client:
        # Parity cells come from enumerating the scope's storage, so stage a
        # source that yields one (a .step can target ifc/xml/step).
        run(_storage(client).put_bytes(Scope.shared(), "models/part.step", b"ISO-10303-21;"))
        r1 = client.post(f"/api/admin/audit/runs/{r['id']}/validate")
        assert r1.status_code == 202, r1.text
        after = client.get(f"/api/admin/audit/runs/{r['id']}").json()["run"]
        assert after["auto_validate_dispatched_at"] is not None
        assert after["total"] > before_total  # parity cell appended to the same run
        # Second validate is rejected — a run is validated at most once.
        assert client.post(f"/api/admin/audit/runs/{r['id']}/validate").status_code == 409


def test_route_validate_409_when_running(tmp_path, db):
    pool, run = db
    r = run(db_module.create_audit_run(pool, scope="shared", worker_pool="wasm"))
    run(db_module.set_audit_run_total(pool, r["id"], 2))  # running
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.post(f"/api/admin/audit/runs/{r['id']}/validate").status_code == 409


def test_route_redispatch_links_parent(tmp_path, db):
    pool, run = db
    prior = run(_finish_run(pool, worker_pool="wasm", n=1))
    with TestClient(create_app(_settings(tmp_path))) as client:
        r = client.post(f"/api/admin/audit/runs/{prior['id']}/re-dispatch")
        assert r.status_code == 202, r.text
        new = r.json()
        assert new["id"] != prior["id"]
        assert new["parent_run_id"] == prior["id"]
        assert new["trigger"] == "re-dispatch"


def test_route_cell_history(tmp_path, db):
    pool, run = db
    r = run(db_module.create_audit_run(pool, scope="shared", worker_pool="wasm"))
    run(
        db_module.insert_audit(
            pool,
            user_sub=None,
            scope_kind="shared",
            scope_id=None,
            action="convert",
            key="models/h.step",
            target_format="ifc",
            status="done",
            duration_ms=7,
            audit_run_id=r["id"],
        )
    )
    with TestClient(create_app(_settings(tmp_path))) as client:
        resp = client.get("/api/admin/audit/cell-history", params={"key": "models/h.step", "target": "ifc"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["key"] == "models/h.step" and body["target_format"] == "ifc"
        assert len(body["history"]) == 1 and body["history"][0]["status"] == "done"
