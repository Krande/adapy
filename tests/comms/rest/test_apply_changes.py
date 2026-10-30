"""Apply changes (the ``apply-changes`` kind of ``asset_schedules``; docs/design/apply-changes.md).

Always-on tests drive one collection with a fake transport, storage and DB, check the declaration's
multi choice, the answer's validation, the publish job's patch roles with fakes, and the patch files'
parsing. The patch SQL itself needs Postgres (ADA_TEST_POSTGRES_URL) and is exercised there.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
import types
import uuid
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi import HTTPException

from ada.comms.rest import db as dbm
from ada.comms.rest.db import attribute_index as ai
from ada.comms.rest.db import node_bounds as nb
from ada.comms.rest.db import node_relations as nr
from ada.comms.rest.formats import asset_publish as ap
from ada.comms.rest.routes import asset_apply_changes as apply
from ada.comms.rest.routes import asset_schedules as sched
from ada.comms.rest.routes import asset_sweeps as sweeps
from ada.comms.rest.scope import Scope

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

TARGETS = ["attributes", "geometry", "bounds", "relations"]
ENTRY = {
    "id": "apply-changes",
    "kind": "apply-changes",
    "label": "Apply changes",
    "options": {"action": "apply-changes", "schema_version": 1},
    "collection_option": "project",
    "settings": [{"name": "apply_targets"}, {"name": "exclude_users", "choices": "change_users"}],
    "parallel": 5,
}
SPEC = {
    "slug": "vendor-plugin",
    "asset_provider_id": "vendor",
    "asset_collections_field": "projects",
    "projects": ["ALPHA", "BETA"],
    "worker_capability": "vendor",
    "job_options": [
        {
            "name": "apply_targets",
            "type": "enum",
            "multiple": True,
            "title": "Apply to",
            "enum": TARGETS,
            "labels": {
                "attributes": "Attributes",
                "geometry": "Geometry",
                "bounds": "Node bounds",
                "relations": "Relations",
            },
            "default": TARGETS,
            "applies_to": ["apply-changes"],
        },
        {"name": "exclude_users", "type": "string_list"},
    ],
    "asset_schedules": [ENTRY, {"id": "loose", "kind": "apply-changes", "options": {}}],  # no collection_option
}


# --- the declaration --------------------------------------------------------------------------


def test_apply_changes_is_a_kind_that_needs_a_collection_option():
    assert [e["id"] for e in sched.schedule_entries(SPEC)] == ["apply-changes"]


def test_a_multiple_enum_says_so_and_defaults_to_a_list():
    [targets, users, _missing, _prebuild] = sched.entry_settings(SPEC, ENTRY)
    assert targets["multiple"] is True and targets["default"] == TARGETS
    assert [c["value"] for c in targets["choices"]] == TARGETS and targets["choices"][2]["label"] == "Node bounds"
    assert users["multiple"] is False and users["source"] == "change_users"


@pytest.mark.asyncio
async def test_build_options_keeps_a_multi_choice_a_list_and_defaults_it():
    kw = dict(scope="providers", provider="vendor", collection=None)
    row = await sched.build_options(None, SPEC, ENTRY, settings={"apply_targets": ["bounds", "attributes"]}, **kw)
    # No collection: an apply-changes row may run every collection, like a sweep's.
    assert row == {
        "action": "apply-changes",
        "schema_version": 1,
        "apply_targets": ["bounds", "attributes"],
        "missing_full_export": "warn",  # core's own setting, stored at its default
    }
    unset = await sched.build_options(None, SPEC, ENTRY, settings={}, **kw)
    assert unset["apply_targets"] == TARGETS and unset["apply_targets"] is not SPEC["job_options"][0]["default"]
    one = await sched.build_options(None, SPEC, ENTRY, settings={"apply_targets": "geometry"}, collection="ALPHA",
                                    scope="providers", provider="vendor")  # fmt: skip
    assert one["apply_targets"] == ["geometry"] and one["project"] == "ALPHA"
    with pytest.raises(HTTPException) as exc:
        await sched.build_options(None, SPEC, ENTRY, settings={"apply_targets": []}, **kw)
    assert exc.value.status_code == 400 and "tick at least one" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await sched.build_options(None, SPEC, ENTRY, settings={"apply_targets": ["geometry", "colour"]}, **kw)
    assert exc.value.status_code == 400 and "colour" in exc.value.detail
    # The firing path (validate=False) never refuses: an empty stored list takes the default.
    fired = await sched.build_options(None, SPEC, ENTRY, settings={"apply_targets": []}, validate=False, **kw)
    assert fired["apply_targets"] == TARGETS


# --- the answer -------------------------------------------------------------------------------


def _answer(**block):
    return {
        "apply_changes": {
            "schema": "ada.assets/apply-changes@1",
            "since": "2026-10-29T02:00:00Z",
            "next_cursor": "2026-10-30T02:00:03Z",
            "baseline": False,
            "targets": TARGETS,
            "counts": {"changed": 3, "inserted": 1, "deleted": 2, "unplaceable": 0},
            "nothing": False,
            **block,
        }
    }


def test_read_answer_checks_the_block():
    got = apply.read_answer(_answer())
    assert got["next_cursor"] == "2026-10-30T02:00:03Z" and got["counts"]["deleted"] == 2 and not got["baseline"]
    with pytest.raises(apply.AnswerError, match="without an apply_changes block"):
        apply.read_answer({"asset_staging_id": "x"})
    with pytest.raises(apply.AnswerError, match="schema"):
        apply.read_answer(_answer(schema="ada.assets/apply-changes@2"))
    with pytest.raises(apply.AnswerError, match="next_cursor"):
        apply.read_answer(_answer(next_cursor=""))
    assert apply.read_answer(_answer(counts={"changed": "x"}))["counts"]["changed"] == 0


def test_the_api_names_the_roles_exactly_as_the_publish_handler_does():
    assert apply.PATCH_ROLES == ap.PATCH_ROLES and apply.CORE_ROLES == ap._CORE_ROLES


def test_role_outcomes_report_patches_as_published_and_geometry_as_applied():
    staged = {
        "csg.db": "assets/_staging/s/csg.db",
        "attribute-index-patch.ndjson.gz": "k1",
        "node-bounds-patch.ndjson.gz": "k2",
        "hierarchy-patch.json": "k3",
    }
    publish = {
        "subjects": ["a", "b"],
        "patches": {
            "attribute-index-patch.ndjson.gz": {"status": "applied", "upserted": 4, "deleted": 1, "timings": {}},
            "node-bounds-patch.ndjson.gz": {"status": "refused", "warning": "no baseline for vendor/alpha"},
        },
    }
    out = apply.role_outcomes(staged, publish)
    assert out["csg.db"] == {"status": "applied", "subjects": 2}
    assert out["attribute-index-patch.ndjson.gz"] == {"status": "applied", "upserted": 4, "deleted": 1}
    assert out["node-bounds-patch.ndjson.gz"]["status"] == "refused"
    assert out["hierarchy-patch.json"]["status"] == "failed"  # not reported: not applied


# --- driving one collection -------------------------------------------------------------------


def _manifest_key(collection: str, revision: str = "20260101T000000Z") -> str:
    return f"assets/{collection}/{collection}/{revision}/asset.json"


class _World:
    def __init__(
        self,
        *,
        answers: dict[str, dict],
        publish_patches: dict | None = None,
        fail_publish=False,
        exported: tuple[str, ...] | None = None,
    ):
        self.answers = answers
        self.publish_patches = publish_patches or {}
        self.fail_publish = fail_publish
        self.blobs: dict[str, bytes] = {}
        # A full export in the store of every answering collection, unless told otherwise -- under
        # the store's lower-case spelling, as a provider offering ALPHA publishes it.
        for c in answers if exported is None else exported:
            self.blobs[_manifest_key(c.lower())] = json.dumps({"provider": "vendor"}).encode()
        self.listings = 0
        self.warnings: list[str] = []
        self.summary: dict = {}
        self.jobs_status: dict[str, str] = {}
        self.enqueued: list[dict] = []
        self.published: list[dict] = []
        self.items: dict[int, dict] = {}
        self.cursors: dict[tuple[str, str], str] = {}
        self.run_status = "running"
        self.finished = None
        self.ctx = SimpleNamespace(
            jobs=SimpleNamespace(status=self._status, cancel=self._cancel),
            storage=SimpleNamespace(get_bytes=self._get, list_prefix=self._list, list_child_names=self._children),
        )

    async def _children(self, scope, prefix):
        self.listings += 1
        return sorted(
            {k[len(prefix) :].split("/", 1)[0] for k in self.blobs if k.startswith(prefix) and "/" in k[len(prefix) :]}
        )

    async def add_warnings(self, pool, run_id, warnings):
        self.warnings += [w for w in warnings if w not in self.warnings]

    async def merge_summary(self, pool, run_id, summary):
        self.summary.update(summary)

    async def get_run(self, pool, run_id):
        # The items as the database lists them; a resumed run's earlier driver left BETA out.
        return {"id": run_id, "items": list(self.db_items)}

    db_items: tuple = ()

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
        summary = self.answers[kw["options"]["project"]]
        self.blobs[kw["derived_key"]] = gzip.compress(json.dumps(summary).encode())
        if summary.get("asset_staging_id"):
            sid = summary["asset_staging_id"]
            self.blobs[f"assets/_staging/{sid}/csg.db"] = b"x"
            for role in self.publish_patches:
                self.blobs[f"assets/_staging/{sid}/{role}"] = b"x"
        self.jobs_status[job_id] = "done"
        return job_id

    async def publish(self, ctx, scope_obj, *, provider, staged, user, **kw):
        self.published.append({"provider": provider, "staged": staged, "scope": scope_obj})
        job_id, key = f"publish-{len(self.published)}", f"_derived/pub/{len(self.published)}.json"
        self.jobs_status[job_id] = "error" if self.fail_publish else "done"
        self.blobs[key] = json.dumps(
            {"revision": "r1", "subjects": ["e1"], "patches": self.publish_patches, "timings": {"write_ms": 3}}
        ).encode()
        return job_id, key

    async def update_item(self, pool, run_id, position, **fields):
        self.items.setdefault(position, {}).update(fields)

    async def heartbeat(self, pool, run_id):
        return self.run_status

    async def item_status(self, pool, run_id, position):
        return self.items.get(position, {}).get("status", "pending")

    async def finish(self, pool, run_id, *, status, error=None):
        self.finished = (status, error)

    async def skip(self, pool, run_id, reason):
        pass

    async def get_cursor(self, pool, *, provider, collection):
        c = self.cursors.get((provider, collection.lower()))
        return {"cursor": c, "applied_at": "then"} if c else None

    async def set_cursor(self, pool, *, provider, collection, cursor, run_id=None):
        self.cursors[(provider, collection.lower())] = cursor
        return {"cursor": cursor}


@pytest.fixture
def world(monkeypatch):
    def make(**kw) -> _World:
        w = _World(**kw)
        monkeypatch.setattr(apply, "enqueue_plugin_job", w.enqueue)
        monkeypatch.setattr(apply, "submit_asset_publish", w.publish)
        monkeypatch.setattr(dbm, "update_asset_sweep_item", w.update_item)
        monkeypatch.setattr(dbm, "heartbeat_asset_sweep_run", w.heartbeat)
        monkeypatch.setattr(dbm, "finish_asset_sweep_run", w.finish)
        monkeypatch.setattr(dbm, "skip_pending_asset_sweep_items", w.skip)
        monkeypatch.setattr(dbm, "asset_sweep_item_status", w.item_status)
        monkeypatch.setattr(dbm, "get_asset_apply_cursor", w.get_cursor)
        monkeypatch.setattr(dbm, "set_asset_apply_cursor", w.set_cursor)
        monkeypatch.setattr(dbm, "add_asset_sweep_run_warnings", w.add_warnings)
        monkeypatch.setattr(dbm, "merge_asset_sweep_run_summary", w.merge_summary)
        monkeypatch.setattr(dbm, "get_asset_sweep_run", w.get_run)

        async def claim(pool, run_id, position, *, provider, collection):
            status = await w.item_status(pool, run_id, position)
            return {"claimed": status in ("pending", "waiting"), "status": status, "holder": None}

        async def acquire(pool, run_id, position, *, provider, cap):
            return True

        async def release(pool, run_id, position):
            return None

        async def no_runs(pool, **kw):
            return []

        monkeypatch.setattr(dbm, "claim_asset_sweep_item", claim)
        monkeypatch.setattr(dbm, "acquire_asset_worker_slot", acquire)
        monkeypatch.setattr(dbm, "release_asset_worker_slot", release)
        monkeypatch.setattr(dbm, "list_asset_sweep_runs", no_runs)
        monkeypatch.setattr(sweeps, "POLL_INTERVAL_S", 0)
        return w

    return make


RUN = {"id": "run-1", "provider": "vendor", "plugin_id": "vendor-plugin",
       "settings": {"apply_targets": ["attributes", "bounds"], "exclude_users": ["BATCH"]}}  # fmt: skip
STORE = Scope.providers()


async def _one(w, collection="ALPHA", run=RUN):
    return await apply.apply_one(w.ctx, None, run, 0, collection, scope_obj=STORE, spec=SPEC, entry=ENTRY, user=None)


@pytest.mark.asyncio
async def test_the_first_run_is_a_baseline_that_only_records_the_cursor(world):
    w = world(answers={"ALPHA": _answer(since=None, baseline=True, counts={})})
    assert await _one(w)
    [job] = w.enqueued
    opts = job["options"]
    assert opts["action"] == "apply-changes" and opts["schema_version"] == 1 and opts["project"] == "ALPHA"
    assert opts["since"] is None and opts["apply_targets"] == ["attributes", "bounds"]
    assert opts["exclude_users"] == ["BATCH"] and "requested_at" in opts
    assert job["scope_obj"] == STORE and job["derived_key"] == sched.job_keys("vendor-plugin", opts)[0]
    assert w.published == []
    assert w.cursors == {("vendor", "alpha"): "2026-10-30T02:00:03Z"}
    item = w.items[0]
    assert item["status"] == "done" and item["message"].startswith("baseline")
    assert item["timings"]["apply_changes"]["baseline"] is True and item["timings"]["apply_changes"]["advanced"]
    assert {"queued_ms", "fetch_ms", "total_ms"} <= set(item["timings"])


@pytest.mark.asyncio
async def test_changes_are_published_and_then_the_cursor_advances(world):
    patches = {
        "attribute-index-patch.ndjson.gz": {"status": "applied", "upserted": 4, "deleted": 2},
        "hierarchy-patch.json": {"status": "applied", "upserted": 1, "deleted": 2},
    }
    w = world(answers={"ALPHA": {**_answer(), "asset_staging_id": "s-1", "timings": {"changes_ms": 9}}},
              publish_patches=patches)  # fmt: skip
    w.cursors[("vendor", "alpha")] = "2026-10-29T02:00:00Z"
    assert await _one(w)
    assert w.enqueued[0]["options"]["since"] == "2026-10-29T02:00:00Z"
    [pub] = w.published
    assert pub["provider"] == "vendor" and pub["scope"] == STORE and set(pub["staged"]) == {"csg.db", *patches}
    assert w.cursors[("vendor", "alpha")] == "2026-10-30T02:00:03Z"
    item = w.items[0]
    t = item["timings"]
    assert item["status"] == "done" and item["revision"] == "r1"
    assert t["roles"]["csg.db"] == {"status": "applied", "subjects": 1}
    assert t["roles"]["attribute-index-patch.ndjson.gz"]["upserted"] == 4
    assert t["apply_changes"]["counts"]["changed"] == 3 and t["provider"] == {"changes_ms": 9}
    assert {"publish_ms", "publish"} <= set(t) and "3 changed, 1 inserted, 2 deleted" in item["message"]


@pytest.mark.asyncio
async def test_a_refused_patch_warns_and_still_advances(world):
    patches = {"node-bounds-patch.ndjson.gz": {"status": "refused", "warning": "no baseline for vendor/alpha"}}
    w = world(answers={"ALPHA": {**_answer(), "asset_staging_id": "s-1"}}, publish_patches=patches)
    assert await _one(w)
    assert w.cursors and "refused (no baseline): node-bounds-patch.ndjson.gz" in w.items[0]["message"]


@pytest.mark.asyncio
async def test_a_failed_patch_keeps_the_cursor_and_fails_the_item(world):
    patches = {"attribute-index-patch.ndjson.gz": {"status": "failed", "error": "deadlock"}}
    w = world(answers={"ALPHA": {**_answer(), "asset_staging_id": "s-1"}}, publish_patches=patches)
    w.cursors[("vendor", "alpha")] = "old"
    assert not await _one(w)
    assert w.cursors[("vendor", "alpha")] == "old"
    item = w.items[0]
    assert item["status"] == "error" and "deadlock" in item["error"] and "cursor kept" in item["error"]
    assert item["timings"]["apply_changes"]["advanced"] is False


@pytest.mark.asyncio
async def test_a_failed_publish_or_a_bad_answer_keeps_the_cursor(world):
    w = world(answers={"ALPHA": {**_answer(), "asset_staging_id": "s-1"}}, fail_publish=True)
    assert not await _one(w)
    assert w.cursors == {} and w.items[0]["status"] == "error" and w.items[0]["error"] == "boom"

    w = world(answers={"ALPHA": {"asset_staging_id": "s-1"}})
    assert not await _one(w)
    assert w.cursors == {} and "apply_changes block" in w.items[0]["error"] and w.published == []

    w = world(answers={"ALPHA": _answer()})  # changes counted, nothing staged
    assert not await _one(w)
    assert w.cursors == {} and "staged nothing" in w.items[0]["error"]


@pytest.mark.asyncio
async def test_nothing_changed_advances_without_a_publish(world):
    w = world(answers={"ALPHA": _answer(nothing=True, counts={})})
    w.cursors[("vendor", "alpha")] = "2026-10-29T02:00:00Z"
    assert await _one(w)
    assert w.published == [] and w.cursors[("vendor", "alpha")] == "2026-10-30T02:00:03Z"
    assert w.items[0]["status"] == "nothing"


@pytest.mark.asyncio
async def test_unset_targets_send_the_declared_default(world):
    w = world(answers={"ALPHA": _answer(nothing=True)})
    assert await _one(w, run={**RUN, "settings": {}})
    opts = w.enqueued[0]["options"]
    assert opts["apply_targets"] == TARGETS and opts["exclude_users"] == []


@pytest.mark.asyncio
async def test_the_sweep_driver_runs_apply_changes_per_collection(world):
    w = world(answers={"ALPHA": _answer(baseline=True), "BETA": _answer(nothing=True)})
    await sweeps._drive(
        w.ctx, None, RUN, scope_obj=STORE, spec=SPEC, entry=ENTRY, collections=["ALPHA", "BETA"], user=None
    )
    assert [e["options"]["project"] for e in w.enqueued] == ["ALPHA", "BETA"]
    assert set(w.cursors) == {("vendor", "alpha"), ("vendor", "beta")} and w.finished == ("done", None)


# --- no full export, no apply ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_offers_and_checks_its_own_missing_full_export_setting():
    core = sched.entry_settings(SPEC, ENTRY)[-2]
    assert core["name"] == "missing_full_export" and core["source"] == "enum" and core["default"] == "warn"
    assert [(c["value"], c["label"]) for c in core["choices"]] == [
        ("warn", "Warn only"),
        ("export", "Queue a full project export"),
    ]
    kw = dict(scope="providers", provider="vendor", collection=None)
    opts = await sched.build_options(None, SPEC, ENTRY, settings={"missing_full_export": "export"}, **kw)
    assert opts["missing_full_export"] == "export"
    assert sched.settings_of(ENTRY, opts)["missing_full_export"] == "export"  # kept on the schedule
    with pytest.raises(HTTPException):
        await sched.build_options(None, SPEC, ENTRY, settings={"missing_full_export": "delete"}, **kw)
    # Never offered on another kind.
    assert "missing_full_export" not in {s["name"] for s in sched.entry_settings(SPEC, {**ENTRY, "kind": "sweep"})}


@pytest.mark.asyncio
async def test_the_setting_is_never_sent_to_the_provider(world):
    w = world(answers={"ALPHA": _answer(nothing=True)})
    assert await _one(w, run={**RUN, "settings": {**RUN["settings"], "missing_full_export": "export"}})
    assert "missing_full_export" not in w.enqueued[0]["options"]


@pytest.mark.asyncio
async def test_a_collection_without_a_full_export_is_not_asked_and_gets_no_cursor(world):
    w = world(answers={"ALPHA": _answer(baseline=True)}, exported=())
    assert await _one(w)
    assert w.enqueued == [] and w.cursors == {}
    item = w.items[0]
    assert item["status"] == "no_full_export"
    assert item["message"].startswith("ALPHA has not had a full project export yet")
    assert w.warnings == ["ALPHA has not had a full project export yet"]


@pytest.mark.asyncio
async def test_a_full_export_must_be_this_providers_and_complete():
    async def check(blobs):
        storage = SimpleNamespace(list_child_names=None, get_bytes=None)

        async def children(scope, prefix):
            return sorted({k[len(prefix) :].split("/", 1)[0] for k in blobs if k.startswith(prefix)})

        async def get(scope, key):
            return blobs[key]

        storage.list_child_names, storage.get_bytes = children, get
        return await apply.has_full_export(
            SimpleNamespace(storage=storage), STORE, provider="vendor", collection="ALPHA", state=apply.ApplyRunState()
        )

    other = json.dumps({"provider": "other"}).encode()
    mine = json.dumps({"provider": "vendor"}).encode()
    assert await check({_manifest_key("alpha"): mine})  # the store's own spelling, matched case-insensitively
    assert not await check({_manifest_key("alpha"): other})
    assert not await check({"assets/alpha/alpha/20260101T000000Z/tree.json": b"{}"})  # no manifest: incomplete
    assert not await check({"assets/alpha/site-1/20260101T000000Z/asset.json": mine})  # not the index subject
    assert await check(
        {_manifest_key("Alpha", "20260101T000000Z"): other, _manifest_key("Alpha", "20250101T000000Z"): mine}
    )


@pytest.mark.asyncio
async def test_the_store_is_listed_once_per_run(world):
    w = world(answers={"ALPHA": _answer(nothing=True), "BETA": _answer(nothing=True)}, exported=("ALPHA",))
    state = apply.ApplyRunState()
    for position, c in enumerate(["ALPHA", "BETA", "ALPHA"]):
        await apply.apply_one(w.ctx, None, RUN, position, c, scope_obj=STORE, spec=SPEC, entry=ENTRY, user=None,
                              run_state=state)  # fmt: skip
    # assets/ once; ALPHA's subjects and revisions once (cached after); BETA is not in the store.
    assert w.listings == 3
    assert [e["options"]["project"] for e in w.enqueued] == ["ALPHA", "ALPHA"] and state.missing == ["BETA"]


@pytest.mark.asyncio
async def test_the_providers_own_no_baseline_answer_keeps_the_cursor(world):
    answer = {**_answer(), "warnings": ["no baseline for collection 'alpha'; run a full export first"]}
    w = world(answers={"ALPHA": answer})
    w.cursors[("vendor", "alpha")] = "old"
    assert await _one(w)
    assert w.cursors[("vendor", "alpha")] == "old" and w.items[0]["status"] == "no_full_export"
    assert "the provider found no published index" in w.items[0]["message"] and w.warnings


@pytest.mark.asyncio
async def test_a_refused_tree_or_index_patch_keeps_the_cursor(world):
    patches = {"hierarchy-patch.json": {"status": "refused", "warning": "no baseline for vendor/alpha"}}
    w = world(answers={"ALPHA": {**_answer(), "asset_staging_id": "s-1"}}, publish_patches=patches)
    w.cursors[("vendor", "alpha")] = "old"
    assert await _one(w)
    assert w.cursors[("vendor", "alpha")] == "old" and w.items[0]["status"] == "no_full_export"


@pytest.mark.asyncio
async def test_export_mode_queues_one_full_export_of_what_was_left_out_after_the_run(world, monkeypatch):
    started = []

    async def start(ctx, pool, **kw):
        # The run is finished before the follow-up starts: the guard is free.
        assert w.finished is not None
        started.append(kw)
        return {"id": "follow-1"}

    monkeypatch.setattr(sweeps, "start_sweep", start)
    w = world(answers={"ALPHA": _answer(nothing=True), "BETA": {}, "GAMMA": {}}, exported=("ALPHA",))
    run = {**RUN, "settings": {**RUN["settings"], "missing_full_export": "export"}}
    await sweeps._drive(
        w.ctx, None, run, scope_obj=STORE, spec=SPEC, entry=ENTRY, collections=["ALPHA", "BETA", "GAMMA"], user=None
    )
    [kw] = started
    assert kw["job"] == sweeps.COLLECTION_REQUEST and kw["collections"] == ["BETA", "GAMMA"]
    assert kw["via"] == "apply-changes" and kw["summary"] == {"apply_changes_run_id": "run-1"}
    assert kw["scope_obj"] == STORE
    assert w.summary == {"queued_full_exports": {"run_id": "follow-1", "collections": ["BETA", "GAMMA"]}}
    assert w.finished == ("done", None) and len(w.warnings) == 2
    assert "a full project export is queued" in w.items[1]["message"]


@pytest.mark.asyncio
async def test_export_mode_reads_what_an_earlier_driver_left_out(world, monkeypatch):
    started = []

    async def start(ctx, pool, **kw):
        started.append(kw)
        return {"id": "follow-1"}

    monkeypatch.setattr(sweeps, "start_sweep", start)
    w = world(answers={"ALPHA": _answer(nothing=True)})
    w.db_items = ({"collection": "BETA", "status": "no_full_export"}, {"collection": "ALPHA", "status": "done"})
    run = {**RUN, "settings": {"missing_full_export": "export"}}
    await apply.after_apply_run(w.ctx, None, run, apply.ApplyRunState(), user=None)
    assert started[0]["collections"] == ["BETA"]


@pytest.mark.asyncio
async def test_export_mode_falls_back_to_a_warning_without_a_collection_request(world, monkeypatch):
    async def refuse(ctx, pool, **kw):
        raise sweeps.SweepRefused("provider 'vendor' declares no collection request")

    monkeypatch.setattr(sweeps, "start_sweep", refuse)
    w = world(answers={"BETA": {}}, exported=())
    state = apply.ApplyRunState(missing=["BETA"])
    out = await apply.after_apply_run(
        w.ctx, None, {**RUN, "settings": {"missing_full_export": "export"}}, state, user=None
    )
    assert out is None and w.summary == {}
    assert "declares no full export" in w.warnings[-1] and "warned only" in w.warnings[-1]


@pytest.mark.asyncio
async def test_warn_mode_queues_nothing(world, monkeypatch):
    async def start(ctx, pool, **kw):
        raise AssertionError("warn only")

    monkeypatch.setattr(sweeps, "start_sweep", start)
    w = world(answers={"BETA": {}}, exported=())
    assert await apply.after_apply_run(w.ctx, None, RUN, apply.ApplyRunState(missing=["BETA"]), user=None) is None


@pytest.mark.asyncio
async def test_an_apply_changes_start_runs_the_named_collection_without_the_default_list(monkeypatch):
    inserted, spawned = {}, {}

    async def specs(ctx):
        return {"vendor": [SPEC]}

    async def idle(pool, provider):
        return None

    async def setting(pool, key):
        raise AssertionError("the default sweep list does not apply to apply-changes")

    async def insert(pool, **kw):
        inserted.update(kw)
        return {"id": "r1", **kw}

    def spawn(coro, name):
        spawned.update(coro.cr_frame.f_locals)
        coro.close()

    monkeypatch.setattr(sweeps, "provider_specs", specs)
    monkeypatch.setattr(sweeps, "spawn", spawn)
    monkeypatch.setattr(dbm, "running_asset_sweep", idle)
    monkeypatch.setattr(dbm, "get_setting", setting)
    monkeypatch.setattr(dbm, "insert_asset_sweep_run", insert)
    kw = dict(
        scope="providers", scope_obj=STORE, provider="vendor", job="apply-changes", settings={}, user=None, via="user"
    )
    await sweeps.start_sweep(None, None, **kw)
    assert inserted["collections"] == ["ALPHA", "BETA"] and inserted["excluded"] == frozenset()
    await sweeps.start_sweep(None, None, collections=["alpha"], **kw)
    assert inserted["collections"] == ["ALPHA"] and spawned["entry"]["kind"] == "apply-changes"


@pytest.mark.asyncio
async def test_the_run_read_lifts_the_answer_onto_the_item(monkeypatch):
    async def get_run(pool, run_id):
        return {"id": run_id, "items": [{"status": "done", "fetch_job_id": None, "publish_job_id": None,
                "timings": {"total_ms": 1, "apply_changes": {"nothing": True}, "roles": {"csg.db": {}}}}]}  # fmt: skip

    monkeypatch.setattr(dbm, "get_asset_sweep_run", get_run)
    run = await sweeps.sweep_with_progress(None, None, "r1")
    assert run["items"][0]["apply_changes"] == {"nothing": True} and run["items"][0]["roles"] == {"csg.db": {}}


@pytest.mark.asyncio
async def test_resetting_a_cursor(monkeypatch):
    seen = []

    async def delete(pool, *, provider, collection):
        seen.append((provider, collection))
        return True

    monkeypatch.setattr(dbm, "delete_asset_apply_cursor", delete)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=object())))
    resp = await apply.admin_asset_apply_cursor_reset(request, provider="vendor", collection="ALPHA")
    assert json.loads(resp.body) == {"provider": "vendor", "collection": "alpha", "reset": True}
    with pytest.raises(HTTPException):
        await apply.admin_asset_apply_cursor_reset(request, provider="vendor", collection="")


# --- the publish job's patch roles ------------------------------------------------------------


class _Storage:
    def __init__(self, blobs):
        self.blobs = blobs

    async def get_bytes(self, scope, key):
        return self.blobs[key]

    async def open_stream(self, scope, key):
        async def gen():
            yield self.blobs[key]

        return SimpleNamespace(stream=gen())


@pytest.mark.asyncio
async def test_patch_roles_apply_in_order_and_report_each(monkeypatch):
    calls = []
    hp = types.ModuleType("ada.comms.rest.hierarchy_patches")

    async def store_hierarchy_patch(storage, scope, *, provider, collection, doc):
        calls.append(("hierarchy", provider, collection, len(doc["upsert"])))
        return {"stored": "p-1"}

    hp.store_hierarchy_patch = store_hierarchy_patch
    monkeypatch.setitem(sys.modules, "ada.comms.rest.hierarchy_patches", hp)
    import ada.comms.rest as rest_pkg

    monkeypatch.setattr(rest_pkg, "hierarchy_patches", hp, raising=False)

    async def attr_patch(pool, *, scope, provider, chunks):
        calls.append(("attributes", scope, provider))
        return {"collection": "alpha", "upserted": 3, "deleted": 1, "warnings": [], "timings": {"patch_ms": 2}}

    async def bounds_patch(pool, *, scope, provider, chunks):
        calls.append(("bounds",))
        return ai.patch_refused(provider=provider, collection="alpha")

    async def relations_patch(pool, *, scope, provider, chunks):
        raise RuntimeError("lock timeout")

    monkeypatch.setitem(ap._NDJSON_PATCHES, ap.ATTRIBUTE_INDEX_PATCH_ROLE, attr_patch)
    monkeypatch.setitem(ap._NDJSON_PATCHES, ap.NODE_BOUNDS_PATCH_ROLE, bounds_patch)
    monkeypatch.setitem(ap._NDJSON_PATCHES, ap.NODE_RELATIONS_PATCH_ROLE, relations_patch)
    doc = {"schema": "ada.assets/hierarchy-patch@1", "collection": "ALPHA", "cols": [], "upsert": [[1]], "delete": []}
    staged = {
        "csg.db": "assets/_staging/s/csg.db",
        "node-relations-patch.ndjson.gz": "assets/_staging/s/r",
        "node-bounds-patch.ndjson.gz": "assets/_staging/s/b",
        "attribute-index-patch.ndjson.gz": "assets/_staging/s/a",
        "hierarchy-patch.json": "assets/_staging/s/h",
    }
    storage = _Storage({"assets/_staging/s/h": gzip.compress(json.dumps(doc).encode()), **{k: b"" for k in "rba"}})
    out, consumed = await ap.apply_patch_roles(
        staged=staged, scope=STORE, storage=storage, db_pool=object(), provider="vendor", dry_run=False
    )
    assert [c[0] for c in calls] == ["hierarchy", "attributes", "bounds"]  # the tree first
    assert calls[0] == ("hierarchy", "vendor", "alpha", 1) and calls[1] == ("attributes", STORE.prefix(), "vendor")
    assert out["hierarchy-patch.json"]["status"] == "applied"
    assert out["attribute-index-patch.ndjson.gz"]["upserted"] == 3
    assert out["node-bounds-patch.ndjson.gz"]["status"] == "refused"
    assert "no baseline for vendor/alpha; run a full export first" in out["node-bounds-patch.ndjson.gz"]["warning"]
    assert out["node-relations-patch.ndjson.gz"] == {
        **out["node-relations-patch.ndjson.gz"],
        "status": "failed",
        "error": "lock timeout",
    }
    # Applied and refused are consumed; the failed one stays staged.
    assert sorted(consumed) == ["assets/_staging/s/a", "assets/_staging/s/b", "assets/_staging/s/h"]
    assert ap.HIERARCHY_PATCH_ROLE in ap._CORE_ROLES and ap.ATTRIBUTE_INDEX_PATCH_ROLE in ap._CORE_ROLES


@pytest.mark.asyncio
async def test_patch_roles_without_a_database_or_in_a_dry_run_apply_nothing():
    staged = {"node-bounds-patch.ndjson.gz": "k"}
    out, consumed = await ap.apply_patch_roles(
        staged=staged, scope=STORE, storage=_Storage({}), db_pool=None, provider="vendor", dry_run=False
    )
    assert out["node-bounds-patch.ndjson.gz"]["status"] == "skipped" and consumed == []
    out, consumed = await ap.apply_patch_roles(
        staged=staged, scope=STORE, storage=_Storage({}), db_pool=object(), provider="vendor", dry_run=True
    )
    assert out["node-bounds-patch.ndjson.gz"]["status"] == "skipped" and consumed == []


# --- the patch files ----------------------------------------------------------------------------


def _header(kind, **kw):
    return json.dumps({"schema": f"ada.assets/{kind}-patch@1", "provider": "vendor", "collection": "ALPHA",
                       "since": "a", "next_cursor": "b", **kw}).encode()  # fmt: skip


def test_patch_headers_are_checked():
    h = ai.parse_patch_header(_header("node-bounds"), nb.NODE_BOUNDS_PATCH_SCHEMA, nb.NodeBoundsError)
    assert h["collection"] == "alpha" and h["since"] == "a" and h["next_cursor"] == "b"
    with pytest.raises(nb.NodeBoundsError, match="schema"):
        ai.parse_patch_header(_header("node-bounds"), nr.NODE_RELATIONS_PATCH_SCHEMA, nb.NodeBoundsError)
    with pytest.raises(ai.AttributeIndexError, match="collection"):
        ai.parse_patch_header(
            _header("attribute-index", collection=" "), ai.ATTRIBUTE_INDEX_PATCH_SCHEMA, ai.AttributeIndexError
        )
    with pytest.raises(ValueError, match="not JSON"):
        ai.parse_patch_header(b"{", ai.ATTRIBUTE_INDEX_PATCH_SCHEMA)


def test_a_patch_batch_splits_rows_from_deletions_in_file_order():
    lines = [
        json.dumps({"node_id": "n1", "bbox": [0, 0, 0, 1, 1, 1]}).encode(),
        json.dumps({"node": "n2", "deleted": True}).encode(),
        b"not json",
        json.dumps({"node_id": "n3"}).encode(),  # no bbox: skipped by the set's own parser
        json.dumps({"node": "", "deleted": True}).encode(),
        json.dumps({"node_id": "n2", "bbox": [0, 0, 0, 2, 2, 2]}).encode(),
    ]

    def parse(batch):
        return nb.parse_bounds_batch(batch, scope="providers", provider="vendor", collection="alpha")

    plan = ai.plan_patch_batch(lines, parse, 10)
    assert plan.deleted == [("n2", 11)] and plan.skipped == 3
    assert [(r[3], r[-1]) for r in plan.records] == [("n1", 10), ("n2", 15)]


def test_attribute_and_relation_rows_parse_with_their_sets_own_parsers():
    excluded = ai._exclusion([])
    attr = ai.plan_patch_batch(
        [json.dumps({"node_id": "n1", "attrs": {"TYPE": "BEAM", "X": None}}).encode()],
        lambda b: ai._parse_batch(b, excluded, ai._ValueStats()),
        0,
    )
    assert attr.records[0][0] == "n1" and json.loads(attr.records[0][6]) == {"TYPE": "BEAM"}
    rel = ai.plan_patch_batch(
        [json.dumps({"from_node_id": "n1", "relation": "connects_to", "to_node_id": "n9"}).encode()] * 2,
        lambda b: nr.parse_relations_batch(b, scope="providers", provider="vendor", collection="alpha"),
        0,
    )
    assert [(r[3], r[5], r[-1]) for r in rel.records] == [("n1", "connects_to", 0), ("n1", "connects_to", 1)]


class _NoSetConn:
    """A connection whose set lookup finds nothing: the patch must be refused before any write."""

    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetchval(self, sql, *args):
        return None

    async def fetchrow(self, sql, *args):
        return None

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Acq()


async def _chunks(*lines):
    yield gzip.compress(b"\n".join(lines))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn,kind",
    [
        (ai.patch_attribute_index, "attribute-index"),
        (nb.patch_node_bounds, "node-bounds"),
        (nr.patch_node_relations, "node-relations"),
    ],
)
async def test_a_patch_without_a_baseline_is_refused_and_writes_nothing(fn, kind):
    conn = _NoSetConn()
    out = await fn(_Pool(conn), scope="providers", provider="vendor",
                   chunks=_chunks(_header(kind), b'{"node": "n1", "deleted": true}'))  # fmt: skip
    assert out["refused"] is True and out["applied"] is False and out["upserted"] == 0
    assert out["warnings"] == ["no baseline for vendor/alpha; run a full export first"]
    assert not any("CREATE" in s or "DELETE" in s or "INSERT" in s for s in conn.executed)


# --- live Postgres -------------------------------------------------------------------------------


@needs_postgres
@pytest.mark.asyncio
async def test_patches_replace_and_delete_nodes_against_postgres():
    pool = await dbm.init_pool(POSTGRES_URL)
    provider = f"test-{uuid.uuid4().hex[:8]}"
    scope = "providers"
    try:
        full_attr = [
            json.dumps(
                {"schema": ai.ATTRIBUTE_INDEX_SCHEMA, "provider": provider, "collection": "alpha", "revision": "1"}
            ).encode(),
            json.dumps({"node_id": "n1", "attrs": {"TYPE": "BEAM"}}).encode(),
            json.dumps({"node_id": "n2", "attrs": {"TYPE": "PLATE"}}).encode(),
        ]
        await ai.ingest_attribute_index(pool, scope=scope, provider=provider, chunks=_chunks(*full_attr))
        out = await ai.patch_attribute_index(pool, scope=scope, provider=provider, chunks=_chunks(
            _header("attribute-index", provider=provider),
            json.dumps({"node_id": "n1", "attrs": {"TYPE": "COLUMN", "NEW": "x"}}).encode(),
            json.dumps({"node": "n2", "deleted": True}).encode(),
        ))  # fmt: skip
        assert out["applied"] and out["upserted"] == 1 and out["deleted"] == 1 and out["new_keys"] == 1
        row = await pool.fetchrow(
            "SELECT rows, keys FROM attr_collections WHERE scope = $1 AND provider = $2", scope, provider
        )
        assert row["rows"] == 1 and "NEW" in row["keys"]
        values = {r["value"]: r["n"] for r in await pool.fetch(
            "SELECT value, n FROM attr_values v JOIN attr_collections c USING (part_key) "
            "WHERE c.provider = $1 AND v.key = 'TYPE'", provider)}  # fmt: skip
        assert values == {"COLUMN": 1}

        full_bounds = [
            json.dumps({"schema": nb.NODE_BOUNDS_SCHEMA, "provider": provider, "collection": "alpha"}).encode(),
            json.dumps({"node_id": "n1", "bbox": [0, 0, 0, 1, 1, 1]}).encode(),
        ]
        await nb.ingest_node_bounds(pool, scope=scope, provider=provider, chunks=_chunks(*full_bounds))
        out = await nb.patch_node_bounds(pool, scope=scope, provider=provider, chunks=_chunks(
            _header("node-bounds", provider=provider),
            json.dumps({"node_id": "n1", "bbox": [0, 0, 0, 5, 5, 5]}).encode(),
            json.dumps({"node_id": "n3", "bbox": [0, 0, 0, 2, 2, 2]}).encode(),
        ))  # fmt: skip
        assert out["upserted"] == 2 and out["removed_rows"] == 1
        assert (
            await pool.fetchval("SELECT max_x FROM node_bounds WHERE provider = $1 AND node_id = 'n1'", provider) == 5
        )
    finally:
        await ai.drop_attr_collection(pool, scope=scope, provider=provider, collection="alpha")
        await nb.drop_node_bounds(pool, scope=scope, provider=provider, collection="alpha")
        await dbm.close_pool(pool)


# --- the Scheduled jobs listing -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_listing_shows_the_cursors_of_apply_changes_rows(monkeypatch):
    rows = [
        {"id": "s-all", "asset_provider": "vendor", "collection": None, "schedule_job": "apply-changes",
         "schedule_kind": "apply-changes", "enabled": True, "last_fired_at": None, "next_fire_at": None,
         "cron_expr": "0 2 * * *", "options": {"action": "apply-changes", "apply_targets": ["bounds"]},
         "last_skipped_reason": None, "last_job_id": None},
        {"id": "s-beta", "asset_provider": "vendor", "collection": "BETA", "schedule_job": "apply-changes",
         "schedule_kind": "apply-changes", "enabled": True, "last_fired_at": None, "next_fire_at": None,
         "cron_expr": "0 * * * *", "options": {}, "last_skipped_reason": None, "last_job_id": None},
    ]  # fmt: skip

    async def specs(ctx):
        return {"vendor": [{**SPEC, "asset_collections_label": "projects"}]}

    async def schedules(pool, **kw):
        return rows

    async def latest(pool, ids):
        return {}

    async def cursors(pool, provider=None):
        return [{"provider": "vendor", "collection": "alpha", "cursor": "c1", "applied_at": "t1", "run_id": "r"}]

    monkeypatch.setattr(sched, "provider_specs", specs)
    monkeypatch.setattr(dbm, "list_plugin_job_schedules", schedules)
    monkeypatch.setattr(dbm, "latest_asset_change_runs_for_schedules", latest)
    monkeypatch.setattr(dbm, "list_asset_apply_cursors", cursors)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=object())))
    body = json.loads((await sched.admin_asset_schedules(request, None)).body)
    [job] = body["providers"][0]["jobs"]
    assert job["kind"] == "apply-changes" and job["collections_label"] == "projects"
    assert job["settings"][0]["multiple"] is True
    every, beta = body["schedules"]
    assert every["cursors"] == {"alpha": {"cursor": "c1", "applied_at": "t1", "run_id": "r"}}
    assert every["settings"] == {"apply_targets": ["bounds"]} and beta["cursors"] == {}


@pytest.mark.asyncio
async def test_pre_build_meshes_builds_the_published_elements_not_their_ancestors(world, monkeypatch):
    asked = []

    async def prebuild(ctx, pool, run, position, targets, *, user, poll_s):
        asked.extend(targets)
        return {"subjects": len(targets), "cached": 0, "built": len(targets), "failed": 0, "skipped": 0, "ms": 1}

    monkeypatch.setattr(apply, "prebuild", prebuild)
    w = world(answers={"ALPHA": {**_answer(), "asset_staging_id": "s-1"}})
    run = {**RUN, "settings": {**RUN["settings"], "prebuild_meshes": True}}
    assert await _one(w, run=run)
    assert asked == [{"collection": "ALPHA", "subject": "e1", "provider": "vendor"}]
    assert "prebuild_meshes" not in w.enqueued[0]["options"]
    pre = w.items[0]["timings"]["prebuild"]
    assert pre["built"] == 1 and "rebuild on demand" in pre["note"]
    assert "meshes pre-built 1 of 1" in w.items[0]["message"]


def test_a_subtree_deletion_is_planned_apart_from_a_node_deletion():
    lines = [
        json.dumps({"node": "unit-1", "deleted": True, "subtree": True}).encode(),
        json.dumps({"node_id": "unit-1", "bbox": [0, 0, 0, 1, 1, 1]}).encode(),
        json.dumps({"node": "n9", "deleted": True}).encode(),
    ]

    def parse(batch):
        return nb.parse_bounds_batch(batch, scope="providers", provider="vendor", collection="alpha")

    plan = ai.plan_patch_batch(lines, parse, 0)
    assert plan.subtree == [("unit-1", 0)] and plan.deleted == [("n9", 2)]
    assert [(r[3], r[-1]) for r in plan.records] == [("unit-1", 1)]  # written after the subtree line: survives
    sql = ai.subtree_expansion_sql("attr_nodes", "n.part_key = $1")
    # Descendants are deleted at the subtree line's own sequence number, walked by parent_id.
    assert "SELECT d.seq, n.node_id" in sql and "n.parent_id = d.node_id" in sql and "WHERE d.subtree" in sql
    # The template aliases the source itself: a caller passing "attr_nodes n" made "attr_nodes n n".
    assert "JOIN attr_nodes n ON" in sql and " n n " not in sql


def test_the_relations_patch_goes_before_the_attribute_patch():
    roles = list(ap.PATCH_ROLES)
    assert roles.index(nr.NODE_RELATIONS_PATCH_ROLE) < roles.index(ai.ATTRIBUTE_INDEX_PATCH_ROLE)
    assert roles[0] == ap.HIERARCHY_PATCH_ROLE


@needs_postgres
@pytest.mark.asyncio
async def test_a_subtree_patch_removes_a_units_components_against_postgres():
    pool = await dbm.init_pool(POSTGRES_URL)
    provider = f"test-{uuid.uuid4().hex[:8]}"
    scope = "providers"
    try:
        full = [
            json.dumps(
                {"schema": ai.ATTRIBUTE_INDEX_SCHEMA, "provider": provider, "collection": "alpha", "revision": "1"}
            ).encode(),
            json.dumps({"node_id": "unit", "attrs": {"TYPE": "EQUI"}}).encode(),
            json.dumps({"node_id": "c1", "parent_id": "unit", "attrs": {"TYPE": "BOX"}}).encode(),
            json.dumps({"node_id": "c2", "parent_id": "c1", "attrs": {"TYPE": "CYL"}}).encode(),
            json.dumps({"node_id": "other", "attrs": {"TYPE": "PIPE"}}).encode(),
        ]
        await ai.ingest_attribute_index(pool, scope=scope, provider=provider, chunks=_chunks(*full))
        out = await ai.patch_attribute_index(pool, scope=scope, provider=provider, chunks=_chunks(
            _header("attribute-index", provider=provider),
            json.dumps({"node": "unit", "deleted": True, "subtree": True}).encode(),
            json.dumps({"node_id": "unit", "attrs": {"TYPE": "EQUI"}}).encode(),
            json.dumps({"node_id": "c3", "parent_id": "unit", "attrs": {"TYPE": "BOX"}}).encode(),
        ))  # fmt: skip
        assert out["applied"]
        part = await pool.fetchval(
            "SELECT part_key FROM attr_collections WHERE scope = $1 AND provider = $2", scope, provider
        )
        ids = {r["node_id"] for r in await pool.fetch("SELECT node_id FROM attr_nodes WHERE part_key = $1", part)}
        assert ids == {"unit", "c3", "other"}
    finally:
        await ai.drop_attr_collection(pool, scope=scope, provider=provider, collection="alpha")
        await dbm.close_pool(pool)
