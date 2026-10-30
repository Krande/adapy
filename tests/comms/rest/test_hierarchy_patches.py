"""Hierarchy patches over published spines, and the build exclusions they (and newer own publishes)
imply -- docs/design/apply-changes.md section 4.

A synthetic collection in a local-storage scope:

* the collection index at R1: proj > site-a > area-1 > eq-1, eq-2, eq-3 > part-3a; proj > site-b >
  area-2 > eq-4;
* site-a's own spine at R1, claiming a BUILD of the whole site; site-b's own spine at R1;
* eq-1 published as its own mesh subject at R0 (OLDER than the site's build), eq-2 at R3 (NEWER).

Patches are stored the way the publish job stores them (``store_hierarchy_patch``) into the same
sandbox, at a fixed clock between R1 and R4. Provider ids are neutral on purpose.
"""

import asyncio
import datetime as dt
import json
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-assets-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.core.assets.fixture_provider.builder import register_fixture_builder  # noqa: E402

from ada.assets.build import (  # noqa: E402
    BuildProvenance,
    BuildSummary,
    build_fingerprint,
    parse_build_summary,
)
from ada.assets.builders import (  # noqa: E402
    builder_accepts_exclude_nodes,
    clear_asset_builders,
    register_asset_builder,
)
from ada.assets.index import fold_listing  # noqa: E402
from ada.assets.keys import asset_key  # noqa: E402
from ada.assets.manifest import ArtefactEntry, AssetManifest, BuildSpec  # noqa: E402
from ada.assets.projection import build_hierarchy  # noqa: E402
from ada.comms.rest import hierarchy_patches as hp  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.formats import asset_build  # noqa: E402

from .test_asset_routes import _settings  # noqa: E402

COLLECTION = "plant"
TREE, MESH, SITE = "tree-maker", "mesh-only", "site-builder"
CAPABILITY = "asset-build-fixture"  # the fixture builder's, so a non-cached POST can enqueue
R0, R1, R3, R4 = "20260801T100000Z", "20260901T100000Z", "20260903T100000Z", "20260910T100000Z"
PATCH_CLOCK = dt.datetime(2026, 9, 5, 12, 0, 0, tzinfo=dt.timezone.utc)
COLS = ["id", "parent", "label", "kind", "leaf", "delivery"]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    from ada.comms.rest.routes import assets as assets_routes

    assets_routes.clear_asset_spine_cache()
    clear_asset_builders()
    register_fixture_builder()
    monkeypatch.setattr(hp, "_utcnow", lambda: PATCH_CLOCK)
    yield
    assets_routes.clear_asset_spine_cache()
    clear_asset_builders()


class _Entry(SimpleNamespace):
    pass


class _Scope:
    """The local-storage sandbox the app serves as ``user:me``, writable from the test, plus the
    async storage surface ``store_hierarchy_patch`` is handed (the same bytes the app reads)."""

    def __init__(self, root) -> None:
        self.root = root / "users" / "local-dev"

    def put(self, key: str, data: bytes) -> None:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    # -- async storage surface
    async def list_prefix(self, scope, prefix: str):
        base = self.root
        out = []
        for path in base.rglob("*"):
            if path.is_file():
                key = path.relative_to(base).as_posix()
                if key.startswith(prefix):
                    stat = path.stat()
                    out.append(_Entry(key=key, size=stat.st_size, last_modified=str(stat.st_mtime_ns)))
        return sorted(out, key=lambda e: e.key)

    async def get_bytes(self, scope, key: str) -> bytes:
        path = self.root / key
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    async def put_bytes(self, scope, key: str, data: bytes, **_kw) -> None:
        self.put(key, data)

    async def delete(self, scope, key: str) -> None:
        (self.root / key).unlink()

    def publish(self, subject, revision, provider, delivery="none", nodes=None, root=None, build=None):
        artefacts = []
        if nodes is not None:
            slice_ = build_hierarchy(
                provider=provider,
                collection=COLLECTION,
                produced_at="2026-09-01T10:00:00Z",
                nodes=[{"leaf": False, "delivery": "", **n} for n in nodes],
                root=root,
                depth=99,
            )
            data = slice_.to_json()
            self.put(asset_key(COLLECTION, subject, revision, "hierarchy.json"), data)
            artefacts.append(ArtefactEntry(role="hierarchy", file="hierarchy.json", sha256="0" * 64, size=len(data)))
        if delivery == "mesh":
            self.put(asset_key(COLLECTION, subject, revision, "model.glb"), b"glTF")
            artefacts.append(ArtefactEntry(role="mesh", file="model.glb", sha256="0" * 64, size=4))
        manifest = AssetManifest(
            provider=provider,
            collection=COLLECTION,
            subject=subject,
            revision=revision,
            node=None if subject == COLLECTION else subject,
            produced_at="2026-09-01T10:00:00Z",
            published_at="2026-09-01T10:00:00Z",
            delivery=delivery,
            build=build,
            artefacts=tuple(artefacts),
        )
        self.put(asset_key(COLLECTION, subject, revision, "asset.json"), manifest.to_json())

    def store_patch(self, upsert=(), delete=(), cols=COLS, collection=COLLECTION) -> dict:
        doc = {
            "schema": hp.HIERARCHY_PATCH_SCHEMA,
            "collection": collection,
            "cols": list(cols),
            "upsert": [list(r) for r in upsert],
            "delete": list(delete),
        }
        return asyncio.run(hp.store_hierarchy_patch(self, None, provider=TREE, collection=COLLECTION, doc=doc))


