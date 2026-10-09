"""Issue recheck against live Postgres: the ``issue_fp`` backfill, the cells a recheck
resolves (failure-corpus copy first), and a finished recheck run closing its issue through
the issue-bot pass. Opt-in via ``ADA_TEST_POSTGRES_URL`` like the other DB-backed tests;
the forge is a stub.

The fixture TRUNCATEs the audit tables, so it refuses any database that is not on this
machine -- a test URL pointed at a shared or production database must fail, not wipe it.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

import pytest

from ada.comms.rest import audit_issue
from ada.comms.rest import db as db_module
from ada.comms.rest import issue_client
from ada.comms.rest.audit_issue import fingerprint_job, fp_label, recheck_cells
from ada.comms.rest.routes import admin_audit_perf

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture
def db():
    host = urlparse(POSTGRES_URL).hostname
    if host not in _LOCAL_HOSTS:
        pytest.fail(f"refusing to TRUNCATE audit tables on non-local database host {host!r}")
    loop = asyncio.new_event_loop()

    def run(coro):
        return loop.run_until_complete(coro)

    p = run(db_module.init_pool(POSTGRES_URL))
    assert p is not None
    run(p.execute("TRUNCATE audit_log, audit_parity, audit_runs RESTART IDENTITY CASCADE"))
    try:
        yield p, run
    finally:
        run(p.close())
        loop.close()


def _failed(pool, run, **kw):
    base = dict(user_sub="u1", action="convert", target_format="glb", status="failed", error="boom")
    base.update(kw)
    run(db_module.insert_audit(pool, **base))


def test_backfill_links_rows_and_a_recheck_resolves_the_failure_corpus_copy(db):
    pool, run = db
    ar = run(db_module.create_audit_run(pool, scope="corpus:regress", worker_pool="audit"))
    run(db_module.set_audit_run_total(pool, ar["id"], 5))
    _failed(pool, run, scope_kind="user", scope_id="u1", key="private/plant.step", failure_key="ab12.step")
    _failed(pool, run, scope_kind="corpus", scope_id="regress", key="a.step", audit_run_id=ar["id"])
    _failed(pool, run, scope_kind="corpus", scope_id="regress", key="other.ifc", error="different")

    assert run(db_module.backfill_audit_log_issue_fps(pool)) == 3
    assert run(db_module.backfill_audit_log_issue_fps(pool)) == 0

    fp = fingerprint_job({"key": "a.step", "target_format": "glb", "error": "boom"})
    rows = run(db_module.list_issue_fp_rows(pool, fp))
    assert len(rows) == 2
    groups, skipped = recheck_cells(rows, failure_slug="failures")
    assert groups == {
        ("corpus:failures", None): [("ab12.step", "glb")],
        ("corpus:regress", "audit"): [("a.step", "glb")],
    }
    assert skipped == []
    assert run(db_module.count_issue_fp_cells(pool, [fp])) == {fp: 2}


class _Forge:
    def __init__(self, label):
        self.label = label
        self.commented: list[tuple[int, str]] = []
        self.states: list[tuple[int, str]] = []
        self.created: list[dict] = []

    async def list_issues_by_label(self, label, *, state="open"):
        if label == self.label and state == "open" and not self.states:
            return [type("I", (), {"number": 7, "title": "t", "html_url": None, "labels": [label], "state": "open"})()]
        return []

    async def find_issue_by_title(self, title):
        return None

    async def create_issue(self, *, title, body, labels):
        self.created.append({"title": title, "labels": list(labels)})

    async def comment_issue(self, number, *, body):
        self.commented.append((number, body))

    async def update_issue_body(self, number, *, body):
        pass

    async def set_issue_state(self, number, *, state):
        self.states.append((number, state))


def test_pg_issue_claims_serialise_one_fingerprint_and_remember_its_issue(db):
    pool, run = db
    run(pool.execute("TRUNCATE audit_issue_claims"))
    claims = db_module.PgIssueClaims(pool, target="github::o/r")
    order: list[str] = []

    async def sync(name, number):
        async with claims.hold("fp1") as held:
            order.append(f"{name}:in:{await held.get()}")
            await asyncio.sleep(0.05)
            if await held.get() is None:
                await held.record(number)
            order.append(f"{name}:out")

    async def both():
        await asyncio.gather(sync("a", 11), sync("b", 22))

    run(both())
    # one at a time, and the second saw the first one's issue
    assert order[1].endswith(":out") and order[2].endswith(":in:11")

    async def readback():
        other = db_module.PgIssueClaims(pool, target="forgejo:https://x:o/r")
        async with other.hold("fp1") as held:
            assert await held.get() is None, "claims are per forge repo"
        async with claims.hold("fp1") as held:
            got = await held.get()
            await held.forget()
            return got, await held.get()

    assert run(readback()) == (11, None)


def test_a_finished_recheck_run_closes_its_issue(db, monkeypatch):
    pool, run = db
    fp = fingerprint_job({"key": "a.step", "target_format": "glb", "error": "boom"})
    rc_run = run(
        db_module.create_audit_run(
            pool, scope="corpus:regress", worker_pool="audit", trigger=audit_issue.RECHECK_TRIGGER, force_rebuild=True
        )
    )
    run(db_module.create_issue_recheck(pool, fp=fp, run_id=rc_run["id"], cells=[("a.step", "glb")], created_by="admin"))
    run(db_module.set_audit_run_total(pool, rc_run["id"], 1))
    assert run(db_module.pending_recheck_fps(pool, [fp])) == {fp}, "a running recheck blocks a second one"

    run(
        db_module.insert_audit(
            pool,
            user_sub="admin",
            scope_kind="corpus",
            scope_id="regress",
            action="convert",
            key="a.step",
            target_format="glb",
            status="done",
            audit_run_id=rc_run["id"],
            worker_image_tag="img:2",
        )
    )
    assert run(db_module.get_audit_run(pool, rc_run["id"]))["status"] == "finished"
    assert run(db_module.pending_recheck_fps(pool, [fp])) == set()

    forge = _Forge(fp_label(fp))

    async def _cfg(_pool):
        return {"kind": "github", "repo": "o/r", "token": "t", "base_url": None, "token_env": "X"}

    monkeypatch.setattr(admin_audit_perf, "load_issue_target_config", _cfg)
    monkeypatch.setattr(issue_client, "build_client", lambda *a, **k: forge)

    claimed = run(db_module.claim_audit_run_for_issue_bot(pool))
    assert claimed["id"] == rc_run["id"]
    run(admin_audit_perf.run_issue_bot_for(pool, claimed))

    assert forge.states == [(7, "closed")]
    assert "closing" in forge.commented[0][1] and "`img:2`" in forge.commented[0][1]
    latest = run(db_module.latest_rechecks(pool, [fp]))[fp]
    assert latest["verdict"] == "fixed" and latest["verdict_detail"] == "passed 1/1"
    assert run(db_module.get_audit_run(pool, rc_run["id"]))["issue_bot_status"] == "done"
