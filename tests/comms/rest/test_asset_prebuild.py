"""Pre-build meshes (core's ``prebuild_meshes`` setting): after a collection published, fill the
provider store's build cache for its build-delivered subjects -- with fakes for the store, the build
route and the job queue."""

from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi import HTTPException

from ada.comms.rest import db as dbm
from ada.comms.rest.routes import asset_prebuild as pb
from ada.comms.rest.routes import asset_schedules as sched
from ada.comms.rest.routes import asset_sweeps as sweeps
from ada.comms.rest.scope import Scope


def _manifest(delivery: str, provider: str = "vendor", capability: str = "builder") -> bytes:
    doc = {"provider": provider, "delivery": delivery}
    if delivery == "build":
        doc["build"] = {"capability": capability, "options": {}}
    return json.dumps(doc).encode()


class _Store:
    def __init__(self, blobs: dict[str, bytes]):
        self.blobs = blobs

    async def list_child_names(self, scope, prefix):
        return sorted(
            {k[len(prefix) :].split("/", 1)[0] for k in self.blobs if k.startswith(prefix) and "/" in k[len(prefix) :]}
        )

    async def get_bytes(self, scope, key):
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]


BLOBS = {
    "assets/alpha/site-1/20260101T000000Z/asset.json": _manifest("build"),
    "assets/alpha/site-2/20260101T000000Z/asset.json": _manifest("mesh"),
    # The newest revision is incomplete (no manifest yet): the older complete one decides.
    "assets/alpha/site-3/20260102T000000Z/part.bin": b"x",
    "assets/alpha/site-3/20260101T000000Z/asset.json": _manifest("build", provider="other"),
    "assets/beta/site-9/20260101T000000Z/asset.json": _manifest("build"),
    "assets/_staging/abc/csg.db": b"x",
}


def test_the_core_checkbox_is_offered_on_sweeps_and_apply_changes_and_never_stored_off():
    sweep = {"id": "s", "kind": "sweep", "options": {}, "collection_option": "project"}
    names = [s["name"] for s in sched.entry_settings({}, sweep)]
    assert names == ["prebuild_meshes"]
    [setting] = sched.entry_settings({}, sweep)
    assert setting["type"] == "bool" and setting["title"] == "Pre-build meshes" and setting["default"] is False
    assert "prebuild_meshes" not in [s["name"] for s in sched.entry_settings({}, {**sweep, "kind": "change-check"})]


@pytest.mark.asyncio
async def test_build_options_stores_it_only_when_ticked():
    entry = {"id": "s", "kind": "sweep", "options": {"action": "x"}, "collection_option": "project"}
    kw = dict(scope="providers", provider="vendor", collection=None)
    assert await sched.build_options(None, {}, entry, settings={}, **kw) == {"action": "x"}
    on = await sched.build_options(None, {}, entry, settings={"prebuild_meshes": "true"}, **kw)
    assert on["prebuild_meshes"] is True and sched.settings_of(entry, on) == {"prebuild_meshes": True}


@pytest.mark.asyncio
async def test_the_build_delivered_subjects_of_a_collection_newest_complete_manifest_decides():
    ctx = SimpleNamespace(storage=_Store(BLOBS))
    got = await pb.build_subjects(ctx, Scope.providers(), "ALPHA")
    assert got == [
        {"collection": "alpha", "subject": "site-1", "provider": "vendor", "capability": "builder"},
        {"collection": "alpha", "subject": "site-3", "provider": "other", "capability": "builder"},
    ]


