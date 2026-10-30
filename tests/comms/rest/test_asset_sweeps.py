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
from fastapi import HTTPException

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

    def __init__(
        self, *, fetch_summaries: dict[str, dict], fail_publish: bool = False, fail_staging: frozenset = frozenset()
    ):
        self.fetch_summaries = fetch_summaries  # collection -> provider summary
        self.fail_publish = fail_publish
        self.fail_staging = fail_staging  # staging ids whose publish fails
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
        summary = self.fetch_summaries[collection]
        for staging in summary.get("asset_staging_ids") or [summary.get("asset_staging_id")]:
            if staging:
                self.blobs[f"assets/_staging/{staging}/csg.db"] = b"x"
        self.jobs_status[job_id] = "done"
        return job_id

    async def publish(self, ctx, scope_obj, *, provider, staged, user, **kw):
        self.published.append({"provider": provider, "staged": staged, "user": user})
        job_id, key = f"publish-{len(self.published)}", f"_derived/pub/{len(self.published)}.json"
        failing = self.fail_publish or any(f"/{s}/" in str(staged.get("csg.db")) for s in self.fail_staging)
        self.jobs_status[job_id] = "error" if failing else "done"
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

    # Another run holding a collection: collection -> holder, or a callable answering one per try.
    holders: dict = {}
    slots_held: int = 0
    max_slots_seen: int = 0

    async def claim(self, pool, run_id, position, *, provider, collection):
        status = await self.item_status(pool, run_id, position)
        if status not in ("pending", "waiting"):
            return {"claimed": False, "status": status, "holder": None}
        holder = self.holders.get(collection)
        holder = holder() if callable(holder) else holder
        if holder:
            return {"claimed": False, "status": status, "holder": holder}
        self.items.setdefault(position, {})["status"] = "fetching"
        return {"claimed": True, "status": "fetching", "holder": None}

    async def acquire_slot(self, pool, run_id, position, *, provider, cap):
        if self.slots_held >= cap:
            return False
        self.slots_held += 1
        self.max_slots_seen = max(self.max_slots_seen, self.slots_held)
        return True

    async def release_slot(self, pool, run_id, position):
        self.slots_held -= 1


def install_run_fakes(monkeypatch, w) -> None:
    """The run tables' claim and worker-slot calls, answered by ``w``."""
    monkeypatch.setattr(dbm, "claim_asset_sweep_item", w.claim)
    monkeypatch.setattr(dbm, "acquire_asset_worker_slot", w.acquire_slot)
    monkeypatch.setattr(dbm, "release_asset_worker_slot", w.release_slot)


@pytest.fixture
def world(monkeypatch):
    def make(**kw) -> _World:
        w = _World(**kw)
        install_run_fakes(monkeypatch, w)
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
async def test_a_collection_staged_in_parts_publishes_every_part(world):
    w = world(fetch_summaries={"ALPHA": {"asset_staging_ids": ["s-1", "s-2", "s-3"]}})
    await sweeps._drive(
        w.ctx, None, RUN, scope_obj=Scope.shared(), spec=SPEC, entry=SWEEP_ENTRY, collections=["ALPHA"], user=None
    )
    assert [p["staged"]["csg.db"] for p in w.published] == [f"assets/_staging/s-{i}/csg.db" for i in (1, 2, 3)]
    assert w.items[0]["status"] == "done" and w.items[0]["timings"]["published_parts"] == 3
    assert w.finished == ("done", None)


@pytest.mark.asyncio
async def test_a_part_that_fails_to_publish_is_counted_and_the_rest_still_publish(world):
    w = world(fetch_summaries={"ALPHA": {"asset_staging_ids": ["s-1", "s-2", "s-3"]}}, fail_staging=frozenset({"s-2"}))
    await sweeps._drive(
        w.ctx, None, RUN, scope_obj=Scope.shared(), spec=SPEC, entry=SWEEP_ENTRY, collections=["ALPHA"], user=None
    )
    assert len(w.published) == 3
    item = w.items[0]
    assert item["status"] == "error" and item["error"].startswith("1 of 3 staged part(s) failed to publish")
    assert item["timings"]["published_parts"] == 2


def test_staging_ids_take_the_list_and_fall_back_to_the_one_id():
    assert sweeps.staging_ids_of({"asset_staging_ids": ["a", " b ", "a", "", None]}) == ["a", "b"]
    assert sweeps.staging_ids_of({"asset_staging_id": "x"}) == ["x"]
    assert sweeps.staging_ids_of({}) == []


