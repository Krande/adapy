"""Missing geometry found before an export (the preflight) and requested from the geometry provider
before the export writes its files (``routes/geometry_requests.py`` and the export driver in
``routes/asset_exports.py``).

Fakes only: the queue, the store and the provider's declaration are in-memory; nothing touches a
live database, store or worker.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from tests.comms.rest.test_search_export import _catalog, _spine, _Store

from ada.comms.rest import db as dbm
from ada.comms.rest import search_export as se
from ada.comms.rest.routes import asset_exports as ex
from ada.comms.rest.routes import geometry_requests as greq
from ada.comms.rest.run_driver import RunBeat
from ada.comms.rest.scope import Scope

SPEC = {
    "id": "geo-plugin",
    "asset_provider_id": se.GEOMETRY_PROVIDER,
    "asset_collections_field": "projects",
    "projects": ["ALPHA", "BETA"],
    "requires_admin": True,
    "asset_node_request": {
        "options": {"action": "export-geometry", "with_tree": True},
        "collection_option": "project",
        "node_option": "nodes",
        "label_option": "node_labels",
        "max_nodes": 20,
        "label": "Request geometry",
    },
}
REQ = greq.node_request_of(SPEC)


def _hit(n: int, collection: str = "alpha") -> dict:
    return {
        "provider": "vendor-a",
        "collection": collection,
        "node_id": f"fn-{n}",
        "full_name": f"/STRU-{n}",
        "path": [{"node_id": "fn-site", "full_name": "/SITE-A", "kind": "SITE", "depth": 1}],
        "file": f"out/{collection}/STRU-{n}.ifc",
    }


def _states(hits, state="listed"):
    return [
        greq.HitState(hit=h, state=state, node_id=f"7-{i}" if state == "listed" else None) for i, h in enumerate(hits)
    ]


# --- the leaf functions -------------------------------------------------------------------------------


def test_the_declaration_is_read_as_the_frontend_reads_it():
    assert REQ.plugin_id == "geo-plugin" and REQ.node_option == "nodes" and REQ.label_option == "node_labels"
    assert REQ.max_nodes == 20 and REQ.requires_admin
    assert REQ.collection_name("alpha") == "ALPHA" and REQ.collection_name("gamma") == "gamma"
    assert greq.node_request_of({"id": "x", "asset_node_request": {"collection_option": "p"}}) is None


def test_targets_prefer_the_providers_own_id_and_always_carry_the_full_name():
    listed = greq.HitState(hit=_hit(1), state="listed", node_id="7-1")
    assert greq.request_target(listed) == {"id": "7-1", "label": "/STRU-1"}
    unpublished = greq.HitState(hit={**_hit(2), "full_name": "STRU-2"}, state="unpublished")
    target = greq.request_target(unpublished)
    assert target["id"] == se._node_id_for_full_name("STRU-2") and target["label"] == "/STRU-2"


def test_requests_are_chunked_deduplicated_and_carry_ids_and_labels_index_for_index():
    targets = [{"id": f"n{i}", "label": f"/N{i}"} for i in range(5)] + [{"id": "n0", "label": "/N0"}]
    chunks = greq.request_chunks(targets, 2)
    assert [[t["id"] for t in c] for c in chunks] == [["n0", "n1"], ["n2", "n3"], ["n4"]]
    assert greq.request_estimate(5, 2) == 3 and greq.request_estimate(0) == 0
    # One job per collection by default, above the declared max_nodes (sized for whole sites).
    assert len(greq.request_chunks(targets)) == 1
    options = greq.request_options(REQ, "alpha", chunks[0], extra={"def_dbs": ["X"], "action": "no"}, requested_at="t")
    assert options == {
        "def_dbs": ["X"],
        "action": "export-geometry",  # the declaration wins over the collection's provider options
        "with_tree": True,
        "project": "ALPHA",
        "nodes": ["n0", "n1"],
        "node_labels": ["/N0", "/N1"],
        "requested_at": "t",
    }


def test_hits_are_classified_and_an_unknown_node_says_whether_its_top_level_is_missing():
    tree = _spine("alpha", "alpha", [("7-0", "/SITE-A"), ("7-1", "/STRU-1")], readable=False)
    exact = {"SITE-A": {"7-0": "/SITE-A"}, "STRU-1": {"7-1": "/STRU-1"}}
    index = SimpleNamespace(catalog=lambda c, p: _catalog([tree], exact=exact))
    other_site = {**_hit(9), "path": [{"node_id": "fn-s", "full_name": "/SITE-B", "kind": "SITE", "depth": 1}]}
    [listed, unknown, gone] = greq.classify_hits(index, "alpha", [_hit(1), _hit(2), other_site])
    assert (listed.state, listed.node_id) == ("listed", "7-1")
    assert unknown.state == "unknown" and "is in the provider's tree but" in unknown.message
    assert gone.state == "unknown" and "has no '/SITE-B'" in gone.message and "deferred" in gone.message
    empty = SimpleNamespace(catalog=lambda c, p: _catalog([]))
    assert greq.classify_hits(empty, "alpha", [_hit(1)])[0].state == "unpublished"


# --- the request inside the export run ----------------------------------------------------------------


class _World:
    """The export run's world, with a geometry provider that answers each request job per
    ``answers`` (one summary per job, in order; ``"hold"`` = still running)."""

    def __init__(self, monkeypatch, *, answers, states_for, skip_on_job=None, admin=True):
        self.store = _Store()
        self.items: dict[int, dict] = {}
        self.events: list[str] = []
        self.jobs: dict[str, str] = {}
        self.cancelled: list[str] = []
        self.enqueued: list[dict] = []
        self.published: list[str] = []
        self.export_hits: list[list[str]] = []
        self.finished = None
        self.answers = list(answers)
        self.skip_on_job = skip_on_job
        self.skip_position = None
        self.ctx = SimpleNamespace(
            jobs=SimpleNamespace(
                submit=self.submit, status=self.status, cancel=self.cancel, advertised_specs=self.specs
            ),
            storage=self.store,
            audit=self.audit,
        )

        async def classify(storage, scope_obj, groups, provider=se.GEOMETRY_PROVIDER):
            return [states_for(c, h) for c, h in groups]

        async def provider_options(ctx, scope_obj, provider, collection):
            return {"def_dbs": ["D1"]}

        monkeypatch.setattr(greq, "classify_in_scope", classify)
        monkeypatch.setattr(greq, "_provider_options", provider_options)
        monkeypatch.setattr(greq, "_enqueue", self.enqueue)
        monkeypatch.setattr(greq, "_publish", self.publish)
        monkeypatch.setattr(dbm, "update_asset_export_item", self.update_item)
        monkeypatch.setattr(dbm, "heartbeat_asset_export_run", self.heartbeat)
        monkeypatch.setattr(dbm, "clear_asset_export_skip", self.clear_skip)
        monkeypatch.setattr(dbm, "finish_asset_export_run", self.finish)
        monkeypatch.setattr(dbm, "skip_pending_asset_export_items", self.skip_pending)
        monkeypatch.setattr(ex, "POLL_INTERVAL_S", 0)

    async def specs(self, kind):
        return {"geo-plugin": SPEC}

    async def audit(self, *a, **k):
        return None

    async def enqueue(self, ctx, *, plugin_id, options, scope_obj, user, pool, derived_key, plugin_spec):
        job_id = f"req-{len(self.enqueued)}"
        self.enqueued.append(options)
        self.events.append(f"enqueue {job_id}")
        answer = self.answers.pop(0)
        if answer == "hold":
            self.jobs[job_id] = "running"
        else:
            await self.store.put_bytes(scope_obj, derived_key, gzip.compress(json.dumps(answer).encode()))
            self.jobs[job_id] = "done"
        return job_id

    async def publish(self, ctx, scope_obj, *, provider, staging_id, user):
        job_id = f"pub-{staging_id}"
        self.published.append(staging_id)
        self.events.append(f"publish {staging_id}")
        key = f"_derived/assets/_publish/{staging_id}.json"
        await self.store.put_bytes(scope_obj, key, json.dumps({"revision": f"r-{staging_id}"}).encode())
        self.jobs[job_id] = "done"
        return job_id, key

    async def submit(self, req, before_dispatch=None):
        self.events.append("export")
        hits = req.conversion_options["hits"]
        self.export_hits.append([h["node_id"] for h in hits])
        files = [
            {
                "file": h["file"],
                "node_id": h["node_id"],
                "full_name": h["full_name"],
                "status": "no_geometry",
                "error": "x",
            }
            for h in hits
        ]
        summary = {
            "files": files,
            "counts": {"written": 0, "no_geometry": len(files), "failed": 0},
            "timings": {},
            "warnings": [],
        }
        await self.store.put_bytes(req.scope, req.derived_key, gzip.compress(json.dumps(summary).encode()))
        self.jobs["export"] = "done"
        return SimpleNamespace(job_id="export")

    async def status(self, job_id):
        st = self.jobs.get(job_id)
        return None if st is None else SimpleNamespace(status=st, payload={})

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.jobs[job_id] = "cancelled"
        return True

    async def update_item(self, pool, run_id, position, **fields):
        self.items.setdefault(position, {}).update(fields)
        if self.skip_on_job is not None and fields.get("job_id") == self.skip_on_job:
            self.skip_position = position

    async def heartbeat(self, pool, run_id):
        return RunBeat("running", self.skip_position, "Tester")

    async def clear_skip(self, pool, run_id, position):
        self.skip_position = None

    async def finish(self, pool, run_id, *, status, error=None):
        self.finished = (status, error)

    async def skip_pending(self, pool, run_id, reason):
        pass

    def drive(self, batches, *, request=True):
        asyncio.run(
            ex.drive_export(
                self.ctx,
                None,
                {"id": "run-1", "query": {}, "created_at": None},
                scope_obj=Scope.shared(),
                target=Scope.shared(),
                batches=batches,
                job_opts={
                    "run_id": "run-1",
                    "target_folder": "out",
                    "target_scope": {"kind": "shared", "id": None},
                    "request_missing_geometry": request,
                },
                user=None,
            )
        )


def _ok(staging, mode="adopted"):
    return {"asset_staging_id": staging, "login": {"mode": mode}, "timings": {"login_ms": 5, "export_ms": 9}}


def test_hits_from_a_scope_the_user_cannot_publish_into_are_reported_not_requested(monkeypatch):
    """Shared in from a project this caller is no member of (here: no database, so no member of
    any): classified THERE, nothing requested, the item says where to ask instead."""
    hits = [{**_hit(i), "origin": "projects/p-9"} for i in range(2)]
    w = _World(monkeypatch, answers=[], states_for=lambda c, hs: _states(hs))
    seen = []

    async def classify(storage, scope_obj, groups, provider=se.GEOMETRY_PROVIDER):
        seen.append(scope_obj)
        return [_states(h) for _, h in groups]

    monkeypatch.setattr(greq, "classify_in_scope", classify)
    w.drive([("vendor-a", "alpha", hits)])
    assert seen == [Scope.project("p-9")]
    assert w.enqueued == [] and w.published == [] and w.events == ["export"]
    [batch] = w.items[0]["timings"]["request_batches"]
    assert batch["status"] == "error" and "cannot publish into" in batch["message"]


def test_missing_nodes_are_requested_back_to_back_published_then_exported(monkeypatch):
    monkeypatch.setattr(greq, "NODES_PER_REQUEST", 2)
    hits = [_hit(i) for i in range(5)]

    def states_for(collection, hs):
        out = _states(hs)
        out[4] = greq.HitState(hit=hs[4], state="ready", node_id="fn-4")
        return out

    w = _World(monkeypatch, answers=[_ok("s0", "fresh"), _ok("s1")], states_for=states_for)
    w.drive([("vendor-a", "alpha", hits)])

    # Four missing nodes in jobs of two: both enqueued before either is waited for, each answer
    # published, and only then the export job.
    assert w.events == ["enqueue req-0", "enqueue req-1", "publish s0", "publish s1", "export"]
    assert [o["nodes"] for o in w.enqueued] == [["7-0", "7-1"], ["7-2", "7-3"]]
    assert w.enqueued[0]["node_labels"] == ["/STRU-0", "/STRU-1"] and w.enqueued[0]["project"] == "ALPHA"
    assert w.enqueued[0]["def_dbs"] == ["D1"]
    item = w.items[0]
    t = item["timings"]
    assert {"request_ms", "request_publish_ms", "request_batches", "request", "queued_ms", "run_ms"} <= set(t)
    assert [b["status"] for b in t["request_batches"]] == ["published", "published"]
    assert [b["login_mode"] for b in t["request_batches"]] == ["fresh", "adopted"]
    assert t["request_batches"][0]["login_ms"] == 5 and t["request"]["logins"] == {"fresh": 1, "adopted": 1}
    assert "requested geometry for 4 node(s) in 2 job(s)" in item["message"]
    assert w.finished == ("done", None)


def test_a_provider_answering_nothing_is_the_files_reason(monkeypatch):
    hits = [_hit(1), _hit(2)]
    nothing = {"asset_nothing": True, "message": "no element named /STRU-1 in the source"}
    w = _World(monkeypatch, answers=[nothing], states_for=lambda c, hs: _states(hs, "unpublished"))
    w.drive([("vendor-a", "alpha", hits)])
    assert w.published == [] and w.events[-1] == "export"
    item = w.items[0]
    assert item["timings"]["request_batches"][0]["status"] == "nothing"
    assert {e["error"] for e in item["file_errors"]} == {
        "the geometry provider has nothing for it: no element named /STRU-1 in the source"
    }
    assert item["status"] == "partial"


def test_unknown_and_ready_hits_are_not_requested(monkeypatch):
    hits = [_hit(1), _hit(2)]
    w = _World(monkeypatch, answers=[], states_for=lambda c, hs: _states(hs, "unknown"))
    w.drive([("vendor-a", "alpha", hits)])
    assert w.enqueued == [] and w.events == ["export"]
    assert w.items[0]["timings"]["request"]["unknown"] == 2


def test_without_the_option_nothing_is_requested(monkeypatch):
    w = _World(monkeypatch, answers=[], states_for=lambda c, hs: _states(hs))
    w.drive([("vendor-a", "alpha", [_hit(1)])], request=False)
    assert w.enqueued == [] and w.events == ["export"] and "request_ms" not in w.items[0]["timings"]


def test_skipping_during_the_request_cancels_every_request_job_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(greq, "NODES_PER_REQUEST", 1)
    w = _World(monkeypatch, answers=["hold", "hold"], states_for=lambda c, hs: _states(hs), skip_on_job="req-0")
    w.drive([("vendor-a", "alpha", [_hit(1), _hit(2)])])
    assert sorted(w.cancelled) == ["req-0", "req-1"]
    assert "export" not in w.events and w.items[0]["status"] == "skipped"
    assert w.items[0]["message"] == "skipped by Tester" and w.finished == ("done", None)


def test_cancelling_the_run_during_the_request_stops_it(monkeypatch):
    w = _World(monkeypatch, answers=["hold"], states_for=lambda c, hs: _states(hs))

    async def cancelled(pool, run_id):
        return RunBeat("cancelled" if w.enqueued else "running")

    monkeypatch.setattr(dbm, "heartbeat_asset_export_run", cancelled)
    w.drive([("vendor-a", "alpha", [_hit(1)])])
    assert w.cancelled == ["req-0"] and "export" not in w.events
    assert w.items[0]["status"] == "skipped" and w.finished is None


def test_collections_request_one_at_a_time(monkeypatch):
    w = _World(monkeypatch, answers=[_ok("a"), _ok("b")], states_for=lambda c, hs: _states(hs))
    w.drive([("vendor-a", "alpha", [_hit(1)]), ("vendor-a", "beta", [_hit(2, "beta")])])
    # Never two collections' requests interleaved: each enqueue is followed by its own publish.
    reqs = [e for e in w.events if not e.startswith("export")]
    assert reqs == ["enqueue req-0", "publish a", "enqueue req-1", "publish b"]
    assert [o["project"] for o in w.enqueued] == ["ALPHA", "BETA"]


# --- the preflight -----------------------------------------------------------------------------------


def test_preflight_counts_per_collection_and_says_who_may_request(monkeypatch):
    def states_for(collection, hs):
        if collection == "alpha":
            return [
                greq.HitState(hit=hs[0], state="ready"),
                greq.HitState(hit=hs[1], state="listed", node_id="7-1"),
                greq.HitState(hit=hs[2], state="unpublished"),
            ]
        return [greq.HitState(hit=h, state="unknown", message="no '/SITE-B'") for h in hs]

    w = _World(monkeypatch, answers=[], states_for=states_for)

    async def no_sweep(pool, *, provider):
        return {"id": "sw-1", "created_at": "now"}

    async def not_gated(plugin_id, pool, advertised):
        return bool(advertised.get("requires_admin"))

    monkeypatch.setattr(dbm, "running_asset_sweep", no_sweep)
    monkeypatch.setattr("ada.comms.rest.routes.plugin_jobs.plugin_job_requires_admin", not_gated)
    rows = [{k: v for k, v in _hit(i).items() if k != "file"} for i in range(3)] + [
        {k: v for k, v in _hit(9, "beta").items() if k != "file"}
    ]

    def run(user):
        return asyncio.run(
            ex.export_preflight(w.ctx, None, scope_obj=Scope.shared(), body={"query": {}, "hits": rows}, user=user)
        )

    doc = run(SimpleNamespace(is_admin=False))
    alpha, beta = doc["collections"]
    assert (alpha["ready"], alpha["missing"], alpha["listed"], alpha["unpublished"], alpha["jobs"]) == (1, 2, 1, 1, 1)
    assert (beta["unknown"], beta["missing"], beta["unknown_reason"]) == (1, 0, "no '/SITE-B'")
    assert beta["unknown_examples"] == ["/STRU-9"]
    assert (doc["hits"], doc["ready"], doc["missing"], doc["unknown"]) == (4, 1, 2, 1)
    assert doc["request"]["available"] and not doc["request"]["allowed"]
    assert "administrators" in doc["request"]["reason"]
    assert doc["sweep_running"]["id"] == "sw-1"
    assert run(SimpleNamespace(is_admin=True))["request"]["allowed"] is True
    assert w.enqueued == [] and w.store.blobs == {}  # a preflight writes nothing


def test_an_export_asking_for_geometry_it_may_not_request_is_refused(monkeypatch):
    w = _World(monkeypatch, answers=[], states_for=lambda c, hs: _states(hs))

    async def gated(plugin_id, pool, advertised):
        return True

    monkeypatch.setattr("ada.comms.rest.routes.plugin_jobs.plugin_job_requires_admin", gated)

    async def writable(pool, raw, user):
        return Scope.shared()

    monkeypatch.setattr(ex, "_writable_scope", writable)
    with pytest.raises(ex.ExportRefused, match="administrators"):
        asyncio.run(
            ex.start_export(
                w.ctx,
                None,
                scope="shared",
                scope_obj=Scope.shared(),
                body={
                    "query": {},
                    "hits": [],
                    "target_scope": "shared",
                    "target_folder": "out",
                    "request_missing_geometry": True,
                },
                user=SimpleNamespace(is_admin=False),
            )
        )
