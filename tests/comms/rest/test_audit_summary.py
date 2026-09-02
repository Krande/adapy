"""Aggregate counts behind the Audit tab's Overview (live Postgres).

The Overview turns each count into a control: click "Failed", land on those
rows. That only holds if the summary and the log agree about what the filter
means, and if the summary counts the whole population rather than the page the
client happens to be holding — the log is keyset-paginated at 100, so a sweep
of several hundred would otherwise under-report every number on the screen.

The one deliberate asymmetry is ``status``: the summary ignores it, because the
status tiles are what SET it. Honouring it would mean clicking a tile zeroes
the other three — the act of drilling in would destroy the context you drilled
in from. That is asserted here rather than left to a comment.

Live-Postgres only (opt-in via ``ADA_TEST_POSTGRES_URL``) — audit rows are
DB-backed and the aggregation is SQL.
"""

from __future__ import annotations

import asyncio
import datetime
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
    """``(pool, run)`` on one event loop, with the audit tables truncated."""
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


async def _seed(
    p, *, n, status, target="glb", key_prefix="models/f", action="convert", error=None, scope_kind="shared"
):
    for i in range(n):
        await db_module.insert_audit(
            p,
            user_sub=None,
            scope_kind=scope_kind,
            scope_id=None,
            action=action,
            key=f"{key_prefix}{i}.step",
            target_format=target,
            status=status,
            error=error,
        )


# ── the counts themselves ──────────────────────────────────────────


def test_counts_every_state_and_zero_fills_the_rest(db):
    pool, run = db
    run(_seed(pool, n=3, status="done"))
    run(_seed(pool, n=2, status="error", error="boom"))

    s = run(db_module.summarize_audit(pool))
    assert s["total"] == 5
    # The four states the queue writes are always present, so the UI never has
    # to invent a missing key to render a tile.
    assert s["by_status"] == {"queued": 0, "running": 0, "done": 3, "error": 2}


def test_counts_the_population_not_a_page(db):
    """The log pages at 100; the summary must not."""
    pool, run = db
    run(_seed(pool, n=150, status="queued"))

    s = run(db_module.summarize_audit(pool))
    assert s["by_status"]["queued"] == 150, "summary counted a page, not the population"


def test_by_target_splits_by_state(db):
    pool, run = db
    run(_seed(pool, n=4, status="done", target="glb"))
    run(_seed(pool, n=1, status="error", target="glb", error="boom"))
    run(_seed(pool, n=2, status="error", target="step", error="boom"))

    s = run(db_module.summarize_audit(pool))
    by_target = {r["target"]: r for r in s["by_target"]}
    assert by_target["glb"]["counts"] == {"done": 4, "error": 1}
    assert by_target["step"]["counts"] == {"error": 2}
    # Ordered by volume, so the busiest format is the one you read first.
    assert s["by_target"][0]["target"] == "glb"


def test_top_errors_are_ranked(db):
    pool, run = db
    run(_seed(pool, n=3, status="error", error="out of memory", key_prefix="a/"))
    run(_seed(pool, n=1, status="error", error="timeout", key_prefix="b/"))
    run(_seed(pool, n=5, status="done"))

    s = run(db_module.summarize_audit(pool))
    assert [(e["error"], e["count"]) for e in s["top_errors"]] == [
        ("out of memory", 3),
        ("timeout", 1),
    ]


def test_reports_states_beyond_the_job_lifecycle(db):
    """Real deployments carry more than queued/running/done/error.

    ``ok`` marks instantaneous actions (download, delete, view) and
    ``presigned`` a URL grant — on a live instance they outnumbered the jobs
    3:1 — while ``cancelled`` is a genuine job outcome. The summary must report
    every state it finds, not just the four it zero-fills, or the caller cannot
    tell that its tiles fail to account for the total.
    """
    pool, run = db
    run(_seed(pool, n=5, status="done"))
    run(_seed(pool, n=2, status="cancelled"))
    run(_seed(pool, n=7, status="ok", action="download", target=None))

    s = run(db_module.summarize_audit(pool))
    assert s["total"] == 14
    assert s["by_status"]["cancelled"] == 2, "a real job outcome went missing"
    assert s["by_status"]["ok"] == 7, "non-job rows went missing"
    # The four the queue writes are still guaranteed present for the tiles.
    for k in ("queued", "running", "done", "error"):
        assert k in s["by_status"]
    # And they do NOT account for the whole population, which is exactly why
    # the UI reports non-job activity separately.
    lifecycle = sum(s["by_status"][k] for k in ("queued", "running", "done", "error", "cancelled"))
    assert lifecycle < s["total"]