def test_an_entry_may_lengthen_its_fetch_timeout_within_a_bound():
    assert sweeps.fetch_timeout({}) == sweeps.FETCH_TIMEOUT_S
    assert sweeps.fetch_timeout({"fetch_timeout_s": 7200}) == 7200
    assert sweeps.fetch_timeout({"fetch_timeout_s": 10**9}) == sweeps.MAX_FETCH_TIMEOUT_S
    assert sweeps.fetch_timeout({"fetch_timeout_s": "nonsense"}) == sweeps.FETCH_TIMEOUT_S


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
async def test_start_refuses_an_offline_provider_but_not_a_second_run(monkeypatch):
    async def no_specs(ctx):
        return {}

    monkeypatch.setattr(sweeps, "provider_specs", no_specs)
    kw = dict(scope="shared", scope_obj=Scope.shared(), provider="vendor", job="project-sweep", settings={}, user=None)
    with pytest.raises(sweeps.SweepRefused, match="no online worker"):
        await sweeps.start_sweep(None, None, via="user", **kw)

    async def specs(ctx):
        return {"vendor": [SPEC]}

    async def busy(pool, provider):
        raise AssertionError("a running run of the provider is no longer a reason to refuse")

    async def setting(pool, key):
        return None

    async def insert(pool, **kw):
        return {"id": "r2", **kw}

    monkeypatch.setattr(sweeps, "provider_specs", specs)
    monkeypatch.setattr(sweeps, "spawn", lambda coro, name: coro.close())
    monkeypatch.setattr(dbm, "running_asset_sweep", busy)
    monkeypatch.setattr(dbm, "get_setting", setting)
    monkeypatch.setattr(dbm, "insert_asset_sweep_run", insert)
    assert (await sweeps.start_sweep(None, None, via="user", **kw))["id"] == "r2"
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


# --- resuming an interrupted sweep -----------------------------------------------------------------


def _resume_world(monkeypatch, *, status="interrupted", specs=None, busy=None, rearmed=None):
    """Fakes for :func:`sweeps.resume_sweep`; answers what it did."""
    seen = {"cancelled": [], "spawned": None}
    items = [
        {"position": 1, "collection": "BETA", "status": "pending"},
        {"position": 0, "collection": "ALPHA", "status": "done"},
    ]

    async def get_run(pool, run_id):
        # After the re-arm the run reads `running` again (its heartbeat is fresh).
        live = "running" if seen.get("rearmed") else status
        return {**RUN, "scope": "shared", "job": "project-sweep", "status": live, "items": list(items)}

    async def provider_specs(ctx):
        return {"vendor": [SPEC]} if specs is None else specs

    async def running(pool, provider):
        return busy

    async def scope_of(pool, scope, user):
        return Scope.shared()

    async def rearm(pool, run_id):
        seen["rearmed"] = True
        return (
            [{"position": 1, "collection": "BETA", "fetch_job_id": "old-fetch", "publish_job_id": None}]
            if rearmed is None
            else rearmed
        )

    async def cancel(job_id):
        seen["cancelled"].append(job_id)
        return True

    def spawn(coro, name):
        seen["spawned"] = (name, coro.cr_frame.f_locals.get("collections"))
        coro.close()

    monkeypatch.setattr(dbm, "get_asset_sweep_run", get_run)
    monkeypatch.setattr(dbm, "running_asset_sweep", running)
    monkeypatch.setattr(dbm, "resume_asset_sweep_run", rearm)
    monkeypatch.setattr(sweeps, "provider_specs", provider_specs)
    monkeypatch.setattr(sweeps, "_scope_of", scope_of)
    monkeypatch.setattr(sweeps, "spawn", spawn)
    ctx = SimpleNamespace(jobs=SimpleNamespace(cancel=cancel))
    return ctx, seen


@pytest.mark.asyncio
async def test_resume_gives_the_same_run_a_new_driver_over_every_collection(monkeypatch):
    ctx, seen = _resume_world(monkeypatch)
    run = await sweeps.resume_sweep(ctx, object(), "run-1", user=None)
    assert run["id"] == "run-1" and run["status"] == "running" and run["resumed"] == ["BETA"]
    # The job that was in flight is cancelled; it runs again under the new driver.
    assert seen["cancelled"] == ["old-fetch"]
    # Every collection, in position order: the driver passes over what is not `pending`.
    assert seen["spawned"] == ("asset-sweep-run-1", ["ALPHA", "BETA"])


