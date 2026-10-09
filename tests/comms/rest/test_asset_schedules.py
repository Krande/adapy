"""Provider-declared schedules and change checks (the ``asset_schedules`` asset-provider key).

Always-on tests stub the live specs, the enqueue and the DB; the live-Postgres test (skipped
unless ADA_TEST_POSTGRES_URL is set) runs the migration, the run record and a scheduled firing.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ada.comms.rest import db as dbm
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.routes import asset_schedules as sched
from ada.comms.rest.scope import Scope

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

SPEC = {
    "slug": "vendor-plugin",
    "asset_provider_id": "vendor",
    "asset_collections_field": "projects",
    "projects": ["ALPHA", "BETA"],
    "worker_capability": "vendor",
    "job_options": [
        {"name": "exclude_users", "type": "string_list", "title": "Exclude users"},
        {"name": "discipline", "type": "enum", "enum": ["STRU", "PIPE"], "labels": {"STRU": "Structure"}},
        {"name": "note", "type": "string"},
    ],
    "asset_schedules": [
        {
            "id": "changes",
            "kind": "change-check",
            "label": "Changes",
            "options": {"action": "check-changes", "include_elements": True},
            "collection_option": "project",
            "settings": [{"name": "exclude_users", "choices": "change_users"}, "discipline", "note"],
        },
        {
            "id": "sweep",
            "kind": "job",
            "label": "Sweep",
            "options": {"action": "sweep"},
            "collection_option": "project",
        },
        {"id": "broken", "kind": "change-check", "options": {"action": "x"}},  # no collection_option
        {"id": "sweep", "kind": "job", "options": {"action": "dup"}},  # duplicate id
        "not a dict",
    ],
}


def _settings(tmp_path, *, db_url: str = "") -> Settings:
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
        database_url=db_url,
    )


# --- the declaration --------------------------------------------------------------------------


def test_malformed_entries_are_dropped_not_fatal():
    assert [e["id"] for e in sched.schedule_entries(SPEC)] == ["changes", "sweep"]
    assert sched.schedule_entries({"asset_schedules": "nope"}) == []


def test_find_entry_by_id_and_by_kind():
    assert sched.find_entry([SPEC], "sweep")[1]["options"] == {"action": "sweep"}
    assert sched.find_entry([SPEC], kind="change-check")[1]["id"] == "changes"
    assert sched.find_entry([SPEC], "missing") is None


def test_settings_offer_only_choices_never_free_text():
    _, entry = sched.find_entry([SPEC], "changes")
    settings = {s["name"]: s for s in sched.entry_settings(SPEC, entry)}
    # `note` is a plain string with no choices: not offered at all.
    assert set(settings) == {"exclude_users", "discipline"}
    assert settings["exclude_users"]["source"] == "change_users" and settings["exclude_users"]["type"] == "string_list"
    assert settings["discipline"]["choices"] == [
        {"value": "STRU", "label": "Structure"},
        {"value": "PIPE", "label": "PIPE"},
    ]


@pytest.mark.asyncio
async def test_build_options_validates_collection_and_choices(monkeypatch):
    async def users(pool, **kw):
        return ["batch", "jdoe"]

    monkeypatch.setattr(dbm, "asset_change_users", users)
    _, entry = sched.find_entry([SPEC], "changes")
    kw = dict(scope="shared", provider="vendor")

    options = await sched.build_options(
        None, SPEC, entry, collection="ALPHA", settings={"exclude_users": ["batch"], "discipline": "STRU"}, **kw
    )
    assert options == {
        "action": "check-changes",
        "include_elements": True,
        "project": "ALPHA",
        "exclude_users": ["batch"],
        "discipline": "STRU",
    }
    for bad in (
        dict(collection="NOPE", settings={}),
        dict(collection=None, settings={}),
        dict(collection="ALPHA", settings={"exclude_users": ["someone-else"]}),
        dict(collection="ALPHA", settings={"discipline": "ELEC"}),
        dict(collection="ALPHA", settings={"note": "free text"}),
    ):
        with pytest.raises(HTTPException) as exc:
            await sched.build_options(None, SPEC, entry, **kw, **bad)
        assert exc.value.status_code == 400


def test_settings_round_trip_out_of_stored_options():
    _, entry = sched.find_entry([SPEC], "changes")
    stored = {"action": "check-changes", "project": "ALPHA", "exclude_users": ["batch"], "scheduled_at": "x"}
    assert sched.settings_of(entry, stored) == {"exclude_users": ["batch"]}


def test_job_keys_match_the_enqueue_derivation():
    options = {"action": "a", "project": "ALPHA"}
    key, prefix = sched.job_keys("vendor-plugin", options)
    digest = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()[:16]
    assert key == f"_derived/plugin_jobs/vendor-plugin/{digest}.json" and prefix == key[: -len(".json")]


# --- finishing a run from its job's summary ---------------------------------------------------


class _Ctx:
    def __init__(self, status, blobs):
        self._status, self._blobs = status, blobs
        self.jobs = SimpleNamespace(status=self._job_status)
        self.storage = SimpleNamespace(get_bytes=self._get)

    async def _job_status(self, job_id):
        st = self._status.get(job_id)
        return None if st is None else SimpleNamespace(status=st[0], payload=st[1])

    async def _get(self, scope, key):
        if key not in self._blobs:
            raise FileNotFoundError(key)
        return self._blobs[key]


def _run(**over):
    return {"id": "r1", "status": "queued", "job_id": "j1", "derived_key": "_derived/x.json", **over}


@pytest.mark.asyncio
async def test_finish_copies_the_block_or_names_what_is_wrong(monkeypatch):
    finished = []

    async def fake_finish(pool, run_id, *, status, block=None, error=None):
        finished.append((status, block, error))
        return {"id": run_id, "status": status}

    monkeypatch.setattr(dbm, "finish_asset_change_run", fake_finish)
    block = {"schema": "ada.assets/changes@1", "stale": True, "message": "3 changed"}
    scope = Scope.shared()

    ctx = _Ctx({"j1": ("done", {})}, {"_derived/x.json": json.dumps({"asset_changes": block}).encode()})
    [out] = await sched.finish_pending_runs(ctx, None, scope, [_run()])
    assert out["status"] == "done" and finished[-1] == ("done", block, None)

    ctx = _Ctx({"j1": ("done", {})}, {"_derived/x.json": json.dumps({"other": 1}).encode()})
    await sched.finish_pending_runs(ctx, None, scope, [_run()])
    assert finished[-1][0] == "error" and "asset_changes" in finished[-1][2]

    ctx = _Ctx({"j1": ("error", {"error": "worker blew up"})}, {})
    await sched.finish_pending_runs(ctx, None, scope, [_run()])
    assert finished[-1] == ("error", None, "worker blew up")

    # Still running, or a run already finished: left alone.
    n = len(finished)
    ctx = _Ctx({"j1": ("running", {})}, {})
    await sched.finish_pending_runs(ctx, None, scope, [_run(), _run(id="r2", status="done")])
    assert len(finished) == n


# --- routes -----------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    calls: dict = {"enqueued": [], "runs": []}

    async def fake_specs(ctx):
        return {"vendor": [SPEC]}

    async def fake_enqueue(ctx, **kw):
        calls["enqueued"].append(kw)
        return f"job-{len(calls['enqueued'])}"

    async def fake_insert_run(pool, **kw):
        calls["runs"].append(kw)
        return {"id": "run-1", "status": "queued", **kw}

    async def fake_list_schedules(pool, **kw):
        return calls.get("schedules", [])

    async def fake_users(pool, **kw):
        return ["batch"]

    monkeypatch.setattr(sched, "provider_specs", fake_specs)
    monkeypatch.setattr(sched, "enqueue_plugin_job", fake_enqueue)
    monkeypatch.setattr(dbm, "insert_asset_change_run", fake_insert_run)
    monkeypatch.setattr(dbm, "list_plugin_job_schedules", fake_list_schedules)
    monkeypatch.setattr(dbm, "asset_change_users", fake_users)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as c:
        c.app.state.db_pool = object()
        yield c, calls


def test_check_now_enqueues_the_change_check_and_records_a_run(client):
    c, calls = client
    r = c.post("/api/scopes/shared/asset-changes/check", json={"provider": "vendor", "collection": "ALPHA"})
    assert r.status_code == 202, r.text
    [enq] = calls["enqueued"]
    assert enq["plugin_id"] == "vendor-plugin"
    assert enq["options"]["action"] == "check-changes" and enq["options"]["project"] == "ALPHA"
    assert "requested_at" in enq["options"]
    assert enq["derived_prefix"] and enq["derived_key"] == enq["derived_prefix"] + ".json"
    [run] = calls["runs"]
    assert run["requested_via"] == "user" and run["provider"] == "vendor" and run["collection"] == "ALPHA"
    assert run["derived_key"] == enq["derived_key"] and run["scope"] == Scope.shared().prefix()


def test_check_now_uses_the_scheduled_checks_settings(client):
    c, calls = client
    calls["schedules"] = [
        {
            "scope": "shared",
            "collection": "ALPHA",
            "schedule_job": "changes",
            "options": {"action": "check-changes", "project": "ALPHA", "exclude_users": ["batch"]},
        }
    ]
    c.post("/api/scopes/shared/asset-changes/check", json={"provider": "vendor", "collection": "ALPHA"})
    assert calls["enqueued"][-1]["options"]["exclude_users"] == ["batch"]


def test_check_now_refuses_what_it_cannot_serve(client):
    c, _ = client
    assert c.post("/api/scopes/shared/asset-changes/check", json={"provider": "vendor"}).status_code == 400
    assert (
        c.post("/api/scopes/shared/asset-changes/check", json={"provider": "other", "collection": "A"}).status_code
        == 404
    )


def test_admin_create_takes_choices_only(client, monkeypatch):
    c, _ = client
    created = {}

    async def fake_create(pool, **kw):
        created.update(kw)
        return {"id": "s1", "name": kw["name"]}

    monkeypatch.setattr(dbm, "create_plugin_job_schedule", fake_create)
    body = {
        "provider": "vendor",
        "job": "changes",
        "scope": "shared",
        "collection": "ALPHA",
        "frequency": "daily",
        "settings": {"exclude_users": ["batch"]},
    }
    r = c.post("/api/admin/asset-schedules", json=body)
    assert r.status_code == 201, r.text
    assert created["cron_expr"] == "0 2 * * *" and created["schedule_kind"] == "change-check"
    assert created["asset_provider"] == "vendor" and created["schedule_job"] == "changes"
    assert created["options"]["exclude_users"] == ["batch"] and created["options"]["project"] == "ALPHA"

    assert c.post("/api/admin/asset-schedules", json={**body, "frequency": "*/5 * * * *"}).status_code == 400
    assert c.post("/api/admin/asset-schedules", json={**body, "settings": {"note": "hi"}}).status_code == 400
    assert c.post("/api/admin/asset-schedules", json={**body, "job": "nope"}).status_code == 404


def test_admin_listing_describes_providers_without_free_text(client, monkeypatch):
    c, _ = client

    async def fake_latest(pool, ids):
        return {}

    monkeypatch.setattr(dbm, "latest_asset_change_runs_for_schedules", fake_latest)
    r = c.get("/api/admin/asset-schedules")
    assert r.status_code == 200, r.text
    body = r.json()
    [prov] = body["providers"]
    assert prov["provider"] == "vendor" and prov["collections"] == ["ALPHA", "BETA"]
    assert [j["id"] for j in prov["jobs"]] == ["changes", "sweep"]
    assert {f["id"] for f in body["frequencies"]} >= {"hourly", "daily", "weekly"}


# --- live Postgres -------------------------------------------------------------------------------


@needs_postgres
@pytest.mark.asyncio
async def test_run_record_lifecycle_against_postgres():
    pool = await dbm.init_pool(POSTGRES_URL)
    scope = f"test-{uuid.uuid4().hex[:8]}"
    try:
        run = await dbm.insert_asset_change_run(
            pool,
            scope=scope,
            provider="vendor",
            collection="ALPHA",
            plugin_id="vendor-plugin",
            job_id="j1",
            derived_key="_derived/x.json",
            requested_by="tester",
            requested_via="user",
        )
        assert run["status"] == "queued" and run["has_items"] is False
        block = {
            "stale": True,
            "up_to_date": False,
            "message": "2 changed",
            "counts": {"modified": 2},
            "users": ["jdoe", "batch"],
            "items": [{"node": "n1", "action": "modified"}],
        }
        done = await dbm.finish_asset_change_run(pool, run["id"], status="done", block=block)
        assert done["stale"] is True and done["counts"] == {"modified": 2} and done["items"][0]["node"] == "n1"
        # Idempotent: a second finish does not move a done run.
        assert await dbm.finish_asset_change_run(pool, run["id"], status="error", error="late") is None
        [listed] = await dbm.list_asset_change_runs(pool, scope=scope, provider="vendor", collection="ALPHA")
        assert listed["status"] == "done" and listed["has_items"] is True
        assert await dbm.asset_change_users(pool, scope=scope, provider="vendor", collection="ALPHA") == [
            "batch",
            "jdoe",
        ]
    finally:
        await pool.execute("DELETE FROM asset_change_runs WHERE scope = $1", scope)
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_provider_schedule_columns_round_trip_against_postgres():
    from ada.comms.rest.routes.deps import next_fire

    pool = await dbm.init_pool(POSTGRES_URL)
    name = f"test-{uuid.uuid4().hex[:8]}"
    try:
        row = await dbm.create_plugin_job_schedule(
            pool,
            name=name,
            cron_expr="0 2 * * *",
            scope="shared",
            plugin_id="vendor-plugin",
            options={"action": "check-changes", "project": "ALPHA"},
            next_fire_at=next_fire("0 2 * * *"),
            asset_provider="vendor",
            collection="ALPHA",
            schedule_job="changes",
            schedule_kind="change-check",
        )
        assert row["asset_provider"] == "vendor" and row["schedule_kind"] == "change-check"
        mine = [r for r in await dbm.list_plugin_job_schedules(pool, asset_provider="vendor") if r["id"] == row["id"]]
        assert mine and mine[0]["collection"] == "ALPHA"
    finally:
        await pool.execute("DELETE FROM plugin_job_schedules WHERE name = $1", name)
        await dbm.close_pool(pool)