def test_rows_without_a_target_are_still_counted(db):
    """A download has no target_format. Grouping must not drop those rows."""
    pool, run = db
    run(_seed(pool, n=3, status="ok", action="download", target=None))

    s = run(db_module.summarize_audit(pool))
    assert s["total"] == 3
    assert [r["target"] for r in s["by_target"]] == ["—"]


# ── filters: shared with the log, except status ────────────────────


def test_filters_narrow_the_counts(db):
    pool, run = db
    run(_seed(pool, n=3, status="done", target="glb"))
    run(_seed(pool, n=2, status="done", target="ifc"))

    s = run(db_module.summarize_audit(pool, target_format="ifc"))
    assert s["total"] == 2
    assert s["by_status"]["done"] == 2


def test_key_filter_is_a_case_insensitive_substring(db):
    """Same semantics as list_audit's ``key_like`` — one filter, two surfaces."""
    pool, run = db
    run(_seed(pool, n=2, status="done", key_prefix="Beams/part"))
    run(_seed(pool, n=3, status="done", key_prefix="plates/part"))

    assert run(db_module.summarize_audit(pool, key_like="beams"))["total"] == 2
    assert run(db_module.summarize_audit(pool, key_like="PLATES"))["total"] == 3


def test_status_is_not_a_summary_filter(db):
    """The tiles set the status filter, so the summary must ignore it.

    If it did not, clicking "Failed" would zero Queued, Running and Succeeded —
    the drill-down would destroy the very context it was launched from.
    """
    pool, run = db
    run(_seed(pool, n=3, status="done"))
    run(_seed(pool, n=2, status="error", error="boom"))

    # summarize_audit takes no ``statuses`` argument at all; passing one is a
    # TypeError, which is the guarantee this test is pinning.
    with pytest.raises(TypeError):
        run(db_module.summarize_audit(pool, statuses=["error"]))


# ── congestion ─────────────────────────────────────────────────────


def test_queue_ages_report_how_long_work_has_waited(db):
    """``ts`` is the ENQUEUE time, so a queued row's age is its wait so far.

    This is the congestion signal: a growing oldest-wait means the pool is not
    keeping up, and it is visible before anything fails.
    """
    pool, run = db
    run(_seed(pool, n=3, status="queued"))
    run(
        pool.execute(
            "UPDATE audit_log SET ts = now() - interval '10 minutes' WHERE id = (SELECT min(id) FROM audit_log)"
        )
    )
    run(
        pool.execute(
            "UPDATE audit_log SET ts = now() - interval '2 minutes' WHERE id = (SELECT max(id) FROM audit_log)"
        )
    )

    c = run(db_module.summarize_audit(pool))["congestion"]
    assert c["queued"] == 3
    assert 590 < c["oldest_wait_s"] < 610, c["oldest_wait_s"]
    assert c["median_wait_s"] is not None
    assert c["mean_wait_s"] < c["oldest_wait_s"]


def test_only_queued_rows_count_as_waiting(db):
    """A finished job is not waiting. Counting it would make a healthy pool
    look congested in proportion to how much work it had already done."""
    pool, run = db
    run(_seed(pool, n=5, status="done"))
    run(_seed(pool, n=2, status="queued"))

    c = run(db_module.summarize_audit(pool))["congestion"]
    assert c["queued"] == 2


def test_an_empty_queue_reports_no_wait_rather_than_zero(db):
    """None, not 0.0 — "nothing is waiting" and "everything is served
    instantly" are different states and the UI renders them differently."""
    pool, run = db
    run(_seed(pool, n=4, status="done"))

    c = run(db_module.summarize_audit(pool))["congestion"]
    assert c["queued"] == 0
    assert c["oldest_wait_s"] is None
    assert c["median_wait_s"] is None