@pytest.mark.asyncio
async def test_resume_refuses_what_is_not_interrupted_or_cannot_run(monkeypatch):
    ctx, _ = _resume_world(monkeypatch, status="running")
    with pytest.raises(sweeps.SweepRefused, match="only an interrupted sweep"):
        await sweeps.resume_sweep(ctx, object(), "run-1", user=None)
    ctx, _ = _resume_world(monkeypatch, status="cancelled")
    with pytest.raises(sweeps.SweepRefused, match="this one is cancelled"):
        await sweeps.resume_sweep(ctx, object(), "run-1", user=None)
    ctx, _ = _resume_world(monkeypatch, specs={})
    with pytest.raises(sweeps.SweepRefused, match="no online worker"):
        await sweeps.resume_sweep(ctx, object(), "run-1", user=None)
    # Another run of the provider no longer refuses a resume: runs go side by side.
    ctx, seen = _resume_world(monkeypatch, busy={"id": "other", "created_at": "earlier"})
    await sweeps.resume_sweep(ctx, object(), "run-1", user=None)
    assert seen["spawned"] is not None


@pytest.mark.asyncio
async def test_two_resumes_of_one_sweep_start_one_driver(monkeypatch):
    # The second finds the run already re-armed (the database answers None): refused, not doubled.
    async def already(pool, run_id):
        return None

    ctx, seen = _resume_world(monkeypatch)
    monkeypatch.setattr(dbm, "resume_asset_sweep_run", already)
    with pytest.raises(sweeps.SweepRefused, match="no longer interrupted"):
        await sweeps.resume_sweep(ctx, object(), "run-1", user=None)
    assert seen["spawned"] is None and seen["cancelled"] == []


@needs_postgres
@pytest.mark.asyncio
async def test_resume_rearms_only_an_interrupted_sweep_against_postgres():
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
            collections=["ALPHA", "BETA", "GAMMA", "DELTA"],
            requested_by="tester",
            requested_via="user",
            excluded=frozenset({"DELTA"}),
        )
        rid = run["id"]
        await dbm.update_asset_sweep_item(pool, rid, 0, status="done", revision="r1", timings={"total_ms": 5})
        await dbm.update_asset_sweep_item(pool, rid, 1, status="publishing", fetch_job_id="f1", publish_job_id="p1")
        # A live driver: not interrupted, nothing moves.
        assert await dbm.resume_asset_sweep_run(pool, rid) is None
        await pool.execute("UPDATE asset_sweep_runs SET heartbeat_at = NOW() - INTERVAL '1 hour' WHERE id = $1", rid)
        assert (await dbm.get_asset_sweep_run(pool, rid))["status"] == "interrupted"

        [moved] = await dbm.resume_asset_sweep_run(pool, rid)
        assert moved == {"position": 1, "collection": "BETA", "fetch_job_id": "f1", "publish_job_id": "p1"}
        full = await dbm.get_asset_sweep_run(pool, rid)
        assert full["status"] == "running"
        assert [i["status"] for i in full["items"]] == ["done", "pending", "pending", "excluded"]
        from ada.comms.rest.db.asset_sweeps import RESUMED_ITEM

        assert full["items"][0]["timings"] == {"total_ms": 5} and full["items"][1]["message"] == RESUMED_ITEM
        assert (await dbm.running_asset_sweep(pool, provider=provider))["id"] == rid
        # A second resume finds a live heartbeat.
        assert await dbm.resume_asset_sweep_run(pool, rid) is None
    finally:
        await pool.execute("DELETE FROM asset_sweep_runs WHERE provider = $1", provider)
        await dbm.close_pool(pool)


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


# --- a collection request over chosen collections (the export's "request the trees") ----------------

SPEC_WITH_REQUEST = {
    **SPEC,
    "asset_collection_request": {
        "options": {"action": "stage-tree", "schema_version": 1},
        "collection_option": "project",
        "label": "Request project tree (vendor)",
    },
}


def test_the_collection_request_is_a_sweep_entry_asking_what_the_sources_tab_asks():
    entry = sweeps.collection_request_entry(SPEC_WITH_REQUEST)
    assert entry == {
        "id": "collection-request",
        "kind": "sweep",
        "label": "Request project tree (vendor)",
        "options": {"action": "stage-tree", "schema_version": 1},
        "collection_option": "project",
    }
    assert sweeps.collection_request_entry(SPEC) is None


