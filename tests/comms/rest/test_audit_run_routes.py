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


def test_cells_duration_ms_is_sum_of_cells(db):
    pool, run = db
    r = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None))
    run(db_module.set_audit_run_total(pool, r["id"], 2))
    run(
        db_module.insert_audit(
            pool,
            user_sub=None,
            scope_kind="shared",
            scope_id=None,
            action="convert",
            key="a.step",
            target_format="glb",
            status="done",
            duration_ms=10,
            job_id="jobA",
            audit_run_id=r["id"],
        )
    )
    run(
        db_module.insert_audit(
            pool,
            user_sub=None,
            scope_kind="shared",
            scope_id=None,
            action="convert",
            key="b.step",
            target_format="glb",
            status="error",
            duration_ms=20,
            job_id="jobB",
            audit_run_id=r["id"],
        )
    )
    after = run(db_module.get_audit_run(pool, r["id"]))
    assert after["cells_duration_ms"] == 30  # sum, not wall clock
    assert after["ok"] == 1 and after["failed"] == 1 and after["status"] == "finished"


def test_reset_audit_cell_for_rerun_undoes_counter_and_reopens(db):
    pool, run = db
    r = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None))
    run(db_module.set_audit_run_total(pool, r["id"], 2))
    run(
        db_module.insert_audit(
            pool,
            user_sub=None,
            scope_kind="shared",
            scope_id=None,
            action="convert",
            key="a.step",
            target_format="glb",
            status="done",
            duration_ms=10,
            job_id="jobA",
            audit_run_id=r["id"],
        )
    )
    run(
        db_module.insert_audit(
            pool,
            user_sub=None,
            scope_kind="shared",
            scope_id=None,
            action="convert",
            key="b.step",
            target_format="glb",
            status="error",
            duration_ms=20,
            job_id="jobB",
            audit_run_id=r["id"],
        )
    )

    # Re-run cell B: the failed counter drops, the run reopens, and B's row is
    # re-pointed at a fresh job with its result timing cleared.
    ok = run(db_module.reset_audit_cell_for_rerun(pool, r["id"], "b.step", "glb", "jobB2"))
    assert ok is True
    mid = run(db_module.get_audit_run(pool, r["id"]))
    assert mid["failed"] == 0 and mid["ok"] == 1
    assert mid["status"] == "running" and mid["finished_at"] is None
    assert mid["cells_duration_ms"] == 10  # B's 20ms cleared until it re-completes

    # The worker completes the new job → B goes green, run re-finishes, runtime
    # reflects the new per-cell timing.
    run(db_module.update_audit_by_job(pool, job_id="jobB2", status="done", duration_ms=15))
    end = run(db_module.get_audit_run(pool, r["id"]))
    assert end["ok"] == 2 and end["failed"] == 0
    assert end["status"] == "finished"
    assert end["cells_duration_ms"] == 25  # 10 + the re-run's 15

    # Unknown cell → no-op, returns False.
    assert run(db_module.reset_audit_cell_for_rerun(pool, r["id"], "nope.step", "glb", "x")) is False


def test_reset_audit_cell_folds_idle_gap(db):
    pool, run = db
    r = run(_finish_run(pool, n=1))  # one done cell, run finished
    # Backdate the finish so the re-run sees a ~1h gap since the original run.
    run(pool.execute("UPDATE audit_runs SET finished_at = NOW() - INTERVAL '1 hour' WHERE id = $1", r["id"]))
    run(db_module.reset_audit_cell_for_rerun(pool, r["id"], "models/f0.step", "glb", "job0b"))
    after = run(db_module.get_audit_run(pool, r["id"]))
    assert after["status"] == "running" and after["finished_at"] is None
    # The ~1h gap is folded into idle_ms so wall clock won't swallow it when the
    # cell re-completes — the re-run only adds its own delta.
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


def _close_cell(p, run_id, *, key, target_format="glb", action="convert"):
    return db_module.insert_audit(
        p,
        user_sub=None,
        scope_kind="shared",
        scope_id=None,
        action=action,
        key=key,
        target_format=target_format,
        status="done",
        duration_ms=10,
        audit_run_id=run_id,
    )


