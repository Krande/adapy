"""Admin -> Database: storage by feature area, pruning old history, retention.

Always-on tests cover the request parsing, the area grouping, the retention tick's schedule and
the route's dry-run default (with the DB layer stubbed). The live-Postgres tests (skipped unless
ADA_TEST_POSTGRES_URL is set) only ever touch objects they create: a throwaway table for the
storage report, and rows dated 1975 for the prune -- the prune runs with a cutoff in 1977, so
no real row can be old enough to match -- all removed again afterwards.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import db as dbm
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.db import attribute_index as ai
from ada.comms.rest.db import storage_insight as si
from ada.comms.rest.routes import admin_db

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

#: Test rows are dated here; prunes run with OLD_DAYS, i.e. a cutoff two years later.
OLD = datetime(1975, 1, 1, tzinfo=timezone.utc)
NOW = datetime(1977, 1, 1, tzinfo=timezone.utc)
OLD_DAYS = 1


# --- always on ----------------------------------------------------------------------------------


def test_tables_are_grouped_into_areas_and_partitions_follow_their_parent():
    assert si.area_of("asset_sweep_items") == "sweeps"
    assert si.area_of("asset_change_runs") == "change_checks"
    assert si.area_of("audit_log") == "audit_log" and si.area_of("profile_function_stats") == "metrics"
    assert si.area_of("attr_p_alpha_0123456789abcdef", "attr_nodes") == "attribute_index"
    assert si.area_of("attr_n_0123456789abcdef_01234567") == "attribute_index"  # a build table
    assert si.area_of("users") == "other" and si.area_of("zz_anything") == "other"
    assert "other" not in si.PRUNABLE_AREAS and "attribute_index" in si.PRUNABLE_AREAS


def test_prune_requests_are_checked():
    assert si.parse_prune_request({"sweeps": {"older_than_days": 30}, "audit_log": 90}) == {
        "sweeps": {"older_than_days": 30},
        "audit_log": {"older_than_days": 90},
    }
    sel = [{"scope": "shared", "provider": "p", "collection": "ALPHA"}]
    assert si.parse_prune_request({"attribute_index": {"collections": sel}}) == {
        "attribute_index": {"collections": sel}
    }
    for bad in (
        None,
        {},
        {"other": {"older_than_days": 5}},  # not prunable
        {"nope": {"older_than_days": 5}},
        {"sweeps": {"older_than_days": 0}},  # "everything" is a reset, not a prune
        {"sweeps": {"older_than_days": "soon"}},
        {"sweeps": {"collections": sel}},  # only the attribute index goes by collection
        {"attribute_index": {"collections": [{"scope": "shared"}]}},
    ):
        with pytest.raises(ValueError):
            si.parse_prune_request(bad)


def test_retention_runs_once_a_day():
    now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
    assert admin_db.retention_due(None, now)
    assert admin_db.retention_due({"at": "garbage"}, now)
    assert not admin_db.retention_due({"at": "2026-10-12T01:00:00+00:00"}, now)
    assert admin_db.retention_due({"at": "2026-10-11T11:59:00+00:00"}, now)


@pytest.mark.asyncio
async def test_the_tick_does_nothing_without_a_retention(monkeypatch):
    async def none(pool):
        return {}

    async def boom(*a, **kw):
        raise AssertionError("must not prune")

    monkeypatch.setattr(si, "get_retention", none)
    monkeypatch.setattr(si, "prune", boom)
    assert await admin_db.retention_tick(object()) is None


@pytest.mark.asyncio
async def test_the_tick_prunes_every_area_with_a_retention_and_records_it(monkeypatch):
    seen, saved = {}, {}

    async def retention(pool):
        return {"sweeps": 30, "audit_log": 90}

    async def last_run(pool):
        return None

    async def prune(pool, areas, **kw):
        seen.update(areas=areas, **kw)
        return {
            "areas": [{"id": a, "rows": 1, "est_bytes": 10} for a in areas],
            "rows": 2,
            "est_bytes": 20,
            "vacuum": {"ran": [], "errors": []},
        }

    async def record(pool, doc):
        saved.update(doc)

    monkeypatch.setattr(si, "get_retention", retention)
    monkeypatch.setattr(si, "get_retention_last_run", last_run)
    monkeypatch.setattr(si, "prune", prune)
    monkeypatch.setattr(si, "set_retention_last_run", record)
    await admin_db.retention_tick(object(), now=NOW)
    assert seen["areas"] == {"sweeps": {"older_than_days": 30}, "audit_log": {"older_than_days": 90}}
    assert seen["dry_run"] is False and seen["vacuum"] is True
    assert saved["at"] == NOW.isoformat() and saved["result"]["rows"] == 2


def _settings(tmp_path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=None,
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="ada-viewer-jobs",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


def test_the_prune_route_is_a_dry_run_unless_told_otherwise(tmp_path, monkeypatch):
    calls = []

    async def prune(pool, areas, *, dry_run, vacuum, **kw):
        calls.append((areas, dry_run, vacuum))
        return {"dry_run": dry_run, "areas": [], "rows": 0, "est_bytes": 0, "vacuum": {"ran": [], "errors": []}}

    monkeypatch.setattr(si, "prune", prune)
    with TestClient(create_app(_settings(tmp_path))) as c:
        c.app.state.db_pool = object()
        body = {"areas": {"sweeps": {"older_than_days": 30}}}
        assert c.post("/api/admin/db/prune", json=body).json()["dry_run"] is True
        assert c.post("/api/admin/db/prune", json={**body, "dry_run": "false"}).json()["dry_run"] is True
        assert c.post("/api/admin/db/prune", json={**body, "dry_run": False, "vacuum": False}).status_code == 200
        assert calls[-1] == ({"sweeps": {"older_than_days": 30}}, False, False)
        r = c.post("/api/admin/db/prune", json={"areas": {"sweeps": {"older_than_days": 0}}})
        assert r.status_code == 400 and len(calls) == 3


# --- live Postgres ------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _pool():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        yield pool
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_storage_report_shape_with_a_throwaway_table():
    name = f"zz_test_storage_{uuid.uuid4().hex[:8]}"
    async with _pool() as pool:
        try:
            await pool.execute(f'CREATE TABLE "{name}" (id INT, created_at TIMESTAMPTZ, payload TEXT)')
            await pool.execute(
                f"INSERT INTO \"{name}\" SELECT g, $1::timestamptz + g * INTERVAL '1 day', repeat('x', 3000) "
                "FROM generate_series(1, 50) g",
                OLD,
            )
            report = await si.storage_report(pool)
        finally:
            await pool.execute(f'DROP TABLE IF EXISTS "{name}"')

    assert report["database"]["size_bytes"] > 0 and report["database"]["tables_bytes"] > 0
    areas = {a["id"]: a for a in report["areas"]}
    assert set(areas) == {a.id for a in si.AREAS}
    mine = next(t for t in areas["other"]["tables"] if t["name"] == name)
    assert mine["rows"] == 50 and mine["rows_exact"] is True
    assert mine["total_bytes"] >= mine["table_bytes"] > 0
    assert mine["toast_bytes"] >= 0 and mine["index_bytes"] == 0
    assert mine["age_column"] == "created_at" and mine["oldest"].startswith("1975-01-02")
    for area in report["areas"]:
        assert area["total_bytes"] == sum(t["total_bytes"] for t in area["tables"])
    # The attribute index's parent reports its partitions' sum, never a size of its own.
    nodes = next((t for t in areas["attribute_index"]["tables"] if t["name"] == "attr_nodes"), None)
    if nodes is not None:
        parts = [p for p in report["attribute_partitions"] if p["parent"] == "attr_nodes"]
        assert nodes["partitioned"] and nodes["total_bytes"] == sum(p["total_bytes"] for p in parts)


async def _seed(pool, tag: str) -> dict:
    """One deletable and (where the area has one) one protected row per area, all dated 1975."""
    ids: dict = {}
    q = pool.fetchval
    ids["change_done"] = await q(
        "INSERT INTO asset_change_runs (scope, provider, collection, plugin_id, requested_via, status, created_at) "
        "VALUES ($1, 'p', 'c', 'x', 'user', 'done', $2) RETURNING id",
        tag,
        OLD,
    )
    ids["change_queued"] = await q(
        "INSERT INTO asset_change_runs (scope, provider, collection, plugin_id, requested_via, status, created_at) "
        "VALUES ($1, 'p', 'c', 'x', 'user', 'queued', $2) RETURNING id",
        tag,
        OLD,
    )
    ids["node"] = await q(
        "INSERT INTO source_nodes (scope, source, node_ref, last_changed_at, observed_at) "
        "VALUES ($1, 'test', 'n1', $2, $2) RETURNING id",
        tag,
        OLD,
    )
    for kind, table, items in (
        ("sweep", "asset_sweep_runs", "asset_sweep_items"),
        ("export", "asset_export_runs", None),
    ):
        for status in ("done", "running"):
            if table == "asset_sweep_runs":
                rid = await q(
                    "INSERT INTO asset_sweep_runs (scope, provider, plugin_id, job, requested_via, status, created_at) "
                    "VALUES ($1, 'p', 'x', 'j', 'user', $2, $3) RETURNING id",
                    tag,
                    status,
                    OLD,
                )
            else:
                rid = await q(
                    "INSERT INTO asset_export_runs (scope, target_scope, target_folder, status, created_at) "
                    "VALUES ($1, 'shared', 'f', $2, $3) RETURNING id",
                    tag,
                    status,
                    OLD,
                )
            ids[f"{kind}_{status}"] = rid
            if items:
                for pos in range(3):
                    await pool.execute(
                        f"INSERT INTO {items} (run_id, position, collection) VALUES ($1, $2, 'c')", rid, pos
                    )
    ids["run_done"] = await q(
        "INSERT INTO audit_runs (scope, status, started_at) VALUES ($1, 'finished', $2) RETURNING id", tag, OLD
    )
    ids["run_running"] = await q(
        "INSERT INTO audit_runs (scope, status, started_at) VALUES ($1, 'running', $2) RETURNING id", tag, OLD
    )
    await pool.execute(
        "INSERT INTO audit_parity (ts, audit_run_id, source_key, baseline, counts, consistent) "
        "VALUES ($1, $2, $3, 1, '{}'::jsonb, true)",
        OLD,
        ids["run_done"],
        tag,
    )
    audit = (
        "INSERT INTO audit_log (ts, scope_kind, scope_id, action, status, audit_run_id, metrics_samples) "
        "VALUES ($1, 'shared', $2, 'test-prune', $3, $4, $5::jsonb) RETURNING id"
    )
    samples = json.dumps([{"t": i, "rss": 1000 + i} for i in range(50)])
    ids["audit_done"] = await q(audit, OLD, tag, "done", None, samples)
    ids["audit_running"] = await q(audit, OLD, tag, "running", None, None)
    ids["audit_of_running_run"] = await q(audit, OLD, tag, "done", ids["run_running"], None)
    await pool.execute(
        "INSERT INTO profile_function_stats (audit_id, rank, func, file, line, ncalls, primitive_calls, tottime, cumtime) "
        "VALUES ($1, 1, 'f', 'x.py', 1, 1, 1, 0.1, 0.1)",
        ids["audit_done"],
    )
    ids["schedule_archived"] = await q(
        "INSERT INTO plugin_job_schedules (name, cron_expr, scope, plugin_id, archived_at) "
        "VALUES ($1, '0 2 * * *', 'shared', 'x', $2) RETURNING id",
        f"{tag}-archived",
        OLD,
    )
    ids["schedule_live"] = await q(
        "INSERT INTO plugin_job_schedules (name, cron_expr, scope, plugin_id, created_at) "
        "VALUES ($1, '0 2 * * *', 'shared', 'x', $2) RETURNING id",
        f"{tag}-live",
        OLD,
    )
    return ids


async def _cleanup(pool, tag: str) -> None:
    await pool.execute("DELETE FROM audit_log WHERE scope_id = $1 AND action = 'test-prune'", tag)
    for table in ("asset_change_runs", "asset_sweep_runs", "asset_export_runs", "audit_runs", "source_nodes"):
        await pool.execute(f"DELETE FROM {table} WHERE scope = $1", tag)
    await pool.execute("DELETE FROM plugin_job_schedules WHERE name LIKE $1", f"{tag}-%")


AGE_AREAS = ["change_checks", "change_feed", "sweeps", "exports", "metrics", "audit_log", "audit_runs", "schedules"]


@needs_postgres
@pytest.mark.asyncio
async def test_prune_dry_run_counts_and_the_real_run_deletes_only_what_is_not_in_flight():
    tag = f"test-prune-{uuid.uuid4().hex[:8]}"
    areas = {a: {"older_than_days": OLD_DAYS} for a in AGE_AREAS}
    async with _pool() as pool:
        try:
            ids = await _seed(pool, tag)
            dry = await si.prune(pool, areas, dry_run=True, now=NOW)
            by = {a["id"]: a for a in dry["areas"]}
            assert dry["dry_run"] is True and [a["id"] for a in dry["areas"]] == [
                a for a in [x.id for x in si.AREAS] if a in AGE_AREAS
            ]
            tables = {(a["id"], t["table"]): t["rows"] for a in dry["areas"] for t in a["tables"]}
            assert by["change_checks"]["rows"] == 1 and by["change_checks"]["protected"] == 1
            assert by["change_feed"]["rows"] == 1
            assert tables[("sweeps", "asset_sweep_runs")] == 1 and tables[("sweeps", "asset_sweep_items")] == 3
            assert by["sweeps"]["protected"] == 1 and by["exports"]["rows"] == 1 and by["exports"]["protected"] == 1
            # The running audit row and the row of a running run are both protected.
            assert tables[("audit_log", "audit_log")] == 1 and by["audit_log"]["protected"] == 2
            assert tables[("audit_log", "profile_function_stats")] == 1
            assert tables[("audit_runs", "audit_runs")] == 1 and tables[("audit_runs", "audit_parity")] == 1
            assert tables[("metrics", "profile_function_stats")] == 1 and tables[("metrics", "audit_log")] == 1
            assert by["schedules"]["rows"] == 1
            assert all(a["est_bytes"] >= 0 for a in dry["areas"]) and by["metrics"]["est_bytes"] > 0
            # ...and changed nothing.
            assert (
                await pool.fetchval("SELECT count(*) FROM asset_sweep_items WHERE run_id = $1", ids["sweep_done"]) == 3
            )

            real = await si.prune(pool, areas, dry_run=False, vacuum=False, now=NOW)
            assert real["dry_run"] is False and real["vacuum"] == {"ran": [], "errors": []}
            real_by = {a["id"]: a for a in real["areas"]}
            assert real_by["sweeps"]["rows"] == 4 and real_by["change_checks"]["rows"] == 1
            gone = {
                "asset_change_runs": ids["change_done"],
                "source_nodes": ids["node"],
                "asset_sweep_runs": ids["sweep_done"],
                "asset_export_runs": ids["export_done"],
                "audit_runs": ids["run_done"],
                "audit_log": ids["audit_done"],
                "plugin_job_schedules": ids["schedule_archived"],
            }
            for table, rid in gone.items():
                assert await pool.fetchval(f"SELECT count(*) FROM {table} WHERE id = $1", rid) == 0, table
            kept = {
                "asset_change_runs": ids["change_queued"],
                "asset_sweep_runs": ids["sweep_running"],
                "asset_export_runs": ids["export_running"],
                "audit_runs": ids["run_running"],
                "plugin_job_schedules": ids["schedule_live"],
            }
            for table, rid in kept.items():
                assert await pool.fetchval(f"SELECT count(*) FROM {table} WHERE id = $1", rid) == 1, table
            for key in ("audit_running", "audit_of_running_run"):
                assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE id = $1", ids[key]) == 1
            assert (
                await pool.fetchval("SELECT count(*) FROM asset_sweep_items WHERE run_id = $1", ids["sweep_done"]) == 0
            )
            assert (
                await pool.fetchval("SELECT count(*) FROM asset_sweep_items WHERE run_id = $1", ids["sweep_running"])
                == 3
            )
            # Nothing old enough is left to prune.
            again = await si.prune(pool, areas, dry_run=True, now=NOW)
            assert again["rows"] == 0
        finally:
            await _cleanup(pool, tag)


@needs_postgres
@pytest.mark.asyncio
async def test_one_prune_at_a_time():
    async with _pool() as pool:
        async with pool.acquire() as holder:
            await holder.execute("SELECT pg_advisory_lock($1)", si.PRUNE_LOCK_ID)
            try:
                with pytest.raises(si.PruneBusy):
                    await si.prune(pool, {"sweeps": {"older_than_days": OLD_DAYS}}, now=NOW)
            finally:
                await holder.execute("SELECT pg_advisory_unlock($1)", si.PRUNE_LOCK_ID)


def _index_bytes(provider: str, collection: str, n: int) -> bytes:
    header = {"schema": ai.ATTRIBUTE_INDEX_SCHEMA, "provider": provider, "collection": collection, "revision": "r1"}
    nodes = [
        {"node_id": f"{collection}-{i}", "parent_id": None, "full_name": f"/{collection}/{i}", "kind": "EQUI",
         "depth": 1, "attrs": {"FUNC": "MS", "N": i}}
        for i in range(n)
    ]  # fmt: skip
    return gzip.compress(("\n".join(json.dumps(x) for x in [header, *nodes]) + "\n").encode())


async def _chunks(data: bytes):
    yield data


@needs_postgres
@pytest.mark.asyncio
async def test_attribute_partitions_are_reported_and_dropped_by_age_or_by_choice():
    scope = f"projects/test-dbprune-{uuid.uuid4().hex[:8]}"
    provider = f"test-{uuid.uuid4().hex[:6]}"
    async with _pool() as pool:
        try:
            for coll in ("ALPHA", "BETA", "GAMMA"):
                await ai.ingest_attribute_index(
                    pool, scope=scope, provider=provider, chunks=_chunks(_index_bytes(provider, coll, 20))
                )
            # ALPHA and BETA "were ingested" in 1975; GAMMA just now.
            await pool.execute(
                "UPDATE attr_collections SET ingested_at = $3 WHERE scope = $1 AND provider = $2 AND collection <> 'gamma'",
                scope,
                provider,
                OLD,
            )
            await ai.share_attr_collection(
                pool, scope=scope, provider=provider, collection="alpha", target_scope="shared"
            )
            report = await si.storage_report(pool)
            mine = {p["collection"]: p for p in report["attribute_partitions"] if p["scope"] == scope}
            assert set(mine) == {"alpha", "beta", "gamma"}
            assert mine["alpha"]["shared_with"] == ["shared"] and mine["beta"]["shared_with"] == []
            assert mine["alpha"]["rows"] == 20 and mine["alpha"]["provider"] == provider
            assert mine["alpha"]["total_bytes"] > 0 and mine["alpha"]["parent"] == "attr_nodes"

            by_age = {"attribute_index": {"older_than_days": OLD_DAYS}}
            dry = await si.prune(pool, by_age, dry_run=True, now=NOW)
            [area] = dry["areas"]
            assert sorted(c["collection"] for c in area["collections"]) == ["alpha", "beta"]
            assert area["tables"][0]["rows"] == 40 and area["est_bytes"] > 0
            assert await pool.fetchval("SELECT count(*) FROM attr_collections WHERE scope = $1", scope) == 3

            chosen = {
                "attribute_index": {"collections": [{"scope": scope, "provider": provider, "collection": "BETA"}]}
            }
            real = await si.prune(pool, chosen, dry_run=False, vacuum=False, now=NOW)
            assert [c["collection"] for c in real["areas"][0]["collections"]] == ["beta"]
            assert real["areas"][0]["collections"][0]["dropped"] is True
            left = [
                r["collection"]
                for r in await pool.fetch("SELECT collection FROM attr_collections WHERE scope = $1 ORDER BY 1", scope)
            ]
            assert left == ["alpha", "gamma"]
            assert await pool.fetchval("SELECT to_regclass($1)", mine["beta"]["name"]) is None
        finally:
            for r in await pool.fetch("SELECT provider, collection FROM attr_collections WHERE scope = $1", scope):
                await ai.drop_attr_collection(pool, scope=scope, provider=r["provider"], collection=r["collection"])


class _SettingsPool:
    """``app_settings`` in memory: retention keys are real app settings, which a test must not
    write to a live database."""

    def __init__(self):
        self.rows: dict[str, str] = {}

    async def execute(self, sql, key, value=None, updated_by=None):
        if sql.lstrip().startswith("DELETE"):
            self.rows.pop(key, None)
        else:
            self.rows[key] = value

    async def fetch(self, sql):
        return [{"key": k, "value": v} for k, v in self.rows.items() if k.endswith(".days")]


@pytest.mark.asyncio
async def test_retention_settings_are_checked_and_round_trip():
    pool = _SettingsPool()
    for bad in ({"other": 5}, {"exports": 0}, {"sweeps": "weekly"}):
        with pytest.raises(ValueError):
            await si.set_retention(pool, bad, updated_by="test")
    assert pool.rows == {}  # a bad entry writes nothing, not even the good ones beside it
    assert await si.set_retention(pool, {"exports": 30, "audit_log": "90"}, updated_by="t") == {
        "exports": 30,
        "audit_log": 90,
    }
    assert pool.rows["db.retention.exports.days"] == "30"
    assert await si.set_retention(pool, {"exports": None}, updated_by="t") == {"audit_log": 90}