def test_chosen_collections_take_the_providers_spelling_and_unknown_ones_are_named():
    assert sweeps.offered_names(["ALPHA", "BETA"], ["beta", "alpha", "BETA"]) == (["BETA", "ALPHA"], [])
    assert sweeps.offered_names(["ALPHA"], ["alpha", "zeta"]) == (["ALPHA"], ["zeta"])
    assert sweeps.offered_names([], ["x"]) == (["x"], [])


@pytest.mark.asyncio
async def test_a_collection_request_runs_only_the_chosen_collections_and_ignores_the_default_list(monkeypatch):
    inserted, spawned = {}, {}

    async def advertised(kind):
        return {"a": SPEC_WITH_REQUEST}

    async def idle(pool, provider):
        return None

    async def setting(pool, key):
        raise AssertionError("the default list does not apply to named collections")

    async def insert(pool, **kw):
        inserted.update(kw)
        return {"id": "r1", **kw}

    def spawn(coro, name):
        spawned.update(coro.cr_frame.f_locals)
        coro.close()

    ctx = SimpleNamespace(jobs=SimpleNamespace(advertised_specs=advertised))
    monkeypatch.setattr(sweeps, "spawn", spawn)
    monkeypatch.setattr(dbm, "running_asset_sweep", idle)
    monkeypatch.setattr(dbm, "get_setting", setting)
    monkeypatch.setattr(dbm, "insert_asset_sweep_run", insert)
    kw = dict(scope="shared", scope_obj=Scope.shared(), provider="vendor", settings={}, user=None, via="export")
    await sweeps.start_sweep(ctx, None, job=sweeps.COLLECTION_REQUEST, collections=["beta"], **kw)
    assert inserted["job"] == "collection-request"
    assert inserted["collections"] == ["BETA"] and inserted["excluded"] == frozenset()
    assert spawned["entry"]["options"] == {"action": "stage-tree", "schema_version": 1}

    with pytest.raises(sweeps.SweepRefused, match="does not offer zeta"):
        await sweeps.start_sweep(ctx, None, job=sweeps.COLLECTION_REQUEST, collections=["zeta"], **kw)

    async def without_request(kind):
        return {"a": SPEC}

    ctx = SimpleNamespace(jobs=SimpleNamespace(advertised_specs=without_request))
    with pytest.raises(sweeps.SweepRefused, match="declares no collection request"):
        await sweeps.start_sweep(ctx, None, job=sweeps.COLLECTION_REQUEST, collections=["beta"], **kw)


@pytest.mark.asyncio
async def test_a_collection_request_item_asks_the_provider_with_the_requests_own_options(world):
    w = world(fetch_summaries={"BETA": {"asset_staging_id": "stg-b"}})
    entry = sweeps.collection_request_entry(SPEC_WITH_REQUEST)
    run = {"id": "r1", "provider": "vendor", "plugin_id": "vendor-plugin", "settings": {}}
    ok = await sweeps._sweep_one(
        w.ctx, None, run, 0, "BETA", scope_obj=Scope.shared(), spec=SPEC_WITH_REQUEST, entry=entry, user=None
    )
    assert ok and w.items[0]["status"] == "done"
    options = w.enqueued[0]["options"]
    assert options["action"] == "stage-tree" and options["project"] == "BETA" and "sweep_target" not in options
    assert w.published[0]["provider"] == "vendor"


# --- several targets per collection (a multiple `sweep_target`) ------------------------------------

MULTI_ENTRY = {**SWEEP_ENTRY, "targets_setting": "sweep_target"}
MULTI_SPEC = {
    **SPEC,
    "job_options": [
        {
            "name": "sweep_target",
            "type": "enum",
            "multiple": True,
            "enum": ["tree", "geom", "attributes", "bounds"],
            "default": ["tree", "geom", "attributes", "bounds"],
        }
    ],
    "asset_schedules": [MULTI_ENTRY],
}


class _TargetWorld(_World):
    """Answers per (collection, target): ``fetch_summaries["ALPHA/tree"]``."""

    async def enqueue(self, ctx, **kw):
        opts = kw["options"]
        assert isinstance(opts["sweep_target"], str), "each job asks for ONE target"
        key = f"{opts['project']}/{opts['sweep_target']}"
        self.enqueued.append(kw)
        job_id = f"fetch-{len(self.enqueued)}"
        summary = self.fetch_summaries[key]
        self.blobs[kw["derived_key"]] = gzip.compress(json.dumps(summary).encode())
        if summary.get("asset_staging_id"):
            self.blobs[f"assets/_staging/{summary['asset_staging_id']}/csg.db"] = b"x"
        self.jobs_status[job_id] = "error" if summary.get("fail") else "done"
        return job_id