def test_congestion_honours_the_filter(db):
    pool, run = db
    run(_seed(pool, n=2, status="queued", target="glb"))
    run(_seed(pool, n=3, status="queued", target="ifc"))

    assert run(db_module.summarize_audit(pool, target_format="ifc"))["congestion"]["queued"] == 3


def test_running_is_carried_alongside_the_queue(db):
    pool, run = db
    run(_seed(pool, n=2, status="running"))
    run(_seed(pool, n=1, status="queued"))

    c = run(db_module.summarize_audit(pool))["congestion"]
    assert c["running"] == 2 and c["queued"] == 1


# ── the time window ────────────────────────────────────────────────


def test_relative_bounds_parse_against_the_server_clock():
    """ "6h" means six hours before NOW, resolved here rather than by the caller.

    A workstation whose clock runs a few minutes fast would otherwise post an
    absolute instant in the future and silently empty a "last 5 minutes" view —
    and the narrower the window, the larger the error, which is backwards.
    """
    now = datetime.datetime(2026, 9, 2, 12, 0, tzinfo=datetime.timezone.utc)
    assert db_module.parse_audit_time_bound("5m", now=now) == now - datetime.timedelta(minutes=5)
    assert db_module.parse_audit_time_bound("6h", now=now) == now - datetime.timedelta(hours=6)
    assert db_module.parse_audit_time_bound("7d", now=now) == now - datetime.timedelta(days=7)
    assert db_module.parse_audit_time_bound("1w", now=now) == now - datetime.timedelta(weeks=1)
    assert db_module.parse_audit_time_bound(" 30 D ", now=now) == now - datetime.timedelta(days=30)


def test_absolute_bounds_stay_absolute():
    got = db_module.parse_audit_time_bound("2026-09-01T10:30:00Z")
    assert got == datetime.datetime(2026, 9, 1, 10, 30, tzinfo=datetime.timezone.utc)
    # A naive instant is read as UTC rather than guessed at: the column is
    # timestamptz and comparing it against a naive value raises.
    naive = db_module.parse_audit_time_bound("2026-09-01T10:30:00")
    assert naive.tzinfo is not None


def test_an_empty_bound_means_unbounded():
    for empty in (None, "", "   "):
        assert db_module.parse_audit_time_bound(empty) is None


def test_an_unparseable_bound_is_refused_not_ignored():
    # Falling back to "all of history" would answer a question nobody asked,
    # and the number would look plausible.
    for bad in ("yesterday", "6 fortnights", "-1h", "2026-13-45"):
        with pytest.raises(ValueError):
            db_module.parse_audit_time_bound(bad)


def test_the_window_narrows_the_counts(db):
    pool, run = db
    run(_seed(pool, n=4, status="done"))
    run(
        pool.execute(
            "UPDATE audit_log SET ts = now() - interval '10 days' WHERE id <= (SELECT min(id)+1 FROM audit_log)"
        )
    )

    recent = run(db_module.summarize_audit(pool, since=db_module.parse_audit_time_bound("1d")))
    assert recent["total"] == 2, "old rows leaked into a 1-day window"
    assert run(db_module.summarize_audit(pool))["total"] == 4, "unbounded lost rows"


def test_until_bounds_the_other_end(db):
    pool, run = db
    run(_seed(pool, n=3, status="done"))
    past = db_module.parse_audit_time_bound("1h")
    assert run(db_module.summarize_audit(pool, until=past))["total"] == 0
    assert run(db_module.summarize_audit(pool))["total"] == 3


