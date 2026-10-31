"""Writes go to the provider store (phase 3 of ``docs/design/provider-scope-grants.md``).

Every job of an asset-provider plugin -- an on-demand job, a change check, a sweep, a scheduled
firing -- is an admin operation and runs in ``Scope.providers()``, whatever scope the caller had
open. Schedules and sweeps carry no scope; rows and runs from before the store fire and resume
into it. Scopes read the store's change feed through their grants.

Always-on: the DB, the live specs and the enqueue are stubbed. Nothing here needs Postgres.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

import ada.comms.rest.auth as _auth
from ada.comms.rest import db as dbm
from ada.comms.rest import local_jobs
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.routes import admin_plugin_jobs as apj
from ada.comms.rest.routes import asset_schedules as sched
from ada.comms.rest.routes import asset_sweeps as sweeps
from ada.comms.rest.routes import plugin_jobs as pj
from ada.comms.rest.scope import Scope
from ada.plugins import register_plugin_backend, reset_registry

STORE = Scope.providers()

SPEC = {
    "slug": "vendor-plugin",
    "asset_provider_id": "vendor",
    "asset_collections_field": "projects",
    "projects": ["ALPHA", "BETA"],
    "worker_capability": "vendor",
    "job_options": [{"name": "exclude_users", "type": "string_list"}],
    "asset_schedules": [
        {
            "id": "changes",
            "kind": "change-check",
            "label": "Changes",
            "options": {"action": "check-changes"},
            "collection_option": "project",
            "settings": [{"name": "exclude_users", "choices": "change_users"}],
        },
        {
            "id": "project-sweep",
            "kind": "sweep",
            "label": "Every project",
            "options": {"action": "export-project"},
            "collection_option": "project",
        },
    ],
}


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


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def as_member(monkeypatch):
    """The next requests run as a NON-admin (auth is disabled, so every request is local_dev)."""
    monkeypatch.setattr(
        _auth.User,
        "local_dev",
        classmethod(lambda cls: cls(sub="member", email="m@x", display_name="M", groups=frozenset(), is_admin=False)),
    )


def _entrypoint(options, *, storage, scope, on_progress, derived_prefix, **_kw):
    return {"ok": True, "scope": scope.prefix()}


# --- POST /plugins/{id}/jobs ------------------------------------------------------------------


def test_a_member_cannot_run_an_asset_providers_job_even_without_requires_admin(tmp_path, as_member):
    register_plugin_backend("vendor-plugin", job_entrypoint=f"{__name__}:_entrypoint", asset_provider_id="vendor")
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as client:
        for scope in ("shared", "user:me", "providers"):
            r = client.post(f"/api/plugins/vendor-plugin/jobs?scope={scope}", json={"options": {}})
            assert r.status_code == 403, (scope, r.text)
            assert "admin operations" in r.json()["detail"]


def test_an_admins_provider_job_runs_in_the_store_whatever_scope_was_named(tmp_path):
    register_plugin_backend("vendor-plugin", job_entrypoint=f"{__name__}:_entrypoint", asset_provider_id="vendor")
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as client:
        r = client.post("/api/plugins/vendor-plugin/jobs?scope=user:me", json={"options": {"a": 1}})
        assert r.status_code == 200, r.text
        body = r.json()
        # The client reads the summary at `derived_key` from the scope it is told.
        assert body["scope"] == "providers"
        assert local_jobs.registry.get(body["job_id"]).scope_kind == "providers"
        status = client.get(f"/api/convert/{body['job_id']}")
        assert status.status_code == 200, status.text


def test_a_plain_plugins_job_is_unchanged_and_says_its_scope(tmp_path, as_member):
    register_plugin_backend("plain-plugin", job_entrypoint=f"{__name__}:_entrypoint")
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as client:
        r = client.post("/api/plugins/plain-plugin/jobs?scope=shared", json={"options": {}})
        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "shared"
        assert local_jobs.registry.get(r.json()["job_id"]).scope_kind == "shared"


def test_enqueue_forces_the_store_for_an_asset_provider_whatever_the_caller_passed():
    submitted = []

    async def submit(req, before_dispatch=None):
        submitted.append(req)
        return SimpleNamespace(job_id="j1")

    async def advertised(kind):
        return {"vendor-plugin": SPEC}

    async def audit(*a, **k):
        return None

    ctx = SimpleNamespace(jobs=SimpleNamespace(submit=submit, advertised_specs=advertised), audit=audit)
    for scope in (Scope.shared(), Scope.project("p-1"), Scope.user("u-1")):
        asyncio.run(pj.enqueue_plugin_job(ctx, plugin_id="vendor-plugin", options={}, scope_obj=scope, user=None))
        assert submitted[-1].scope == STORE
    # A plain plugin keeps the scope it was given.
    asyncio.run(
        pj.enqueue_plugin_job(
            ctx, plugin_id="plain", options={}, scope_obj=Scope.shared(), user=None, plugin_spec={"slug": "plain"}
        )
    )
    assert submitted[-1].scope == Scope.shared()


# --- GET /convert/{job_id} for a store job -----------------------------------------------------


def _store_local_job(derived_key: str) -> str:
    job = local_jobs.LocalJob(
        job_id=f"local-test-{abs(hash(derived_key)) % 10**8}",
        plugin_id="x",
        scope_kind="providers",
        scope_id=None,
        derived_key=derived_key,
    )
    local_jobs.registry.add(job)
    return job.job_id


def test_a_member_polls_a_store_build_but_not_a_store_provider_job(tmp_path, as_member):
    build = _store_local_job("_derived/assets/vendor/alpha/build/out.json")
    job = _store_local_job("_derived/plugin_jobs/vendor-plugin/abc.json")
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as client:
        assert client.get(f"/api/convert/{build}").status_code == 200
        assert client.get(f"/api/convert/{job}").status_code == 403


def test_an_admin_polls_any_store_job(tmp_path):
    job = _store_local_job("_derived/plugin_jobs/vendor-plugin/def.json")
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as client:
        assert client.get(f"/api/convert/{job}").status_code == 200


# --- Admin -> Providers -> Scheduled jobs: no scope ---------------------------------------------


@pytest.fixture
def sched_client(tmp_path, monkeypatch):
    calls: dict = {"enqueued": [], "runs": [], "schedules": [], "created": []}

    async def fake_specs(ctx):
        return {"vendor": [SPEC]}

    async def fake_enqueue(ctx, **kw):
        calls["enqueued"].append(kw)
        return f"job-{len(calls['enqueued'])}"

    async def fake_insert_run(pool, **kw):
        calls["runs"].append(kw)
        return {"id": "run-1", "status": "queued", **kw}

    async def fake_list_schedules(pool, **kw):
        return calls["schedules"]

    async def fake_users(pool, **kw):
        calls["users_scope"] = kw["scope"]
        return ["batch"]

    async def fake_create(pool, **kw):
        calls["created"].append(kw)
        return {"id": "s1", "name": kw["name"], "scope": kw["scope"]}

    monkeypatch.setattr(sched, "provider_specs", fake_specs)
    monkeypatch.setattr(sched, "enqueue_plugin_job", fake_enqueue)
    monkeypatch.setattr(dbm, "insert_asset_change_run", fake_insert_run)
    monkeypatch.setattr(dbm, "list_plugin_job_schedules", fake_list_schedules)
    monkeypatch.setattr(dbm, "asset_change_users", fake_users)
    monkeypatch.setattr(dbm, "create_plugin_job_schedule", fake_create)
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as c:
        c.app.state.db_pool = object()
        yield c, calls


def test_a_schedule_is_made_without_a_scope_and_stored_in_the_store(sched_client):
    c, calls = sched_client
    body = {"provider": "vendor", "job": "changes", "collection": "ALPHA", "frequency": "daily"}
    r = c.post("/api/admin/asset-schedules", json=body)
    assert r.status_code == 201, r.text
    [created] = calls["created"]
    assert created["scope"] == "providers"
    assert created["name"] == "vendor: Changes -- ALPHA"
    # A scope from an older client is accepted and ignored.
    calls["created"].clear()
    assert c.post("/api/admin/asset-schedules", json={**body, "scope": "project:p-1"}).status_code == 201
    assert calls["created"][0]["scope"] == "providers"


def test_a_second_schedule_of_the_same_job_and_collection_is_refused(sched_client):
    c, calls = sched_client
    # A row from before the store, under a scope: still the one live schedule of that job.
    calls["schedules"] = [{"scope": "shared", "collection": "ALPHA", "schedule_job": "changes", "options": {}}]
    r = c.post(
        "/api/admin/asset-schedules",
        json={"provider": "vendor", "job": "changes", "collection": "ALPHA", "frequency": "daily"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "that job is already scheduled for this collection"
    assert calls["created"] == []


def test_choices_need_no_scope_and_read_the_stores_change_users(sched_client):
    c, calls = sched_client
    r = c.get("/api/admin/asset-schedules/choices?provider=vendor&job=changes&collection=ALPHA")
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["exclude_users"] == [{"value": "batch", "label": "batch"}]
    assert calls["users_scope"] == "providers"


def test_the_listing_reports_every_provider_row_in_the_store(sched_client, monkeypatch):
    c, calls = sched_client

    async def no_runs(pool, ids):
        return {}

    monkeypatch.setattr(dbm, "latest_asset_change_runs_for_schedules", no_runs)
    calls["schedules"] = [
        {
            "id": "s-old",
            "name": "vendor: Changes -- shared / ALPHA",
            "scope": "project:p-1",
            "cron_expr": "0 2 * * *",
            "plugin_id": "vendor-plugin",
            "options": {"action": "check-changes", "project": "ALPHA"},
            "enabled": True,
            "last_fired_at": None,
            "next_fire_at": None,
            "last_skipped_reason": None,
            "last_job_id": None,
            "asset_provider": "vendor",
            "collection": "ALPHA",
            "schedule_job": "changes",
            "schedule_kind": "change-check",
        }
    ]
    [row] = c.get("/api/admin/asset-schedules").json()["schedules"]
    assert row["scope"] == "providers"


def test_check_now_is_an_admins_and_runs_in_the_store(sched_client, monkeypatch):
    c, calls = sched_client
    r = c.post("/api/scopes/shared/asset-changes/check", json={"provider": "vendor", "collection": "ALPHA"})
    assert r.status_code == 202, r.text
    assert calls["enqueued"][-1]["scope_obj"] == STORE and calls["runs"][-1]["scope"] == "providers"

    monkeypatch.setattr(
        _auth.User,
        "local_dev",
        classmethod(lambda cls: cls(sub="member", email="m@x", display_name="M", groups=frozenset(), is_admin=False)),
    )
    r = c.post("/api/scopes/shared/asset-changes/check", json={"provider": "vendor", "collection": "ALPHA"})
    assert r.status_code == 403 and "admin" in r.json()["detail"]


# --- change-check runs read through grants ------------------------------------------------------


def _run(rid: str, scope: str, collection: str = "ALPHA", created: str = "2026-10-01T00:00:00") -> dict:
    return {
        "id": rid,
        "scope": scope,
        "provider": "vendor",
        "collection": collection,
        "status": "done",
        "job_id": None,
        "created_at": created,
        "items": [{"node": rid}],
        "items_key": None,
        "derived_key": None,
    }


@pytest.fixture
def runs_client(tmp_path, monkeypatch, as_member):
    state: dict = {"grants": [], "runs": [_run("own", "shared", created="2026-10-02"), _run("store", "providers")]}
    state["runs"].append(_run("store-beta", "providers", collection="BETA"))

    async def grants(pool, *, target_scope=None, provider=None, collection=None):
        return [g for g in state["grants"] if g["target_scope"] == target_scope]

    async def list_runs(pool, *, scope, provider=None, collection=None, limit=50):
        return [
            r
            for r in state["runs"]
            if r["scope"] == scope
            and (provider is None or r["provider"] == provider)
            and (collection is None or r["collection"] == collection)
        ]

    async def get_run(pool, run_id):
        return next((r for r in state["runs"] if r["id"] == run_id), None)

    monkeypatch.setattr(dbm, "list_provider_grants", grants)
    monkeypatch.setattr(dbm, "list_asset_change_runs", list_runs)
    monkeypatch.setattr(dbm, "get_asset_change_run", get_run)
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as c:
        c.app.state.db_pool = object()
        yield c, state


def _grant(collection, *, attributes=True, geometry=False, request=False):
    return {
        "target_scope": "shared",
        "provider": "vendor",
        "collection": collection,
        "attributes": attributes,
        "geometry": geometry,
        "request": request,
    }


def test_a_scope_without_a_grant_lists_only_its_own_runs(runs_client):
    c, _ = runs_client
    assert [r["id"] for r in c.get("/api/scopes/shared/asset-changes/runs").json()["runs"]] == ["own"]
    assert c.get("/api/scopes/shared/asset-changes/runs/store/items").status_code == 404


def test_a_granted_scope_lists_the_stores_runs_of_its_datasets(runs_client):
    c, state = runs_client
    state["grants"] = [_grant("alpha")]
    ids = [r["id"] for r in c.get("/api/scopes/shared/asset-changes/runs").json()["runs"]]
    assert ids == ["own", "store"]  # newest first; BETA is not granted
    r = c.get("/api/scopes/shared/asset-changes/runs/store/items")
    assert r.status_code == 200 and r.json()["items"] == [{"node": "store"}]
    assert c.get("/api/scopes/shared/asset-changes/runs/store-beta/items").status_code == 404


def test_a_request_only_grant_reads_no_runs_and_a_provider_wide_one_reads_all(runs_client):
    c, state = runs_client
    state["grants"] = [_grant(None, attributes=False, request=True)]
    assert [r["id"] for r in c.get("/api/scopes/shared/asset-changes/runs").json()["runs"]] == ["own"]
    state["grants"] = [_grant(None, attributes=False, geometry=True)]
    ids = {r["id"] for r in c.get("/api/scopes/shared/asset-changes/runs").json()["runs"]}
    assert ids == {"own", "store", "store-beta"}


# --- the scheduler: old rows fire into the store ------------------------------------------------


def _fire_world(monkeypatch, row: dict):
    seen: dict = {"enqueued": [], "skips": [], "runs": []}

    async def advertised(kind):
        return {"vendor-plugin": SPEC}

    async def status(job_id):
        return None

    async def in_flight(pool, **kw):
        seen["guard"] = kw
        return []

    async def skip(pool, sched_id, reason):
        seen["skips"].append(reason)

    async def update(pool, sched_id, **kw):
        return {}

    async def enqueue(ctx, **kw):
        seen["enqueued"].append(kw)
        return "job-1"

    async def insert_run(pool, **kw):
        seen["runs"].append(kw)
        return {}

    async def provider_specs(ctx):
        return {"vendor": [SPEC]}

    monkeypatch.setattr(dbm, "plugin_job_in_flight_jobs", in_flight)
    monkeypatch.setattr(dbm, "set_plugin_job_schedule_skip_reason", skip)
    monkeypatch.setattr(dbm, "update_plugin_job_schedule", update)
    monkeypatch.setattr(dbm, "insert_asset_change_run", insert_run)
    monkeypatch.setattr(apj, "enqueue_plugin_job", enqueue)
    monkeypatch.setattr(sched, "provider_specs", provider_specs)
    ctx = SimpleNamespace(jobs=SimpleNamespace(advertised_specs=advertised, status=status))
    from datetime import datetime, timezone

    outcome = asyncio.run(apj.plugin_schedule_fire(ctx, object(), row, fired_at=datetime.now(timezone.utc)))
    return outcome, seen


def _row(**over):
    return {
        "id": "s1",
        "name": "old",
        # A project that no longer resolves: the row must fire into the store regardless.
        "scope": "project:gone-project",
        "plugin_id": "vendor-plugin",
        "options": {"action": "check-changes", "project": "ALPHA"},
        "capability": None,
        "asset_provider": "vendor",
        "collection": "ALPHA",
        "schedule_job": "changes",
        "schedule_kind": "change-check",
        **over,
    }


def test_an_old_provider_schedule_fires_into_the_store_and_keys_its_run_there(monkeypatch):
    outcome, seen = _fire_world(monkeypatch, _row())
    assert outcome.get("job_id") == "job-1", outcome
    assert seen["enqueued"][0]["scope_obj"] == STORE
    # The in-flight guard is per plugin, not per scope.
    assert seen["guard"]["any_scope"] is True
    [run] = seen["runs"]
    assert run["scope"] == "providers" and run["schedule_id"] == "s1"


def test_a_plain_schedule_of_an_asset_provider_plugin_fires_into_the_store(monkeypatch):
    row = _row(asset_provider=None, collection=None, schedule_job=None, schedule_kind=None, scope="shared")
    outcome, seen = _fire_world(monkeypatch, row)
    assert outcome.get("job_id") == "job-1", outcome
    assert seen["enqueued"][0]["scope_obj"] == STORE and seen["guard"]["any_scope"] is True


def test_record_scheduled_run_keys_a_provider_run_under_the_store(monkeypatch):
    runs = []

    async def insert_run(pool, **kw):
        runs.append(kw)

    monkeypatch.setattr(dbm, "insert_asset_change_run", insert_run)
    asyncio.run(sched.record_scheduled_run(None, _row(), scope_obj=Scope.shared(), job_id="j", derived_key="k"))
    assert runs[0]["scope"] == "providers"


# --- sweeps ---------------------------------------------------------------------------------------


def test_an_admin_sweep_needs_no_scope_and_is_recorded_in_the_store(tmp_path, monkeypatch):
    started = {}

    async def fake_start(ctx, pool, **kw):
        started.update(kw)
        return {"id": "r1"}

    async def specs(ctx):
        return {"vendor": [SPEC]}

    monkeypatch.setattr(sweeps, "start_sweep", fake_start)
    monkeypatch.setattr(sweeps, "provider_specs", specs)
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as c:
        c.app.state.db_pool = object()
        r = c.post("/api/admin/asset-sweeps", json={"provider": "vendor", "job": "project-sweep"})
        assert r.status_code == 202, r.text
        assert started["scope"] == "providers" and started["scope_obj"] == STORE
        r = c.post("/api/admin/asset-sweeps", json={"provider": "vendor", "job": "project-sweep", "scope": "user:me"})
        assert r.status_code == 202 and started["scope_obj"] == STORE


def test_start_sweep_drives_in_the_store_whatever_scope_it_is_handed(monkeypatch):
    spawned = {}

    async def specs(ctx):
        return {"vendor": [SPEC]}

    async def idle(pool, provider):
        return None

    async def setting(pool, key):
        return None

    async def insert(pool, **kw):
        return {"id": "r1", **kw}

    def spawn(coro, name):
        spawned["scope_obj"] = coro.cr_frame.f_locals.get("scope_obj")
        coro.close()

    monkeypatch.setattr(sweeps, "provider_specs", specs)
    monkeypatch.setattr(sweeps, "spawn", spawn)
    monkeypatch.setattr(dbm, "running_asset_sweep", idle)
    monkeypatch.setattr(dbm, "get_setting", setting)
    monkeypatch.setattr(dbm, "insert_asset_sweep_run", insert)
    run = asyncio.run(
        sweeps.start_sweep(
            None,
            None,
            scope="project:p-1",
            scope_obj=Scope.project("p-1"),
            provider="vendor",
            job="project-sweep",
            settings={},
            user=None,
            via="user",
        )
    )
    # Recorded under the requesting scope (it polls by it), driven in the store.
    assert run["scope"] == "project:p-1" and spawned["scope_obj"] == STORE


def test_an_old_sweep_resumes_in_the_store(monkeypatch):
    spawned = {}

    async def get_run(pool, run_id):
        return {
            "id": "r1",
            "provider": "vendor",
            "plugin_id": "vendor-plugin",
            "scope": "project:gone-project",
            "job": "project-sweep",
            "status": "running" if spawned.get("rearmed") else "interrupted",
            "settings": {},
            "items": [{"position": 0, "collection": "ALPHA", "status": "pending"}],
        }

    async def specs(ctx):
        return {"vendor": [SPEC]}

    async def idle(pool, provider):
        return None

    async def rearm(pool, run_id):
        spawned["rearmed"] = True
        return []

    def spawn(coro, name):
        spawned["scope_obj"] = coro.cr_frame.f_locals.get("scope_obj")
        coro.close()

    monkeypatch.setattr(dbm, "get_asset_sweep_run", get_run)
    monkeypatch.setattr(dbm, "running_asset_sweep", idle)
    monkeypatch.setattr(dbm, "resume_asset_sweep_run", rearm)
    monkeypatch.setattr(sweeps, "provider_specs", specs)
    monkeypatch.setattr(sweeps, "spawn", spawn)
    asyncio.run(sweeps.resume_sweep(SimpleNamespace(jobs=None), object(), "r1", user=None))
    assert spawned["scope_obj"] == STORE


def test_provider_options_are_read_from_the_store_first():
    blobs = {
        ("providers", "assets/_options/alpha.json"): {"providers": {"vendor": {"from": "store"}}},
        ("shared", "assets/_options/alpha.json"): {"providers": {"vendor": {"from": "scope"}}},
        ("shared", "assets/_options/beta.json"): {"providers": {"vendor": {"from": "scope"}}},
    }

    async def get_bytes(scope, key):
        doc = blobs.get((scope.prefix(), key))
        if doc is None:
            raise FileNotFoundError(key)
        return json.dumps(doc).encode()

    ctx = SimpleNamespace(storage=SimpleNamespace(get_bytes=get_bytes))
    read = sweeps._provider_options
    assert asyncio.run(read(ctx, Scope.shared(), "vendor", "ALPHA")) == {"from": "store"}
    # Until the store holds the collection's options, a scope's copy from before it answers.
    assert asyncio.run(read(ctx, Scope.shared(), "vendor", "BETA")) == {"from": "scope"}
    assert asyncio.run(read(ctx, STORE, "vendor", "BETA")) == {}


# --- the change feed ------------------------------------------------------------------------------


@pytest.fixture
def feed_client(tmp_path, monkeypatch):
    state: dict = {"grants": [], "recorded": []}

    def _src(name, nodes):
        return {"source": name, "nodes": nodes, "last_changed_at": None, "observed_at": None}

    async def sources(pool, *, scope):
        return {
            "providers": [_src("vendor:ALPHA", 10), _src("vendor:BETA", 5)],
            "shared": [_src("vendor:ALPHA", 1), _src("local:thing", 2)],
        }.get(scope, [])

    async def record(pool, *, scope, source, nodes):
        state["recorded"].append(scope)
        return len(nodes)

    async def changed(pool, *, scope, source, since, limit):
        state["read_key"] = scope
        return []

    async def grants(pool, *, target_scope=None, provider=None, collection=None):
        return [g for g in state["grants"] if g["target_scope"] == target_scope]

    monkeypatch.setattr(dbm, "list_source_node_sources", sources)
    monkeypatch.setattr(dbm, "record_source_nodes", record)
    monkeypatch.setattr(dbm, "list_source_nodes_changed_since", changed)
    monkeypatch.setattr(dbm, "list_provider_grants", grants)
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as c:
        c.app.state.db_pool = object()
        yield c, state


def test_an_admin_token_records_the_stores_feed(feed_client):
    c, state = feed_client
    r = c.post("/api/scopes/providers/source-nodes", json={"source": "vendor:ALPHA", "nodes": []})
    assert r.status_code == 200, r.text
    assert state["recorded"] == ["providers"]


def test_a_member_token_cannot_write_or_read_the_store_feed_directly(feed_client, as_member):
    c, state = feed_client
    r = c.post("/api/scopes/providers/source-nodes", json={"source": "vendor:ALPHA", "nodes": []})
    assert r.status_code == 403 and "ADMIN token" in r.json()["detail"]
    assert c.get("/api/scopes/providers/source-nodes").status_code == 403
    assert state["recorded"] == []


def test_a_granted_scope_reads_the_stores_feed_and_its_own_otherwise(feed_client, as_member):
    c, state = feed_client
    listing = c.get("/api/scopes/shared/source-nodes").json()["sources"]
    assert {s["source"]: s["nodes"] for s in listing} == {"vendor:ALPHA": 1, "local:thing": 2}

    state["grants"] = [_grant("alpha")]
    listing = c.get("/api/scopes/shared/source-nodes").json()["sources"]
    by_name = {s["source"]: s for s in listing}
    # The store answers the granted source (shadowing the scope's own copy); BETA is not granted.
    assert set(by_name) == {"vendor:ALPHA", "local:thing"}
    assert by_name["vendor:ALPHA"]["nodes"] == 10 and by_name["vendor:ALPHA"]["read_from"] == "providers"

    body = c.get("/api/scopes/shared/source-nodes?source=vendor:ALPHA").json()
    assert state["read_key"] == "providers" and body["read_from"] == "providers"
    c.get("/api/scopes/shared/source-nodes?source=local:thing")
    assert state["read_key"] == "shared"