@pytest.fixture
def target_world(monkeypatch):
    def make(**kw) -> _TargetWorld:
        w = _TargetWorld(**kw)
        install_run_fakes(monkeypatch, w)
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


@pytest.mark.asyncio
async def test_build_options_keeps_the_targets_a_list_defaults_them_and_refuses_none():
    kw = dict(scope="providers", provider="vendor", collection=None)
    row = await sched.build_options(None, MULTI_SPEC, MULTI_ENTRY, settings={"sweep_target": ["bounds", "tree"]}, **kw)
    assert row["sweep_target"] == ["bounds", "tree"]
    unset = await sched.build_options(None, MULTI_SPEC, MULTI_ENTRY, settings={}, **kw)
    assert unset["sweep_target"] == ["tree", "geom", "attributes", "bounds"]
    with pytest.raises(HTTPException) as exc:
        await sched.build_options(None, MULTI_SPEC, MULTI_ENTRY, settings={"sweep_target": []}, **kw)
    assert exc.value.status_code == 400 and "tick at least one" in exc.value.detail


def test_targets_run_in_the_declared_order_and_a_stored_single_value_is_a_list_of_one():
    assert sched.sweep_targets(MULTI_SPEC, MULTI_ENTRY, {"sweep_target": ["bounds", "tree"]}) == ["tree", "bounds"]
    assert sched.sweep_targets(MULTI_SPEC, MULTI_ENTRY, {"sweep_target": "geom"}) == ["geom"]
    assert sched.sweep_targets(MULTI_SPEC, MULTI_ENTRY, {}) == ["tree", "geom", "attributes", "bounds"]
    # Still a single choice, or no targets_setting: one job per collection, as before.
    assert sched.sweep_targets(SPEC, MULTI_ENTRY, {"sweep_target": "tree"}) is None
    assert sched.sweep_targets(MULTI_SPEC, SWEEP_ENTRY, {"sweep_target": ["tree"]}) is None


@pytest.mark.asyncio
async def test_one_job_per_ticked_target_each_published_before_the_next(target_world):
    w = target_world(
        fetch_summaries={
            "ALPHA/tree": {"asset_staging_id": "s-tree", "timings": {"export_ms": 5}},
            "ALPHA/geom": {"asset_unchanged": True, "revision": "r0"},
            "ALPHA/bounds": {"asset_staging_id": "s-bounds"},
        }
    )
    run = {**RUN, "settings": {"sweep_target": ["bounds", "geom", "tree"]}}
    ok = await sweeps._sweep_one(
        w.ctx, None, run, 0, "ALPHA", scope_obj=Scope.shared(), spec=MULTI_SPEC, entry=MULTI_ENTRY, user=None
    )
    assert ok
    assert [e["options"]["sweep_target"] for e in w.enqueued] == ["tree", "geom", "bounds"]
    assert [p["staged"]["csg.db"] for p in w.published] == [
        "assets/_staging/s-tree/csg.db",
        "assets/_staging/s-bounds/csg.db",
    ]
    item = w.items[0]
    targets = item["timings"]["targets"]
    assert list(targets) == ["tree", "geom", "bounds"]
    assert targets["tree"]["status"] == "done" and targets["tree"]["timings"]["provider"] == {"export_ms": 5}
    assert targets["tree"]["subjects"] == 0 and targets["geom"]["status"] == "unchanged"
    assert item["status"] == "done" and item["message"] == "tree: done · geom: unchanged · bounds: done"
    assert {"queued_ms", "fetch_ms", "publish_ms", "total_ms"} <= set(item["timings"])