def test_a_bounded_historical_window_excludes_both_ends(db):
    """A period that does NOT run up to now — "what happened last Tuesday".

    Both bounds together, which is the case a relative window cannot express at
    all: every preset is anchored to the present.
    """
    pool, run = db
    run(_seed(pool, n=6, status="done"))
    # Three distinct eras: 20 days ago, 10 days ago, and now.
    run(
        pool.execute(
            "UPDATE audit_log SET ts = now() - interval '20 days' WHERE id IN (SELECT id FROM audit_log ORDER BY id LIMIT 2)"
        )
    )
    run(
        pool.execute(
            "UPDATE audit_log SET ts = now() - interval '10 days' WHERE id IN (SELECT id FROM audit_log ORDER BY id OFFSET 2 LIMIT 2)"
        )
    )

    since = db_module.parse_audit_time_bound("15d")
    until = db_module.parse_audit_time_bound("5d")
    got = run(db_module.summarize_audit(pool, since=since, until=until))
    assert got["total"] == 2, "a middle window caught the wrong era"

    # And the log agrees, which is what makes the count clickable.
    listed = run(db_module.list_audit(pool, since=since, until=until, limit=500))
    assert len(listed) == 2


def test_an_inverted_window_is_empty_not_an_error(db):
    """``until`` before ``since`` yields nothing. The UI warns; the API does not
    refuse, because the bounds are set independently and a transient inverted
    state is normal while the operator is still typing the second one."""
    pool, run = db
    run(_seed(pool, n=3, status="done"))
    got = run(
        db_module.summarize_audit(
            pool,
            since=db_module.parse_audit_time_bound("1d"),
            until=db_module.parse_audit_time_bound("2d"),
        )
    )
    assert got["total"] == 0


def test_the_log_and_the_summary_agree_on_the_window(db):
    """One filter, two surfaces: a window that counts N must list N."""
    pool, run = db
    run(_seed(pool, n=5, status="done"))
    run(
        pool.execute(
            "UPDATE audit_log SET ts = now() - interval '3 days' WHERE id <= (SELECT min(id)+1 FROM audit_log)"
        )
    )

    since = db_module.parse_audit_time_bound("1d")
    counted = run(db_module.summarize_audit(pool, since=since))["total"]
    listed = run(db_module.list_audit(pool, since=since, limit=500))
    assert counted == len(listed) == 3


def test_route_accepts_a_relative_window(db, tmp_path):
    pool, run = db
    run(_seed(pool, n=3, status="done"))
    run(pool.execute("UPDATE audit_log SET ts = now() - interval '40 days' WHERE id = (SELECT min(id) FROM audit_log)"))

    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/api/admin/audit/summary", params={"since": "30d"}).json()["total"] == 2
        assert client.get("/api/admin/audit/summary").json()["total"] == 3
        assert client.get("/api/admin/audit", params={"since": "30d"}).json()["entries"].__len__() == 2


def test_route_refuses_a_bad_window(db, tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        r = client.get("/api/admin/audit/summary", params={"since": "last tuesday"})
        assert r.status_code == 400, r.text
        assert "last tuesday" in r.text


# ── the route ──────────────────────────────────────────────────────


def test_route_returns_the_summary(db, tmp_path):
    pool, run = db
    run(_seed(pool, n=2, status="done"))
    run(_seed(pool, n=1, status="error", error="boom"))

    with TestClient(create_app(_settings(tmp_path))) as client:
        r = client.get("/api/admin/audit/summary")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3
        assert body["by_status"]["done"] == 2
        assert body["by_status"]["error"] == 1
        assert body["top_errors"] == [{"error": "boom", "count": 1}]


def test_route_ignores_status_but_honours_the_rest(db, tmp_path):
    pool, run = db
    run(_seed(pool, n=2, status="done", target="glb"))
    run(_seed(pool, n=1, status="error", target="glb", error="boom"))
    run(_seed(pool, n=4, status="done", target="ifc"))

    with TestClient(create_app(_settings(tmp_path))) as client:
        # status is accepted (the UI sends one filter object) and ignored.
        r = client.get("/api/admin/audit/summary", params={"status": "error", "target": "glb"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3, "status leaked into the summary"
        assert body["by_status"] == {"queued": 0, "running": 0, "done": 2, "error": 1}


def test_route_normalises_target_like_the_log_does(db, tmp_path):
    """``.GLB`` and ``glb`` are the same target — admin_audit strips and
    lower-cases, and the summary must not disagree with it."""
    pool, run = db
    run(_seed(pool, n=2, status="done", target="glb"))

    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/api/admin/audit/summary", params={"target": ".GLB"}).json()["total"] == 2