def _n(node, parent):
    return {"id": node, "parent": parent, "label": node, "kind": "x"}


def _row(node, parent, kind="x"):
    return [node, parent, node, kind, 0, ""]


SITE_A = [
    _n("site-a", None),
    _n("area-1", "site-a"),
    _n("eq-1", "area-1"),
    _n("eq-2", "area-1"),
    _n("eq-3", "area-1"),
    _n("part-3a", "eq-3"),
]
SITE_B = [_n("site-b", None), _n("area-2", "site-b"), _n("eq-4", "area-2")]


def _seed(scope: _Scope) -> None:
    scope.publish(
        COLLECTION,
        R1,
        TREE,
        nodes=[
            _n("proj", None),
            {**SITE_A[0], "parent": "proj"},
            *SITE_A[1:],
            {**SITE_B[0], "parent": "proj"},
            *SITE_B[1:],
        ],
    )
    scope.publish(
        "site-a",
        R1,
        SITE,
        delivery="build",
        root="site-a",
        nodes=SITE_A,
        build=BuildSpec(capability=CAPABILITY, options={"ref": "site-a"}, fingerprint_inputs=("ref",)),
    )
    scope.publish("site-b", R1, TREE, root="site-b", nodes=SITE_B)
    scope.publish("eq-1", R0, MESH, delivery="mesh")
    scope.publish("eq-2", R3, MESH, delivery="mesh")


@pytest.fixture
def scope_and_client(tmp_path):
    scope = _Scope(tmp_path)
    _seed(scope)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield scope, client


def _url(path: str) -> str:
    return f"/api/scopes/user:me/assets/{path}"