@pytest.mark.asyncio
async def test_a_failed_tree_skips_what_reads_it_and_the_rest_still_run(target_world):
    w = target_world(
        fetch_summaries={
            "ALPHA/tree": {"fail": True},
            "ALPHA/geom": {"asset_staging_id": "s-geom"},
            "ALPHA/bounds": {"asset_staging_id": "s-bounds"},
        }
    )
    run = {**RUN, "settings": {"sweep_target": ["tree", "geom", "bounds"]}}
    ok = await sweeps._sweep_one(
        w.ctx, None, run, 0, "ALPHA", scope_obj=Scope.shared(), spec=MULTI_SPEC, entry=MULTI_ENTRY, user=None
    )
    assert not ok
    assert [e["options"]["sweep_target"] for e in w.enqueued] == ["tree", "geom"]
    t = w.items[0]["timings"]["targets"]
    assert t["tree"]["status"] == "error" and t["tree"]["error"] == "boom"
    assert t["geom"]["status"] == "done"
    assert t["bounds"]["status"] == "skipped" and "tree" in t["bounds"]["message"]
    assert w.items[0]["status"] == "error" and w.items[0]["error"] == "1 of 3 target(s) failed: tree: boom"


@pytest.mark.asyncio
async def test_a_single_target_sweep_is_unchanged(world):
    w = world(fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}})
    await sweeps._sweep_one(
        w.ctx, None, RUN, 0, "ALPHA", scope_obj=Scope.shared(), spec=SPEC, entry=MULTI_ENTRY, user=None
    )
    assert w.enqueued[0]["options"]["sweep_target"] == "tree" and "targets" not in w.items[0]["timings"]


# --- several runs of one provider at once --------------------------------------------------------


class _Fleet:
    """Several runs' tables and one provider's jobs, faked with the database's rules: a collection is
    claimed by one live run at a time, and worker slots are capped across runs. A job runs for
    ``polls`` status reads, so runs overlap in time."""

    def __init__(self, *, polls: int = 3):
        self.polls = polls
        self.items: dict[tuple[str, int], dict] = {}
        self.jobs: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.in_flight_by_collection: dict[str, int] = {}
        self.max_same_collection = 0
        self.slots: set[tuple[str, int]] = set()
        self.statuses_seen: dict[tuple[str, int], list[str]] = {}
        self.waiting_messages: list = []
        self.ctx = SimpleNamespace(
            jobs=SimpleNamespace(status=self.status, cancel=self.cancel),
            storage=SimpleNamespace(get_bytes=self.get, list_prefix=self.list),
        )

    def add_run(self, run_id: str, collections: list[str]) -> dict:
        for i, c in enumerate(collections):
            self.items[(run_id, i)] = {"collection": c, "status": "pending"}
        return {"id": run_id, "provider": "vendor", "plugin_id": "vendor-plugin", "settings": {"sweep_target": "tree"}}

    async def status(self, job_id):
        job = self.jobs[job_id]
        job["reads"] += 1
        if job["reads"] >= self.polls and not job["done"]:
            job["done"] = True
            self.in_flight -= 1
            self.in_flight_by_collection[job["collection"]] -= 1
        return SimpleNamespace(status="done" if job["done"] else "running", payload={})

    async def cancel(self, job_id):
        return True

    async def get(self, scope, key):
        return self.blobs[key]

    async def list(self, scope, prefix):
        return [SimpleNamespace(key=k) for k in self.blobs if k.startswith(prefix)]

    async def enqueue(self, ctx, **kw):
        c = kw["options"]["project"]
        job_id = f"job-{len(self.jobs) + 1}"
        self.jobs[job_id] = {"reads": 0, "done": False, "collection": c}
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.in_flight_by_collection[c] = self.in_flight_by_collection.get(c, 0) + 1
        self.max_same_collection = max(self.max_same_collection, self.in_flight_by_collection[c])
        self.blobs[kw["derived_key"]] = json.dumps({"asset_unchanged": True, "revision": "r0"}).encode()
        return job_id

    async def update_item(self, pool, run_id, position, **fields):
        self.items[(run_id, position)].update(fields)
        if "status" in fields:
            self.statuses_seen.setdefault((run_id, position), []).append(fields["status"])
        if fields.get("status") == "waiting":
            self.waiting_messages.append(fields.get("message"))

    async def heartbeat(self, pool, run_id):
        return "running"

    async def finish(self, pool, run_id, *, status, error=None):
        self.items[(run_id, "finished")] = {"status": status, "error": error}

    async def skip(self, pool, run_id, reason):
        pass

    async def claim(self, pool, run_id, position, *, provider, collection):
        mine = self.items[(run_id, position)]
        if mine["status"] not in ("pending", "waiting"):
            return {"claimed": False, "status": mine["status"], "holder": None}
        for (rid, pos), it in self.items.items():
            if rid != run_id and isinstance(pos, int) and it["collection"].lower() == collection.lower():
                if it["status"] in ("fetching", "publishing"):
                    return {"claimed": False, "status": mine["status"],
                            "holder": {"run_id": rid, "job": "project-sweep", "created_at": "earlier"}}  # fmt: skip
        mine["status"] = "fetching"
        return {"claimed": True, "status": "fetching", "holder": None}

    async def acquire(self, pool, run_id, position, *, provider, cap):
        if (run_id, position) in self.slots:
            return True
        if len(self.slots) >= cap:
            return False
        self.slots.add((run_id, position))
        return True

    async def release(self, pool, run_id, position):
        self.slots.discard((run_id, position))