class _Builds:
    """The build route and the queue: ``outcome[subject]`` is ``cached`` / ``built`` / ``failed`` /
    ``refused`` / ``mesh``; a job runs for two status reads; the most in flight at once is kept."""

    def __init__(self, outcome: dict[str, str], workers: int = 2):
        self.outcome = outcome
        self.workers = workers
        self.jobs: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.ctx = SimpleNamespace(jobs=SimpleNamespace(status=self.status, cancel=self.cancel, capabilities=self.caps))

    async def caps(self):
        return [{"capabilities": ["builder"]}] * self.workers

    async def submit(self, ctx, scope_obj, **kw):
        self.calls.append(kw)
        what = self.outcome[kw["subject"]]
        if what == "cached":
            return {"cached": True, "job_id": None}
        if what == "mesh":
            raise HTTPException(status_code=409, detail="claims delivery 'mesh', not 'build'")
        if what == "refused":
            raise HTTPException(status_code=404, detail="no such subject")
        job = f"build-{len(self.jobs) + 1}"
        self.jobs[job] = {"reads": 0, "fail": what == "failed"}
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        return {"cached": False, "job_id": job}

    async def status(self, job_id):
        job = self.jobs[job_id]
        job["reads"] += 1
        if job["reads"] < 2:
            return SimpleNamespace(status="running", payload={})
        if not job.get("ended"):
            job["ended"] = True
            self.in_flight -= 1
        return SimpleNamespace(status="error" if job["fail"] else "done", payload={"error": "builder crashed"})

    async def cancel(self, job_id):
        return True


@pytest.fixture
def builds(monkeypatch):
    def make(outcome, workers=2) -> _Builds:
        b = _Builds(outcome, workers)
        monkeypatch.setattr(pb, "submit_node_build", b.submit)

        async def beat(pool, run_id):
            return "running"

        monkeypatch.setattr(dbm, "heartbeat_asset_sweep_run", beat)
        return b

    return make


RUN = {"id": "run-1", "provider": "vendor", "plugin_id": "vendor-plugin", "settings": {"prebuild_meshes": True}}


def _targets(*subjects):
    return [{"collection": "alpha", "subject": s, "provider": "vendor", "capability": "builder"} for s in subjects]


@pytest.mark.asyncio
async def test_cached_built_failed_and_not_a_build_are_each_counted(builds):
    b = builds({"a": "cached", "b": "built", "c": "failed", "d": "mesh", "e": "refused"})
    got = await pb.prebuild(b.ctx, None, RUN, 0, _targets("a", "b", "c", "d", "e"), user=None, poll_s=0)
    assert {k: got[k] for k in ("subjects", "cached", "built", "failed", "skipped")} == {
        "subjects": 5, "cached": 1, "built": 1, "failed": 2, "skipped": 1,
    }  # fmt: skip
    assert "ms" in got and any("builder crashed" in e for e in got["errors"])
    # Each asks for exactly the subject as its own node, in the store, as the manifest's provider.
    assert all(c["node"] == c["subject"] and c["asked_provider"] == "vendor" for c in b.calls)
    assert pb.prebuild_line(got) == "meshes pre-built 2 of 5 (1 cached, 2 failed)"


@pytest.mark.asyncio
async def test_builds_fan_out_no_wider_than_the_build_workers(builds):
    b = builds({s: "built" for s in "abcdefg"}, workers=3)
    got = await pb.prebuild(b.ctx, None, RUN, 0, _targets(*"abcdefg"), user=None, poll_s=0)
    assert got["built"] == 7 and 1 < b.max_in_flight <= 3
    b = builds({s: "built" for s in "abcdefghijkl"}, workers=40)
    await pb.prebuild(b.ctx, None, RUN, 0, _targets(*"abcdefghijkl"), user=None, poll_s=0)
    assert b.max_in_flight <= pb.MAX_BUILD_WIDTH


# --- in a sweep ------------------------------------------------------------------------------------

from tests.comms.rest.test_asset_sweeps import MULTI_ENTRY, MULTI_SPEC  # noqa: E402
from tests.comms.rest.test_asset_sweeps import RUN as SWEEP_RUN  # noqa: E402
from tests.comms.rest.test_asset_sweeps import SPEC, SWEEP_ENTRY  # noqa: E402


@pytest.fixture
def prebuilt(monkeypatch):
    calls = []

    async def collection(ctx, pool, run, position, collection, *, user, poll_s):
        calls.append(collection)
        return {"subjects": 2, "cached": 1, "built": 1, "failed": 0, "skipped": 0, "ms": 5}

    monkeypatch.setattr(sweeps, "prebuild_collection", collection)
    return calls


