"""Provider sweeps (the ``sweep`` kind of ``asset_schedules``): every collection, one job each,
each published, every phase timed.

Always-on tests drive the sweep with a fake transport, storage and DB; the live-Postgres test
(skipped unless ADA_TEST_POSTGRES_URL is set) runs migration 036's tables.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import uuid
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest

from ada.comms.rest import db as dbm
from ada.comms.rest.routes import asset_schedules as sched
from ada.comms.rest.routes import asset_sweeps as sweeps
from ada.comms.rest.scope import Scope

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

SWEEP_ENTRY = {
    "id": "project-sweep",
    "kind": "sweep",
    "label": "Export every project",
    "options": {"action": "export-project"},
    "collection_option": "project",
    "settings": [{"name": "sweep_target"}],
}
SPEC = {
    "slug": "vendor-plugin",
    "asset_provider_id": "vendor",
    "asset_collections_field": "projects",
    "projects": ["ALPHA", "BETA"],
    "worker_capability": "vendor",
    "capability_option": "project",
    "job_options": [{"name": "sweep_target", "type": "enum", "enum": ["tree", "geom"]}],
    "asset_schedules": [SWEEP_ENTRY, {"id": "loose", "kind": "sweep", "options": {}}],  # no collection_option
}
# A second worker of the same provider serving another collection.
SPEC_2 = {**SPEC, "projects": ["BETA", "GAMMA"]}


# --- the declaration --------------------------------------------------------------------------


def test_a_sweep_needs_a_collection_option_and_its_row_names_no_collection():
    assert [e["id"] for e in sched.schedule_entries(SPEC)] == ["project-sweep"]


@pytest.mark.asyncio
async def test_build_options_for_a_sweep_row_and_for_each_collection():
    kw = dict(scope="shared", provider="vendor", settings={"sweep_target": "tree"})
    row = await sched.build_options(None, SPEC, SWEEP_ENTRY, collection=None, **kw)
    assert row == {"action": "export-project", "sweep_target": "tree"}
    one = await sched.build_options(None, SPEC, SWEEP_ENTRY, collection="ALPHA", **kw)
    assert one == {"action": "export-project", "project": "ALPHA", "sweep_target": "tree"}


def test_every_collection_of_every_worker_in_first_seen_order():
    assert sweeps._all_collections([SPEC, SPEC_2]) == ["ALPHA", "BETA", "GAMMA"]


# --- driving one sweep ------------------------------------------------------------------------


class _World:
    """A transport, storage and DB that answer like a finished provider job and publish."""

    def __init__(self, *, fetch_summaries: dict[str, dict], fail_publish: bool = False):
        self.fetch_summaries = fetch_summaries  # collection -> provider summary
        self.fail_publish = fail_publish
        self.blobs: dict[str, bytes] = {}
        self.jobs_status: dict[str, str] = {}
        self.enqueued: list[dict] = []
        self.published: list[dict] = []
        self.items: dict[int, dict] = {}
        self.run_status = "running"
        self.excluded: set[int] = set()
        self.finished = None
        self.ctx = SimpleNamespace(
            jobs=SimpleNamespace(status=self._status, cancel=self._cancel),
            storage=SimpleNamespace(get_bytes=self._get, list_prefix=self._list),
        )

    async def _status(self, job_id):
        st = self.jobs_status.get(job_id)
        return None if st is None else SimpleNamespace(status=st, payload={"error": "boom"} if st == "error" else {})

    async def _cancel(self, job_id):
        self.jobs_status[job_id] = "cancelled"
        return True

    async def _get(self, scope, key):
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]

    async def _list(self, scope, prefix):
        return [SimpleNamespace(key=k) for k in self.blobs if k.startswith(prefix)]

    async def enqueue(self, ctx, **kw):
        self.enqueued.append(kw)
        job_id = f"fetch-{len(self.enqueued)}"
        collection = kw["options"]["project"]
        self.blobs[kw["derived_key"]] = gzip.compress(json.dumps(self.fetch_summaries[collection]).encode())
        staging = self.fetch_summaries[collection].get("asset_staging_id")
        if staging:
            self.blobs[f"assets/_staging/{staging}/csg.db"] = b"x"
        self.jobs_status[job_id] = "done"
        return job_id

    async def publish(self, ctx, scope_obj, *, provider, staged, user, **kw):
        self.published.append({"provider": provider, "staged": staged, "user": user})
        job_id, key = f"publish-{len(self.published)}", f"_derived/pub/{len(self.published)}.json"
        self.jobs_status[job_id] = "error" if self.fail_publish else "done"
        self.blobs[key] = json.dumps({"revision": "r1", "timings": {"derive_ms": 5, "write_ms": 7}}).encode()
        return job_id, key

    async def update_item(self, pool, run_id, position, **fields):
        self.items.setdefault(position, {}).update(fields)

    async def heartbeat(self, pool, run_id):
        return self.run_status

    async def item_status(self, pool, run_id, position):
        # Positions in `excluded` were ticked off; every other item is pending until it ran.
        return "excluded" if position in self.excluded else self.items.get(position, {}).get("status", "pending")

    async def finish(self, pool, run_id, *, status, error=None):
        self.finished = (status, error)

    async def skip(self, pool, run_id, reason):
        for item in self.items.values():
            item.setdefault("status", "skipped")


@pytest.fixture
def world(monkeypatch):
    def make(**kw) -> _World:
        w = _World(**kw)
        monkeypatch.setattr(sweeps, "enqueue_plugin_job", w.enqueue)
        monkeypatch.setattr(sweeps, "submit_asset_publish", w.publish)
        monkeypatch.setattr(dbm, "update_asset_sweep_item", w.update_item)
        monkeypatch.setattr(dbm, "heartbeat_asset_sweep_run", w.heartbeat)
        monkeypatch.setattr(dbm, "finish_asset_sweep_run", w.finish)
        monkeypatch.setattr(dbm, "skip_pending_asset_sweep_items", w.skip)
        monkeypatch.setattr(dbm, "asset_sweep_item_status", w.item_status)
        monkeypatch.setattr(sweeps, "POLL_INTERVAL_S", 0)
        return w

    return make


RUN = {"id": "run-1", "provider": "vendor", "plugin_id": "vendor-plugin", "settings": {"sweep_target": "tree"}}
PROVIDER_TIMINGS = {"login_ms": 20000, "export_ms": 3000, "stage_ms": 200, "total_ms": 23300}


@pytest.mark.asyncio
async def test_a_sweep_fetches_and_publishes_each_collection_and_times_every_phase(world):
    w = world(
        fetch_summaries={
            "ALPHA": {"asset_staging_id": "s-a", "timings": PROVIDER_TIMINGS, "login": {"mode": "booted"}},
            "BETA": {"asset_unchanged": True, "revision": "r0", "message": "same as before"},
        }
    )
    w.blobs["assets/_options/alpha.json"] = json.dumps(
        {"providers": {"vendor": {"def_dbs": ["TEAM/DB"]}, "other": {"x": 1}}}
    ).encode()
    user = SimpleNamespace(sub="admin")
    await sweeps._drive(
        w.ctx,
        None,
        RUN,
        scope_obj=Scope.shared(),
        spec=SPEC,
        entry=SWEEP_ENTRY,
        collections=["ALPHA", "BETA"],
        user=user,
    )

    alpha, beta = (e["options"] for e in w.enqueued)
    assert alpha["action"] == "export-project" and alpha["project"] == "ALPHA" and alpha["sweep_target"] == "tree"
    assert alpha["def_dbs"] == ["TEAM/DB"] and "x" not in alpha  # its own provider's saved options only
    assert "requested_at" in alpha and "def_dbs" not in beta
    assert w.enqueued[0]["derived_key"] == sched.job_keys("vendor-plugin", alpha)[0]
    # No derived_prefix: the provider stages under assets/_staging/ itself, as for a Sources-tab
    # request. One outside it was refused after a whole-project export.
    assert "derived_prefix" not in w.enqueued[0]

    [pub] = w.published  # BETA was unchanged: nothing staged, nothing published
    assert pub["staged"] == {"csg.db": "assets/_staging/s-a/csg.db"} and pub["user"] is user

    a, b = w.items[0], w.items[1]
    assert a["status"] == "done" and a["revision"] == "r1"
    t = a["timings"]
    assert t["provider"] == PROVIDER_TIMINGS and t["login"] == {"mode": "booted"}
    assert t["publish"] == {"derive_ms": 5, "write_ms": 7}
    assert {"queued_ms", "fetch_ms", "publish_ms", "total_ms"} <= set(t)
    assert b["status"] == "unchanged" and b["message"] == "same as before"
    assert w.finished == ("done", None)


@pytest.mark.asyncio
async def test_a_bool_setting_reaches_every_job_as_a_bool(world):
    entry = {**SWEEP_ENTRY, "settings": [{"name": "sweep_target"}, {"name": "deep"}]}
    spec = {
        **SPEC,
        "job_options": SPEC["job_options"]
        + [{"name": "deep", "type": "bool", "enum": [False, True], "labels": {"False": "Shallow", "True": "Deep"}}],
        "asset_schedules": [entry],
    }
    w = world(fetch_summaries={"ALPHA": {"asset_unchanged": True}})
    # As an older run row might hold it: the text spelling.
    run = {**RUN, "settings": {"sweep_target": "tree", "deep": "false"}}
    await sweeps._drive(
        w.ctx, None, run, scope_obj=Scope.shared(), spec=spec, entry=entry, collections=["ALPHA"], user=None
    )
    [opts] = (e["options"] for e in w.enqueued)
    assert opts["deep"] is False and opts["sweep_target"] == "tree"


@pytest.mark.asyncio
async def test_one_failed_collection_is_its_row_not_the_sweep(world):
    w = world(
        fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}, "BETA": {"asset_staging_id": "s-b"}},
        fail_publish=True,
    )
    await sweeps._drive(
        w.ctx,
        None,
        RUN,
        scope_obj=Scope.shared(),
        spec=SPEC,
        entry=SWEEP_ENTRY,
        collections=["ALPHA", "BETA"],
        user=SimpleNamespace(sub="system"),
    )
    assert [w.items[i]["status"] for i in (0, 1)] == ["error", "error"]
    assert w.items[0]["error"] == "boom" and len(w.enqueued) == 2  # BETA still ran
    assert w.finished == ("error", "2 of 2 collection(s) failed")


@pytest.mark.asyncio
async def test_a_summary_without_a_staging_id_is_an_error_naming_why(world):
    w = world(fetch_summaries={"ALPHA": {"something": "else"}})
    await sweeps._drive(
        w.ctx, None, RUN, scope_obj=Scope.shared(), spec=SPEC, entry=SWEEP_ENTRY, collections=["ALPHA"], user=None
    )
    assert w.items[0]["status"] == "error" and "asset_staging_id" in w.items[0]["error"]


@pytest.mark.asyncio
async def test_a_cancelled_sweep_stops_before_the_next_collection(world):
    w = world(fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}})
    w.run_status = "cancelled"
    await sweeps._drive(
        w.ctx, None, RUN, scope_obj=Scope.shared(), spec=SPEC, entry=SWEEP_ENTRY, collections=["ALPHA"], user=None
    )
    assert w.enqueued == [] and w.finished is None


@pytest.mark.asyncio
async def test_a_collection_ticked_off_is_passed_over_and_is_not_a_failure(world):
    w = world(fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}, "GAMMA": {"asset_staging_id": "s-g"}})
    w.excluded = {1}  # BETA, unticked (by default, or while the sweep ran)
    await sweeps._drive(
        w.ctx,
        None,
        RUN,
        scope_obj=Scope.shared(),
        spec=SPEC,
        entry=SWEEP_ENTRY,
        collections=["ALPHA", "BETA", "GAMMA"],
        user=None,
    )
    assert [e["options"]["project"] for e in w.enqueued] == ["ALPHA", "GAMMA"]
    assert 1 not in w.items and w.finished == ("done", None)


@pytest.mark.asyncio
async def test_new_sweeps_start_with_the_default_list_ticked_off(monkeypatch):
    inserted = {}

    async def specs(ctx):
        return {"vendor": [SPEC]}

    async def idle(pool, provider):
        return None

    async def setting(pool, key):
        assert key == "asset_sweeps.defaults.vendor"
        return json.dumps({"excluded": ["BETA"]})

    async def insert(pool, **kw):
        inserted.update(kw)
        return {"id": "r1", **kw}

    monkeypatch.setattr(sweeps, "provider_specs", specs)
    monkeypatch.setattr(sweeps, "spawn", lambda coro, name: coro.close())
    monkeypatch.setattr(dbm, "running_asset_sweep", idle)
    monkeypatch.setattr(dbm, "get_setting", setting)
    monkeypatch.setattr(dbm, "insert_asset_sweep_run", insert)
    await sweeps.start_sweep(
        None,
        None,
        scope="shared",
        scope_obj=Scope.shared(),
        provider="vendor",
        job="project-sweep",
        settings={},
        user=None,
        via="user",
    )
    assert inserted["collections"] == ["ALPHA", "BETA"] and inserted["excluded"] == frozenset({"BETA"})


@pytest.mark.asyncio
async def test_start_refuses_an_offline_provider_or_a_second_running_sweep(monkeypatch):
    async def no_specs(ctx):
        return {}

    monkeypatch.setattr(sweeps, "provider_specs", no_specs)
    kw = dict(scope="shared", scope_obj=Scope.shared(), provider="vendor", job="project-sweep", settings={}, user=None)
    with pytest.raises(sweeps.SweepRefused, match="no online worker"):
        await sweeps.start_sweep(None, None, via="user", **kw)

    async def specs(ctx):
        return {"vendor": [SPEC]}

    async def busy(pool, provider):
        return {"created_at": "earlier"}

    monkeypatch.setattr(sweeps, "provider_specs", specs)
    monkeypatch.setattr(dbm, "running_asset_sweep", busy)
    with pytest.raises(sweeps.SweepRefused, match="already running"):
        await sweeps.start_sweep(None, None, via="user", **kw)
    with pytest.raises(sweeps.SweepRefused, match="declares no sweep"):
        await sweeps.start_sweep(None, None, via="user", **{**kw, "job": "nope"})


# --- live Postgres -------------------------------------------------------------------------------


@needs_postgres
@pytest.mark.asyncio
async def test_sweep_run_lifecycle_against_postgres():
    pool = await dbm.init_pool(POSTGRES_URL)
    provider = f"test-{uuid.uuid4().hex[:8]}"
    try:
        run = await dbm.insert_asset_sweep_run(
            pool,
            scope="shared",
            provider=provider,
            plugin_id="vendor-plugin",
            job="project-sweep",
            settings={"sweep_target": "tree"},
            collections=["ALPHA", "BETA"],
            requested_by="tester",
            requested_via="user",
        )
        assert run["status"] == "running" and run["total"] == 2
        assert (await dbm.running_asset_sweep(pool, provider=provider))["id"] == run["id"]
        await dbm.update_asset_sweep_item(pool, run["id"], 0, status="done", timings={"total_ms": 5}, revision="r1")
        await dbm.skip_pending_asset_sweep_items(pool, run["id"], "stopped")
        await dbm.finish_asset_sweep_run(pool, run["id"], status="done")
        full = await dbm.get_asset_sweep_run(pool, run["id"])
        assert full["status"] == "done" and [i["status"] for i in full["items"]] == ["done", "skipped"]
        assert full["items"][0]["timings"] == {"total_ms": 5}
        [listed] = await dbm.list_asset_sweep_runs(pool, provider=provider)
        assert listed["counts"] == {"done": 1, "skipped": 1}
        assert await dbm.running_asset_sweep(pool, provider=provider) is None
        assert await dbm.cancel_asset_sweep_run(pool, run["id"]) is False  # finished: not cancellable

        # A running sweep whose heartbeat is old reads as interrupted, and does not block another.
        stale = await dbm.insert_asset_sweep_run(
            pool,
            scope="shared",
            provider=provider,
            plugin_id="vendor-plugin",
            job="project-sweep",
            settings={},
            collections=["ALPHA"],
            requested_by=None,
            requested_via="schedule",
        )
        await pool.execute(
            "UPDATE asset_sweep_runs SET heartbeat_at = NOW() - INTERVAL '1 hour' WHERE id = $1", stale["id"]
        )
        assert (await dbm.get_asset_sweep_run(pool, stale["id"]))["status"] == "interrupted"
        assert await dbm.running_asset_sweep(pool, provider=provider) is None
        assert await dbm.cancel_asset_sweep_run(pool, stale["id"]) is True
    finally:
        await pool.execute("DELETE FROM asset_sweep_runs WHERE provider = $1", provider)
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_ticking_collections_on_and_off_against_postgres():
    pool = await dbm.init_pool(POSTGRES_URL)
    provider = f"test-{uuid.uuid4().hex[:8]}"
    try:
        run = await dbm.insert_asset_sweep_run(
            pool,
            scope="shared",
            provider=provider,
            plugin_id="vendor-plugin",
            job="project-sweep",
            settings={},
            collections=["ALPHA", "BETA", "GAMMA"],
            requested_by="tester",
            requested_via="user",
            excluded=frozenset({"BETA"}),
        )
        assert [await dbm.asset_sweep_item_status(pool, run["id"], i) for i in range(3)] == [
            "pending",
            "excluded",
            "pending",
        ]
        # Off and on again while not reached; an item in flight or finished does not move.
        assert await dbm.set_asset_sweep_item_run(pool, run["id"], 2, run=False) == "excluded"
        assert await dbm.set_asset_sweep_item_run(pool, run["id"], 1, run=True) == "pending"
        await dbm.update_asset_sweep_item(pool, run["id"], 0, status="fetching")
        assert await dbm.set_asset_sweep_item_run(pool, run["id"], 0, run=False) is None
        # Cancelling skips what is pending, and leaves what was ticked off as it was.
        await dbm.cancel_asset_sweep_run(pool, run["id"])
        await dbm.skip_pending_asset_sweep_items(pool, run["id"], "the sweep was cancelled")
        statuses = [i["status"] for i in (await dbm.get_asset_sweep_run(pool, run["id"]))["items"]]
        assert statuses == ["fetching", "skipped", "excluded"]
        assert await dbm.set_asset_sweep_item_run(pool, run["id"], 2, run=True) is None  # not running
    finally:
        await pool.execute("DELETE FROM asset_sweep_runs WHERE provider = $1", provider)
        await dbm.close_pool(pool)


@pytest.mark.asyncio
async def test_cancel_also_cancels_the_jobs_in_flight_even_without_a_driver(monkeypatch):
    # The driver died with its API process (a restart): nobody else will cancel its job, which
    # otherwise ran for nobody on a single-seat provider while the next sweep queued behind it.
    cancelled, updated = [], []
    run = {
        "id": "r1",
        "status": "running",
        "items": [
            {"position": 0, "status": "done", "fetch_job_id": "old", "publish_job_id": "old-pub"},
            {"position": 1, "status": "fetching", "fetch_job_id": "live", "publish_job_id": None},
            {"position": 2, "status": "pending", "fetch_job_id": None, "publish_job_id": None},
        ],
    }

    async def get_run(pool, run_id):
        return run

    async def cancel_run(pool, run_id):
        return True

    async def skip_pending(pool, run_id, reason):
        pass

    async def update_item(pool, run_id, position, **fields):
        updated.append((position, fields["status"]))

    async def cancel_job(job_id):
        cancelled.append(job_id)
        return True

    monkeypatch.setattr(dbm, "get_asset_sweep_run", get_run)
    monkeypatch.setattr(dbm, "cancel_asset_sweep_run", cancel_run)
    monkeypatch.setattr(dbm, "skip_pending_asset_sweep_items", skip_pending)
    monkeypatch.setattr(dbm, "update_asset_sweep_item", update_item)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=object())))
    ctx = SimpleNamespace(jobs=SimpleNamespace(cancel=cancel_job))
    resp = await sweeps.admin_asset_sweep_cancel("r1", request, ctx)
    assert json.loads(resp.body) == {"cancelled": "r1", "jobs": ["live"]}
    assert cancelled == ["live"] and updated == [(1, "skipped")]


@pytest.mark.asyncio
async def test_width_is_the_declared_parallelism_capped_by_live_workers():
    rows = [{"capabilities": ["vendor", "base"]}, {"capabilities": ["VENDOR"]}, {"capabilities": ["other"]}]
    ctx = SimpleNamespace(jobs=SimpleNamespace(capabilities=lambda: _async(rows)))
    assert await sweeps.sweep_width(ctx, SPEC, {**SWEEP_ENTRY, "parallel": 8}) == 2  # two workers serve it
    assert await sweeps.sweep_width(ctx, SPEC, {**SWEEP_ENTRY, "parallel": 1}) == 1
    assert await sweeps.sweep_width(ctx, SPEC, SWEEP_ENTRY) == 1  # undeclared: one lane (a licensed seat)
    assert await sweeps.sweep_width(ctx, SPEC, {**SWEEP_ENTRY, "parallel": "x"}) == 1
    none = SimpleNamespace(jobs=SimpleNamespace(capabilities=lambda: _async([])))
    assert await sweeps.sweep_width(none, SPEC, {**SWEEP_ENTRY, "parallel": 8}) == 1


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_parallel_lanes_run_every_collection_exactly_once(world, monkeypatch):
    names = [f"C{i}" for i in range(7)]
    w = world(fetch_summaries={n: {"asset_staging_id": f"s-{n}"} for n in names})
    w.excluded = {3}

    async def width(ctx, spec, entry):
        return 3

    monkeypatch.setattr(sweeps, "sweep_width", width)
    await sweeps._drive(
        w.ctx, None, RUN, scope_obj=Scope.shared(), spec=SPEC, entry=SWEEP_ENTRY, collections=names, user=None
    )
    projects = sorted(e["options"]["project"] for e in w.enqueued)
    assert projects == sorted(n for i, n in enumerate(names) if i != 3)
    assert w.finished == ("done", None) and all(w.items[i]["status"] == "done" for i in w.items)