@pytest.fixture
def fleet(monkeypatch):
    def make(*, cap: int = 4, polls: int = 3) -> _Fleet:
        f = _Fleet(polls=polls)
        monkeypatch.setattr(sweeps, "enqueue_plugin_job", f.enqueue)
        monkeypatch.setattr(dbm, "update_asset_sweep_item", f.update_item)
        monkeypatch.setattr(dbm, "heartbeat_asset_sweep_run", f.heartbeat)
        monkeypatch.setattr(dbm, "finish_asset_sweep_run", f.finish)
        monkeypatch.setattr(dbm, "skip_pending_asset_sweep_items", f.skip)
        monkeypatch.setattr(dbm, "claim_asset_sweep_item", f.claim)
        monkeypatch.setattr(dbm, "acquire_asset_worker_slot", f.acquire)
        monkeypatch.setattr(dbm, "release_asset_worker_slot", f.release)
        monkeypatch.setattr(sweeps, "POLL_INTERVAL_S", 0)

        async def budget(ctx, pool, provider, spec, *, fresh=False):
            return {"cap": cap, "live_workers": cap, "declared": cap, "max_parallel": None}

        monkeypatch.setattr(sweeps, "provider_budget", budget)
        return f

    return make


async def _drive_runs(f: _Fleet, runs: list[tuple[dict, list[str]]], entry=SWEEP_ENTRY):
    import asyncio

    await asyncio.gather(
        *(
            sweeps._drive(f.ctx, None, run, scope_obj=Scope.shared(), spec=SPEC, entry=entry, collections=cs, user=None)
            for run, cs in runs
        )
    )


@pytest.mark.asyncio
async def test_two_runs_of_different_collections_both_proceed_at_once(fleet):
    f = fleet(cap=4)
    a, b = f.add_run("A", ["ALPHA"]), f.add_run("B", ["BETA"])
    await _drive_runs(f, [(a, ["ALPHA"]), (b, ["BETA"])])
    assert f.items[("A", 0)]["status"] == "unchanged" and f.items[("B", 0)]["status"] == "unchanged"
    assert f.max_in_flight == 2  # side by side, not one after the other


@pytest.mark.asyncio
async def test_one_collection_is_worked_on_by_one_run_at_a_time(fleet):
    f = fleet(cap=4)
    a, b = f.add_run("A", ["ALPHA"]), f.add_run("B", ["alpha", "BETA"])
    await _drive_runs(f, [(a, ["ALPHA"]), (b, ["alpha", "BETA"])])
    assert f.max_same_collection == 1
    # B waited for A's ALPHA, did BETA meanwhile, then ALPHA itself.
    assert "waiting" in f.statuses_seen[("B", 0)]
    assert f.waiting_messages == ["waiting: alpha is being processed by run A (project-sweep, started earlier)"]
    assert [f.items[k]["status"] for k in (("A", 0), ("B", 0), ("B", 1))] == ["unchanged"] * 3
    assert len(f.jobs) == 3


@pytest.mark.asyncio
async def test_the_worker_budget_caps_jobs_across_runs(fleet):
    f = fleet(cap=1)
    runs = [(f.add_run(r, [c]), [c]) for r, c in (("A", "ALPHA"), ("B", "BETA"), ("C", "GAMMA"))]
    await _drive_runs(f, runs)
    assert f.max_in_flight == 1 and len(f.jobs) == 3
    assert all(f.items[(r, 0)]["status"] == "unchanged" for r in "ABC")