@pytest.mark.asyncio
async def test_a_sweep_pre_builds_after_the_collection_published_and_never_tells_the_provider(
    world, prebuilt  # noqa: F811
):
    w = world(fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}, "BETA": {"fail": True}})
    run = {**SWEEP_RUN, "settings": {"sweep_target": "tree", "prebuild_meshes": True}}
    await sweeps._drive(w.ctx, None, run, scope_obj=Scope.shared(), spec=SPEC, entry=SWEEP_ENTRY,
                        collections=["ALPHA"], user=None)  # fmt: skip
    assert prebuilt == ["ALPHA"]
    assert "prebuild_meshes" not in w.enqueued[0]["options"]
    item = w.items[0]
    assert item["status"] == "done" and item["timings"]["prebuild"]["built"] == 1
    assert item["message"] == "meshes pre-built 2 of 2 (1 cached)"


@pytest.mark.asyncio
async def test_no_pre_build_unless_ticked_or_when_nothing_published(world, prebuilt):  # noqa: F811
    w = world(fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}})
    await sweeps._drive(w.ctx, None, SWEEP_RUN, scope_obj=Scope.shared(), spec=SPEC, entry=SWEEP_ENTRY,
                        collections=["ALPHA"], user=None)  # fmt: skip
    assert prebuilt == [] and "prebuild" not in w.items[0]["timings"]

    w = world(fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}}, fail_publish=True)
    run = {**SWEEP_RUN, "settings": {"sweep_target": "tree", "prebuild_meshes": True}}
    await sweeps._drive(w.ctx, None, run, scope_obj=Scope.shared(), spec=SPEC, entry=SWEEP_ENTRY,
                        collections=["ALPHA"], user=None)  # fmt: skip
    assert prebuilt == [] and w.items[0]["timings"]["prebuild"]["reason"].startswith("the collection did not publish")


@pytest.mark.asyncio
async def test_a_failed_geometry_target_skips_the_pre_build_with_the_reason(target_world, prebuilt):  # noqa: F811
    w = target_world(fetch_summaries={"ALPHA/tree": {"asset_staging_id": "s-t"}, "ALPHA/geom": {"fail": True}})
    run = {**SWEEP_RUN, "settings": {"sweep_target": ["tree", "geom"], "prebuild_meshes": True}}
    await sweeps._sweep_one(w.ctx, None, run, 0, "ALPHA", scope_obj=Scope.shared(), spec=MULTI_SPEC,
                            entry=MULTI_ENTRY, user=None)  # fmt: skip
    assert prebuilt == []
    assert w.items[0]["timings"]["prebuild"]["reason"] == "geom did not publish"
    # Only bounds failing: the geometry is there, so the meshes are built.
    w = target_world(fetch_summaries={"ALPHA/geom": {"asset_staging_id": "s-g"}, "ALPHA/bounds": {"fail": True}})
    run = {**SWEEP_RUN, "settings": {"sweep_target": ["geom", "bounds"], "prebuild_meshes": True}}
    await sweeps._sweep_one(w.ctx, None, run, 0, "ALPHA", scope_obj=Scope.shared(), spec=MULTI_SPEC,
                            entry=MULTI_ENTRY, user=None)  # fmt: skip
    assert prebuilt == ["ALPHA"]


def test_which_targets_carry_geometry():
    assert (
        sweeps.geometry_blocked({}, {"tree": {"status": "done"}, "geom": {"status": "error"}}) == "geom did not publish"
    )
    assert sweeps.geometry_blocked({}, {"geom": {"status": "unchanged"}, "bounds": {"status": "error"}}) is None
    assert (
        sweeps.geometry_blocked({"geometry_targets": ["mesh"]}, {"mesh": {"status": "skipped"}})
        == "mesh did not publish"
    )
    assert sweeps.geometry_blocked({}, {"tree": {"status": "error"}}) == "nothing published"


# Fixtures shared with the sweep tests.
from tests.comms.rest.test_asset_sweeps import target_world, world  # noqa: E402, F401