def _level(client, parent, root=COLLECTION, revision=None) -> dict:
    params = {"parent": parent}
    if root != COLLECTION:
        params["root"] = root
    if revision:
        params["revision"] = revision
    r = client.get(_url(f"tree/published/{COLLECTION}"), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ids(body) -> list[str]:
    at = body["cols"].index("id")
    return [row[at] for row in body["rows"]]


def _counts(body) -> dict:
    id_at, n_at = body["cols"].index("id"), body["cols"].index("children")
    return {row[id_at]: row[n_at] for row in body["rows"]}


# -- storing -------------------------------------------------------------------------------------


def test_a_patch_is_validated_and_stored_in_order_under_the_reserved_segment(tmp_path):
    scope = _Scope(tmp_path)
    _seed(scope)
    first = scope.store_patch(upsert=[_row("eq-5", "area-1")], delete=["eq-3"])
    second = scope.store_patch(delete=["eq-4"])

    assert first["upserted"] == 1 and first["deleted"] == 1
    assert first["key"].startswith(f"assets/{COLLECTION}/_patches/hierarchy/") and first["key"].endswith(".json")
    assert second["key"] > first["key"]  # lexical order is store order
    stored = json.loads((scope.root / first["key"]).read_bytes())
    assert stored["schema"] == hp.HIERARCHY_PATCH_SCHEMA
    assert stored["at"] == "20260905T120000Z" and stored["provider"] == TREE
    # The spines it was written over: the collection index (no touched subject has its own spine).
    assert stored["applies_over"] == {COLLECTION: R1}


def test_a_patch_touching_a_subject_with_its_own_spine_records_that_revision(tmp_path):
    scope = _Scope(tmp_path)
    _seed(scope)
    out = scope.store_patch(upsert=[_row("area-9", "site-b")])
    stored = json.loads((scope.root / out["key"]).read_bytes())
    assert stored["applies_over"] == {COLLECTION: R1, "site-b": R1}


@pytest.mark.parametrize(
    "doc_changes, match",
    [
        ({"schema": "ada.assets/hierarchy-patch@2"}, "schema"),
        ({"collection": "other"}, "collection"),
        ({"cols": ["id", "label"]}, "parent"),
        ({"upsert": [["eq-5", "area-1"]]}, "positional"),
        ({"upsert": [[None, "area-1", "x", "x", 0, ""]]}, "'id'"),
        ({"delete": [3]}, "delete entry"),
    ],
)
def test_a_patch_core_cannot_read_is_refused_and_nothing_is_written(tmp_path, doc_changes, match):
    scope = _Scope(tmp_path)
    _seed(scope)
    doc = {"schema": hp.HIERARCHY_PATCH_SCHEMA, "collection": COLLECTION, "cols": COLS, "upsert": [], "delete": []}
    doc.update(doc_changes)
    with pytest.raises(hp.HierarchyPatchError, match=match):
        asyncio.run(hp.store_hierarchy_patch(scope, None, provider=TREE, collection=COLLECTION, doc=doc))
    assert not (scope.root / f"assets/{COLLECTION}/_patches").exists()


def test_a_collection_with_no_published_spine_refuses_the_patch_with_a_warning(tmp_path):
    scope = _Scope(tmp_path)
    scope.publish("eq-1", R0, MESH, delivery="mesh")  # geometry only: no tree to patch
    out = scope.store_patch(delete=["eq-3"])
    assert out["refused"] is True and out["key"] is None and "full export" in out["warning"]
    assert not (scope.root / f"assets/{COLLECTION}/_patches").exists()


def test_the_collection_named_in_the_patch_matches_case_blind(tmp_path):
    scope = _Scope(tmp_path)
    _seed(scope)
    assert scope.store_patch(delete=["eq-3"], collection=COLLECTION.upper())["deleted"] == 1


def test_an_empty_patch_stores_nothing(tmp_path):
    scope = _Scope(tmp_path)
    _seed(scope)
    assert scope.store_patch() == {"key": None, "upserted": 0, "deleted": 0}
    assert not (scope.root / f"assets/{COLLECTION}/_patches").exists()


def test_patches_are_never_a_subject_a_malformed_key_or_a_published_file(scope_and_client):
    scope, client = scope_and_client
    key = scope.store_patch(delete=["eq-3"])["key"]

    folded = fold_listing([key, asset_key(COLLECTION, "eq-1", R0, "asset.json")])
    assert [s.subject for s in folded.subjects(COLLECTION)] == ["eq-1"] and not folded.malformed

    index = client.get(_url("index"), params={"collection": COLLECTION}).json()
    assert "_patches" not in {s["subject"] for s in index["collections"][COLLECTION]}
    assert index["malformed"] == []
    assert client.get(_url("collections")).json()["collections"] == [COLLECTION]

    files = {f["key"]: f for f in client.get(_url("files")).json()["files"]}
    assert files[key]["area"] == "patch" and files[key]["kind"] == "hierarchy-patch"
    assert all(s["collection"] == COLLECTION for s in client.get(_url("sources")).json()["sources"])

    r = client.delete(_url("files"), params={"key": key})
    assert r.status_code == 409 and (scope.root / key).exists()
    r = client.delete(_url(f"{COLLECTION}/_patches/hierarchy"))
    assert r.status_code == 400 and (scope.root / key).exists()


# -- the tree overlay ----------------------------------------------------------------------------


def test_upsert_inserts_and_moves_delete_drops_the_subtree_and_counts_follow(scope_and_client):
    scope, client = scope_and_client
    assert _ids(_level(client, "area-1")) == ["eq-1", "eq-2", "eq-3"]  # before: the bare spine

    scope.store_patch(upsert=[_row("eq-5", "area-1"), _row("eq-1", "area-2")], delete=["eq-3"])
    hp.clear_hierarchy_patch_caches()  # the listing is held a few seconds; a test does not wait

    assert _ids(_level(client, "area-1")) == ["eq-2", "eq-5"]  # eq-1 moved away, eq-3 deleted
    assert _ids(_level(client, "area-2")) == ["eq-4", "eq-1"]  # eq-1 hangs under its new parent
    assert _ids(_level(client, "eq-3")) == []  # the deleted subtree answers nothing
    assert _counts(_level(client, "site-a")) == {"area-1": 2}
    assert _counts(_level(client, "site-b")) == {"area-2": 2}

    whole = client.get(_url(f"tree/published/{COLLECTION}")).json()
    parents = {r[0]: r[1] for r in whole["rows"]}
    assert "eq-3" not in parents and "part-3a" not in parents
    assert parents["eq-5"] == "area-1" and parents["eq-1"] == "area-2"


def test_a_row_moved_into_another_spine_appears_when_that_spine_is_served(scope_and_client):
    scope, client = scope_and_client
    scope.store_patch(upsert=[_row("eq-1", "area-2")])
    hp.clear_hierarchy_patch_caches()

    assert _ids(_level(client, "area-1", root="site-a")) == ["eq-2", "eq-3"]
    assert _ids(_level(client, "area-2", root="site-b")) == ["eq-4", "eq-1"]
    site_b = client.get(_url(f"tree/published/{COLLECTION}"), params={"root": "site-b"}).json()
    assert ["eq-1", "area-2"] == site_b["rows"][-1][:2]


def test_patches_apply_in_order_and_a_reinserted_node_does_not_bring_back_its_old_subtree(scope_and_client):
    scope, client = scope_and_client
    scope.store_patch(upsert=[_row("eq-5", "area-1")], delete=["eq-3"])
    scope.store_patch(delete=["eq-5"])
    scope.store_patch(upsert=[_row("eq-3", "area-1")])
    hp.clear_hierarchy_patch_caches()

    assert _ids(_level(client, "area-1")) == ["eq-1", "eq-2", "eq-3"]  # eq-5 gone, eq-3 back in place
    assert _ids(_level(client, "eq-3")) == []  # part-3a went with the delete; the re-insert is eq-3 alone
    assert _counts(_level(client, "area-1"))["eq-3"] == 0


def test_a_newer_full_publish_supersedes_older_patches_and_the_prune_drops_them(scope_and_client):
    scope, client = scope_and_client
    key = scope.store_patch(delete=["eq-3"])["key"]
    hp.clear_hierarchy_patch_caches()
    assert "eq-3" not in _ids(_level(client, "area-1"))

    # A full export after the patch: it re-states the tree (eq-3 really is gone, eq-6 is new).
    nodes = [_n("proj", None), {**SITE_A[0], "parent": "proj"}, *SITE_A[1:4], _n("eq-6", "area-1")]
    assert asyncio.run(hp.prune_superseded_hierarchy_patches(scope, None, COLLECTION)) == []
    scope.publish(COLLECTION, R4, TREE, nodes=nodes)
    assert _ids(_level(client, "area-1")) == ["eq-1", "eq-2", "eq-6"]
    # The OLDER index revision, asked for explicitly, is still read through the patch.
    assert _ids(_level(client, "area-1", revision=R1)) == ["eq-1", "eq-2"]
    # site-a's own spine (R1) is older than the patch: still overlaid.
    assert _ids(_level(client, "area-1", root="site-a")) == ["eq-1", "eq-2"]

    assert asyncio.run(hp.prune_superseded_hierarchy_patches(scope, None, COLLECTION)) == [key]
    assert not (scope.root / key).exists()


def test_the_overlaid_level_is_cached_per_newest_patch(scope_and_client, monkeypatch):
    scope, client = scope_and_client
    scope.store_patch(delete=["eq-3"])
    hp.clear_hierarchy_patch_caches()
    built = []
    real = hp.OverlaidSpine.__init__

    def counting(self, *a, **kw):
        built.append(1)
        real(self, *a, **kw)

    monkeypatch.setattr(hp.OverlaidSpine, "__init__", counting)
    _level(client, "area-1")
    _level(client, "site-a")
    assert len(built) == 1
    scope.store_patch(delete=["eq-2"])
    hp.clear_hierarchy_patch_caches()
    assert _ids(_level(client, "area-1")) == ["eq-1"]
    assert len(built) == 2


# -- geometry roll-up ----------------------------------------------------------------------------


def test_the_rollup_reads_the_overlay_and_a_new_patch_moves_its_token(scope_and_client):
    scope, client = scope_and_client
    before = client.get(_url(f"geometry/{COLLECTION}")).json()
    assert {"eq-1", "eq-2", "site-a"} <= set(before["any"]["here"])
    assert "area-2" not in before["any"]["below"]

    scope.store_patch(upsert=[_row("eq-1", "area-2")], delete=["eq-2"])
    after = client.get(_url(f"geometry/{COLLECTION}")).json()
    assert after["cached"] is False and after["index_token"] != before["index_token"]
    assert "eq-2" not in after["any"]["here"]  # deleted: counts nowhere, not even unplaced
    assert "eq-2" not in after["any"]["unplaced"]
    assert {"area-2", "site-b", "proj"} <= set(after["any"]["below"])
    assert after["providers"][MESH]["here"] == ["eq-1"]


# -- builds --------------------------------------------------------------------------------------


@pytest.fixture
def captured_jobs(monkeypatch):
    from ada.comms.rest.routes import assets as assets_routes

    seen = []
    real = assets_routes.JobRequest

    def capture(**kw):
        seen.append(kw)
        return real(**kw)

    monkeypatch.setattr(assets_routes, "JobRequest", capture)
    return seen


def _build(client, node="site-a", subject=None) -> dict:
    body = {"provider": "published", "collection": COLLECTION, "node": node}
    if subject:
        body["subject"] = subject
    r = client.post(_url("build"), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_descendant_with_its_own_newer_publish_is_excluded_and_named_as_an_override(scope_and_client, captured_jobs):
    _, client = scope_and_client
    body = _build(client)
    # eq-2's own mesh (R3) is newer than the site's build (R1); eq-1's (R0) is older.
    assert body["exclude_nodes"] == ["eq-2"]
    assert body["overrides"] == [{"node": "eq-2", "subject": "eq-2", "revision": R3, "provider": MESH}]
    assert body["fingerprint"] == build_fingerprint(
        options={"ref": "site-a"},
        fingerprint_inputs=("ref",),
        node="site-a",
        hierarchy_source=R1,
        exclude_nodes=["eq-2"],
    )
    assert captured_jobs[-1]["conversion_options"]["exclude_nodes"] == ["eq-2"]


def test_descendants_a_patch_deleted_are_excluded_as_subtrees_but_are_not_overrides(scope_and_client, captured_jobs):
    scope, client = scope_and_client
    scope.store_patch(delete=["eq-3"], upsert=[_row("eq-1", "area-2")])
    hp.clear_hierarchy_patch_caches()
    body = _build(client)
    # eq-3 deleted (part-3a goes with it, so it is not named), eq-1 moved out from under the site.
    assert body["exclude_nodes"] == ["eq-1", "eq-2", "eq-3"]
    assert [o["node"] for o in body["overrides"]] == ["eq-2"]
    assert captured_jobs[-1]["conversion_options"]["exclude_nodes"] == ["eq-1", "eq-2", "eq-3"]


def test_nothing_to_exclude_keeps_the_fingerprint_it_always_had(scope_and_client, captured_jobs):
    _, client = scope_and_client
    assert build_fingerprint(options={"a": 1}, fingerprint_inputs=("a",), node="n", hierarchy_source=R1) == (
        build_fingerprint(options={"a": 1}, fingerprint_inputs=("a",), node="n", hierarchy_source=R1, exclude_nodes=())
    )
    leaf = _build(client, node="eq-3", subject="site-a")  # a covered node with one child, nothing newer
    assert leaf["exclude_nodes"] == [] and leaf["overrides"] == []
    assert leaf["fingerprint"] == build_fingerprint(
        options={"ref": "site-a"}, fingerprint_inputs=("ref",), node="eq-3", hierarchy_source=R1
    )
    assert "exclude_nodes" not in captured_jobs[-1]["conversion_options"]


def test_an_element_with_its_own_newer_publish_delivers_its_own_manifest(scope_and_client):
    _, client = scope_and_client
    own = client.get(_url(f"delivery/published/{COLLECTION}/eq-2")).json()
    assert own["kind"] == "mesh" and own["revision"] == R3 and own["provider"] == MESH
    covered = client.get(_url(f"delivery/published/{COLLECTION}/eq-2"), params={"subject": "site-a"}).json()
    assert covered["kind"] == "build" and covered["revision"] == R1


# -- the worker: builders with and without the parameter -------------------------------------------

DERIVED_PREFIX = "_derived/assets/site-builder/plant/site-a/20260901T100000Z/site-a/0123456789abcdef"


def _summary(request, derived_prefix) -> dict:
    return BuildSummary(
        ok=True,
        glb_key=f"{derived_prefix}/model.glb",
        provenance=BuildProvenance(
            provider=request.provider,
            collection=request.collection,
            subject=request.subject,
            revision=request.revision,
            node=request.node,
            fingerprint=request.fingerprint,
            built_at="2026-09-05T12:00:00Z",
        ),
    ).to_dict()


class _OldBuilder:
    """Written before ``exclude_nodes`` existed: named keywords only."""

    calls: list = []

    def build(self, options, *, request, storage, scope, derived_prefix, on_progress=None, cancel_event=None):
        _OldBuilder.calls.append(options)
        return _summary(request, derived_prefix)


class _NewBuilder:
    calls: list = []

    def build(self, options, *, request, storage, scope, derived_prefix, exclude_nodes=(), **_kw):
        _NewBuilder.calls.append(list(exclude_nodes))
        return _summary(request, derived_prefix)


class _Queue:
    def __init__(self):
        self.updates = []

    async def update(self, job_id, **fields):
        self.updates.append(fields)


class _Storage:
    def __init__(self):
        self.blobs = {}

    async def put_bytes(self, scope, key, data, **kwargs):
        self.blobs[key] = data


def _run_job(builder, exclude, monkeypatch) -> dict:
    clear_asset_builders()
    register_asset_builder("asset-build-probe", builder)

    async def _audit_done(*a, **kw):
        return None

    monkeypatch.setattr(asset_build, "_audit_done", _audit_done)
    opts = {
        "capability": "asset-build-probe",
        "provider": SITE,
        "collection": COLLECTION,
        "subject": "site-a",
        "revision": R1,
        "node": "site-a",
        "fingerprint": "0123456789abcdef",
        "hierarchy_source": R1,
        "options": {"ref": "site-a"},
        "derived_prefix": DERIVED_PREFIX,
    }
    if exclude is not None:
        opts["exclude_nodes"] = exclude
    job = SimpleNamespace(job_id="job-1", derived_key=f"{DERIVED_PREFIX}/summary.json", conversion_options=opts)
    queue, storage = _Queue(), _Storage()
    asyncio.run(
        asset_build._run_asset_build(job=job, scope=None, storage=storage, queue=queue, db_pool=None, started_at=0.0)
    )
    assert queue.updates[-1]["status"] == "done", queue.updates
    return parse_build_summary(json.loads(storage.blobs[job.derived_key])).to_dict()


def test_a_builder_without_the_parameter_is_still_called_and_the_summary_says_so(monkeypatch):
    _OldBuilder.calls = []
    assert not builder_accepts_exclude_nodes(_OldBuilder())
    summary = _run_job(_OldBuilder, ["eq-3", "eq-2"], monkeypatch)
    assert _OldBuilder.calls == [{"ref": "site-a"}]
    assert any("exclude_nodes" in w for w in summary.get("warnings", []))


def test_a_builder_with_the_parameter_gets_the_sorted_list_and_only_when_non_empty(monkeypatch):
    _NewBuilder.calls = []
    assert builder_accepts_exclude_nodes(_NewBuilder())
    summary = _run_job(_NewBuilder, ["eq-3", "eq-2"], monkeypatch)
    assert _NewBuilder.calls == [["eq-2", "eq-3"]] and "warnings" not in summary
    _run_job(_NewBuilder, None, monkeypatch)
    assert _NewBuilder.calls[-1] == []  # not passed: the default


def test_an_old_builder_with_nothing_to_exclude_carries_no_warning(monkeypatch):
    _OldBuilder.calls = []
    summary = _run_job(_OldBuilder, [], monkeypatch)
    assert _OldBuilder.calls and "warnings" not in summary