@pytest.mark.asyncio
async def test_a_collection_held_too_long_ends_busy_naming_the_other_run(fleet, monkeypatch):
    f = fleet(cap=4)
    monkeypatch.setattr(sweeps, "PUBLISH_TIMEOUT_S", 0)
    f.items[("OTHER", 0)] = {"collection": "ALPHA", "status": "fetching"}  # never lets go
    run = f.add_run("A", ["ALPHA"])
    entry = {**SWEEP_ENTRY, "fetch_timeout_s": 0.001}
    await _drive_runs(f, [(run, ["ALPHA"])], entry=entry)
    item = f.items[("A", 0)]
    assert item["status"] == "busy" and "run OTHER" in item["message"] and f.jobs == {}


@pytest.mark.asyncio
async def test_the_budget_is_recomputed_and_an_admin_may_lower_it(monkeypatch):
    workers = {"n": 1}

    async def caps():
        return [{"capabilities": ["vendor"]}] * workers["n"]

    admin = {"v": None}

    async def setting(pool, key):
        assert key == "asset_runs.max_parallel.vendor"
        return admin["v"]

    monkeypatch.setattr(dbm, "get_setting", setting)
    sweeps._CAPS.clear()
    ctx = SimpleNamespace(jobs=SimpleNamespace(capabilities=caps))
    spec = {**SPEC, "asset_schedules": [{**SWEEP_ENTRY, "parallel": 5}]}
    assert (await sweeps.provider_budget(ctx, None, "vendor", spec))["cap"] == 1
    workers["n"] = 3
    assert (await sweeps.provider_budget(ctx, None, "vendor", spec))["cap"] == 1  # cached
    monkeypatch.setattr(sweeps, "CAP_TTL_S", 0)
    assert (await sweeps.provider_budget(ctx, None, "vendor", spec))["cap"] == 3  # recomputed
    admin["v"] = "2"
    got = await sweeps.provider_budget(ctx, None, "vendor", spec)
    assert got == {"cap": 2, "live_workers": 3, "declared": 5, "max_parallel": 2}
    workers["n"] = 9
    assert (await sweeps.provider_budget(ctx, None, "vendor", spec))["cap"] == 2
    # A single-seat provider (no `parallel` anywhere) stays at one, whatever the fleet.
    assert (await sweeps.provider_budget(ctx, None, "vendor", SPEC, fresh=True))["cap"] == 1
    sweeps._CAPS.clear()


@needs_postgres
@pytest.mark.asyncio
async def test_claims_and_worker_slots_hold_across_runs_against_postgres():
    import asyncio

    pool = await dbm.init_pool(POSTGRES_URL)
    provider = f"test-{uuid.uuid4().hex[:8]}"
    try:
        kw = dict(scope="providers", provider=provider, plugin_id="p", job="project-sweep", settings={},
                  requested_by=None, requested_via="user")  # fmt: skip
        a = await dbm.insert_asset_sweep_run(pool, collections=["ALPHA", "BETA"], **kw)
        b = await dbm.insert_asset_sweep_run(pool, collections=["alpha"], **kw)
        # Both runs try ALPHA at once (as two replicas would): exactly one gets it.
        got = await asyncio.gather(
            dbm.claim_asset_sweep_item(pool, a["id"], 0, provider=provider, collection="ALPHA"),
            dbm.claim_asset_sweep_item(pool, b["id"], 0, provider=provider, collection="alpha"),
        )
        assert sorted(g["claimed"] for g in got) == [False, True]
        loser = got[0] if not got[0]["claimed"] else got[1]
        assert loser["holder"]["job"] == "project-sweep"
        # Another collection is free.
        assert (await dbm.claim_asset_sweep_item(pool, a["id"], 1, provider=provider, collection="BETA"))["claimed"]
        # Worker slots: a cap of one across both runs.
        assert await dbm.acquire_asset_worker_slot(pool, a["id"], 0, provider=provider, cap=1)
        assert not await dbm.acquire_asset_worker_slot(pool, b["id"], 0, provider=provider, cap=1)
        assert await dbm.asset_worker_slots_in_use(pool, provider=provider) == 1
        await dbm.release_asset_worker_slot(pool, a["id"], 0)
        assert await dbm.acquire_asset_worker_slot(pool, b["id"], 0, provider=provider, cap=1)
        # A run whose driver died holds nothing.
        await pool.execute(
            "UPDATE asset_sweep_runs SET heartbeat_at = NOW() - INTERVAL '1 hour' WHERE id = $1", b["id"]
        )
        assert await dbm.asset_worker_slots_in_use(pool, provider=provider) == 0
    finally:
        await pool.execute("DELETE FROM asset_sweep_runs WHERE provider = $1", provider)
        await dbm.close_pool(pool)