def test_reserved_validation_counts_upfront(db):
    """An auto-validate run advertises conversions + parity in ``total`` from
    the start; the validation dispatch consumes the reservation instead of
    growing the total."""
    pool, run = db
    r = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None, auto_validate=True))
    # 2 conversion cells + 1 reserved parity cell.
    run(db_module.set_audit_run_total(pool, r["id"], 3, validate_total=1))
    fresh = run(db_module.get_audit_run(pool, r["id"]))
    assert fresh["total"] == 3 and fresh["validate_total"] == 1

    # Not claimable while conversion cells are still outstanding.
    run(_close_cell(pool, r["id"], key="models/f0.step"))
    assert run(db_module.claim_audit_run_for_auto_validate(pool)) is None

    # All conversion cells landed: the reserve keeps the run 'running',
    # and the poller can now claim it for validation.
    run(_close_cell(pool, r["id"], key="models/f1.step"))
    mid = run(db_module.get_audit_run(pool, r["id"]))
    assert mid["status"] == "running" and mid["finished_at"] is None
    claimed = run(db_module.claim_audit_run_for_auto_validate(pool))
    assert claimed is not None and claimed["id"] == r["id"]

    # Dispatch swaps the reservation for the actual parity count — total unchanged.
    run(db_module.consume_audit_run_validation_reserve(pool, r["id"], 1))
    consumed = run(db_module.get_audit_run(pool, r["id"]))
    assert consumed["total"] == 3 and consumed["validate_total"] == 0
    assert consumed["status"] == "running"
    assert consumed["idle_ms"] == 0  # never finished, so no idle gap folded in

    # Parity cell lands → run finishes at the originally-advertised total.
    run(_close_cell(pool, r["id"], key="models/f0.step", target_format="parity", action="validate"))
    done = run(db_module.get_audit_run(pool, r["id"]))
    assert done["status"] == "finished" and done["total"] == 3


def test_consume_reserve_handles_drift_and_zero(db):
    """Scope drift between the two enumerations moves the total by the
    difference; zero actual parity cells finishes the run on the spot."""
    pool, run = db
    # Drift up: reserved 1, actual 2.
    a = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None, auto_validate=True))
    run(db_module.set_audit_run_total(pool, a["id"], 2, validate_total=1))
    run(_close_cell(pool, a["id"], key="models/f0.step"))
    run(db_module.consume_audit_run_validation_reserve(pool, a["id"], 2))
    drifted = run(db_module.get_audit_run(pool, a["id"]))
    assert drifted["total"] == 3 and drifted["validate_total"] == 0
    assert drifted["status"] == "running"

    # Zero actual: reservation released, run finishes (no bump will arrive).
    b = run(db_module.create_audit_run(pool, scope="shared", worker_pool=None, auto_validate=True))
    run(db_module.set_audit_run_total(pool, b["id"], 2, validate_total=1))
    run(_close_cell(pool, b["id"], key="models/f0.step"))
    run(db_module.consume_audit_run_validation_reserve(pool, b["id"], 0))
    released = run(db_module.get_audit_run(pool, b["id"]))
    assert released["total"] == 1 and released["validate_total"] == 0
    assert released["status"] == "finished" and released["finished_at"] is not None


def test_delete_audit_run_removes_log_rows(db):
    pool, run = db
    r = run(_finish_run(pool, n=2))
    # delete_audit_run returns (deleted, queued_job_ids_to_clean).
    deleted, _ = run(db_module.delete_audit_run(pool, r["id"]))
    assert deleted is True
    assert run(db_module.get_audit_run(pool, r["id"])) is None
    left = run(pool.fetchval("SELECT count(*) FROM audit_log WHERE audit_run_id = $1", r["id"]))
    assert left == 0
    deleted_again, _ = run(db_module.delete_audit_run(pool, r["id"]))
    assert deleted_again is False  # already gone


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
    # An on-demand / viewer-triggered re-conversion (no audit_run_id) must NOT
    # leak in: history is a cross-run comparison, so standalone rows are excluded.
    run(
        db_module.insert_audit(
            pool,
            user_sub=None,
            scope_kind="shared",
            scope_id=None,
            action="convert",
            key="models/a.step",
            target_format="ifc",
            status="done",
            duration_ms=99,
            audit_run_id=None,
        )
    )
    hist = run(db_module.audit_log_history_for_cell(pool, "models/a.step", "ifc"))
    assert [h["status"] for h in hist] == ["error", "done"]  # newest first
    assert hist[0]["error"] == "boom" and hist[0]["duration_ms"] == 22
    # the standalone (audit_run_id NULL) row is filtered out
    assert all(h["duration_ms"] != 99 for h in hist)
    assert all(h["audit_run_id"] is not None for h in hist)


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
