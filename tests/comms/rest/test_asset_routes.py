"""The asset browser routes, driven end to end by the private-format fixture provider.

The scope here is Phase 1's acceptance: the fixture's tree, index and delivery claims come back
through the routes, and core never learns what the fixture's source format is (the layering gate
in tests/core/assets/test_layering_gate.py holds the other half of that claim).

Local storage sandbox, auth disabled -- these exercise the asset routes, not the auth stack.
"""

import os
import tempfile

# ada.comms.rest.app builds a default `app = create_app()` at module scope, so point storage at a
# throwaway dir BEFORE importing it -- same reason (and same lines) as the other REST tests: the
# import must succeed in an environment without ./viewer-data.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-assets-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.core.assets.fixture_provider import FIXTURE_PROVIDER_ID  # noqa: E402
from tests.core.assets.fixture_provider.provider import (  # noqa: E402
    BUILD_CAPABILITY,
    FakeStore,
    publish_fixture,
)
from tests.core.assets.fixture_provider.second import (  # noqa: E402
    SECOND_BUILD_CAPABILITY,
    SECOND_PROVIDER_ID,
    publish_second_claim,
)

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

COLLECTION = "fixture-a"


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


@pytest.fixture
def client_and_revision(tmp_path):
    """Publish the fixture into a local-storage scope, then serve it over the routes."""
    store = FakeStore()
    revision = publish_fixture(store)

    scope_root = tmp_path / "users" / "local-dev"
    for key, data in store.blobs.items():
        dest = scope_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client, revision


def _scope_url(path: str) -> str:
    return f"/api/scopes/user:me/assets/{path}"


@pytest.fixture(autouse=True)
def _fresh_route_caches():
    """The route caches are keyed by (scope prefix, key) -- immutable in production, but every test
    here publishes into a NEW sandbox at the SAME deterministic keys."""
    from ada.comms.rest.routes import assets as assets_routes

    assets_routes.clear_asset_spine_cache()
    assets_routes.clear_asset_attributes_cache()
    yield
    assets_routes.clear_asset_spine_cache()
    assets_routes.clear_asset_attributes_cache()


@pytest.fixture
def two_providers(tmp_path):
    """``pump-b`` carries provider A's build claim AND provider B's, newer, on the same subject.

    The key grammar has no provider segment, so both are just revisions of one subject: the
    provider axis is read off each manifest.
    """
    store = FakeStore()
    rev_a = publish_fixture(store)
    rev_b = publish_second_claim(store, collection=COLLECTION, subject="pump-b")
    assert rev_b > rev_a

    scope_root = tmp_path / "users" / "local-dev"
    for key, data in store.blobs.items():
        dest = scope_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client, rev_a, rev_b


def test_providers_always_offers_the_built_in_published_one(client_and_revision):
    """A scope may hold assets from a provider THIS process has never heard of."""
    client, _ = client_and_revision
    r = client.get(_scope_url("providers"))
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()["providers"]]
    assert "published" in ids


def test_a_broken_provider_is_listed_without_its_exception_text(client_and_revision, monkeypatch, caplog):
    """A provider whose factory raised still shows -- but its exception text, which can carry the
    hosts, paths or credentials of its configuration, stays in the server log (CodeQL
    py/stack-trace-exposure)."""
    from ada.comms.rest.routes import assets as assets_routes

    secret = "OperationalError: could not connect to postgres://svc:hunter2@10.0.0.5/catalogue"
    monkeypatch.setattr(
        assets_routes, "asset_providers", lambda: [{"id": "broken", "label": "Broken", "error": secret, "delivery": []}]
    )
    client, _ = client_and_revision
    # adapy's logger does not propagate to the root one caplog listens on: hand it caplog's handler.
    monkeypatch.setattr(assets_routes.logger, "handlers", [*assets_routes.logger.handlers, caplog.handler])
    with caplog.at_level("WARNING", logger=assets_routes.logger.name):
        r = client.get(_scope_url("providers"))
    assert r.status_code == 200, r.text
    (entry,) = [p for p in r.json()["providers"] if p["id"] == "broken"]
    assert entry["error"] == assets_routes.PROVIDER_LOAD_FAILED
    assert "hunter2" not in r.text
    assert "hunter2" in caplog.text


def test_index_folds_without_listing_the_whole_scope(client_and_revision):
    client, revision = client_and_revision
    r = client.get(_scope_url("index"), params={"collection": COLLECTION})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["malformed"] == []
    subjects = {s["subject"] for s in body["collections"][COLLECTION]}
    assert {"pump-a", "pump-b", "tank-c", "unit-1", "unit-2", "site", COLLECTION} == subjects
    pump = next(s for s in body["collections"][COLLECTION] if s["subject"] == "pump-a")
    assert pump["revisions"][0]["revision"] == revision
    # Opt-in: without it the index is the plain fold, byte for byte as before.
    assert "manifest" not in pump["revisions"][0]


def test_index_folds_manifest_summaries_per_revision(client_and_revision):
    """The tab badges from delivery + hierarchy_revision without one fetch per subject."""
    client, revision = client_and_revision
    r = client.get(_scope_url("index"), params={"collection": COLLECTION, "manifests": "true"})
    assert r.status_code == 200, r.text
    by_subject = {s["subject"]: s["revisions"][0] for s in r.json()["collections"][COLLECTION]}
    assert by_subject["pump-a"]["manifest"] == {
        "provider": "fixture-lines",
        "node": "pump-a",
        "delivery": "mesh",
        "produced_at": "2026-09-21T14:30:01Z",
    }
    assert by_subject["pump-b"]["manifest"]["delivery"] == "build"
    assert by_subject["unit-1"]["manifest"]["delivery"] == "none"
    # Opaque provider data stays out of the index.
    assert "build" not in by_subject["pump-b"]["manifest"]


def test_index_reports_an_unreadable_manifest_on_its_revision(client_and_revision, tmp_path):
    client, revision = client_and_revision
    bad = tmp_path / "users" / "local-dev" / "assets" / COLLECTION / "tank-c" / revision / "asset.json"
    bad.write_bytes(b'{"schema": "someone-else/manifest@9"}')
    r = client.get(_scope_url("index"), params={"collection": COLLECTION, "manifests": "true"})
    tank = next(s for s in r.json()["collections"][COLLECTION] if s["subject"] == "tank-c")
    assert "manifest" not in tank["revisions"][0]
    assert "unknown manifest schema" in tank["revisions"][0]["manifest_error"]


def test_index_manifests_needs_a_collection(client_and_revision):
    client, _ = client_and_revision
    r = client.get(_scope_url("index"), params={"manifests": "true"})
    assert r.status_code == 400


def test_tree_returns_the_slice_in_cores_vocabulary(client_and_revision):
    client, _ = client_and_revision
    r = client.get(_scope_url(f"tree/published/{COLLECTION}"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "ada.assets/hierarchy@1"
    cols = body["cols"]
    rows = {row[cols.index("id")]: row for row in body["rows"]}
    assert rows["pump-a"][cols.index("label")] == "Pump A"
    assert rows["pump-a"][cols.index("kind")] == "equipment"
    assert rows["site"][cols.index("parent")] is None


def test_mesh_delivery_claim(client_and_revision):
    client, revision = client_and_revision
    r = client.get(_scope_url(f"delivery/published/{COLLECTION}/pump-a"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "mesh"
    assert body["revision"] == revision
    assert body["url"].endswith("model.glb")


def test_build_delivery_claim_carries_opaque_options(client_and_revision):
    """Core forwards the provider's options verbatim without interpreting them."""
    client, _ = client_and_revision
    r = client.get(_scope_url(f"delivery/published/{COLLECTION}/pump-b"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "build"
    assert body["capability"] == "asset-build-fixture"
    assert body["options"]["ref"] == "pump-b"


def test_delivery_published_takes_the_newest_revision_of_any_provider(two_providers):
    client, _, rev_b = two_providers
    body = client.get(_scope_url(f"delivery/published/{COLLECTION}/pump-b")).json()
    assert (body["provider"], body["revision"]) == (SECOND_PROVIDER_ID, rev_b)
    assert body["capability"] == SECOND_BUILD_CAPABILITY


def test_delivery_named_provider_selects_its_own_publish(two_providers):
    client, rev_a, rev_b = two_providers
    a = client.get(_scope_url(f"delivery/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-b"))
    b = client.get(_scope_url(f"delivery/{SECOND_PROVIDER_ID}/{COLLECTION}/pump-b"))
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert (a.json()["provider"], a.json()["revision"]) == (FIXTURE_PROVIDER_ID, rev_a)
    assert a.json()["capability"] == BUILD_CAPABILITY
    assert (b.json()["provider"], b.json()["revision"]) == (SECOND_PROVIDER_ID, rev_b)


def test_delivery_explicit_revision_by_another_provider_is_409(two_providers):
    client, rev_a, _ = two_providers
    r = client.get(_scope_url(f"delivery/{SECOND_PROVIDER_ID}/{COLLECTION}/pump-b"), params={"revision": rev_a})
    assert r.status_code == 409, r.text
    assert FIXTURE_PROVIDER_ID in r.json()["detail"]
    # ... while the provider that did write it gets it.
    r = client.get(_scope_url(f"delivery/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-b"), params={"revision": rev_a})
    assert r.status_code == 200, r.text


def test_delivery_provider_that_never_published_the_subject_is_404_naming_it(two_providers):
    client, _, _ = two_providers
    r = client.get(_scope_url(f"delivery/nobody-here/{COLLECTION}/pump-b"))
    assert r.status_code == 404, r.text
    assert "nobody-here" in r.json()["detail"]
    # pump-a was only ever published by provider A.
    r = client.get(_scope_url(f"delivery/{SECOND_PROVIDER_ID}/{COLLECTION}/pump-a"))
    assert r.status_code == 404, r.text


def test_delivery_for_a_covered_node_reads_the_covering_subject(client_and_revision):
    """A node covered by a publish rooted above it has no manifest of its own."""
    client, revision = client_and_revision
    assert client.get(_scope_url(f"delivery/published/{COLLECTION}/covered-node")).status_code == 404
    r = client.get(_scope_url(f"delivery/published/{COLLECTION}/covered-node"), params={"subject": "pump-a"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "mesh"
    assert body["url"] == f"assets/{COLLECTION}/pump-a/{revision}/model.glb"
    assert (body["provider"], body["revision"]) == (FIXTURE_PROVIDER_ID, revision)


def test_attributes_select_by_provider_too(two_providers):
    """Provider A's attributes stay reachable under A even though B's revision is newer."""
    client, rev_a, _ = two_providers
    r = client.get(_scope_url(f"attributes/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-b"))
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == rev_a
    r = client.get(_scope_url(f"attributes/{SECOND_PROVIDER_ID}/{COLLECTION}/pump-a"))
    assert r.status_code == 404


def test_node_without_a_claim_is_404(client_and_revision):
    client, _ = client_and_revision
    assert client.get(_scope_url(f"delivery/published/{COLLECTION}/unit-1")).status_code == 404


def test_unknown_collection_is_404_not_an_empty_tree(client_and_revision):
    """An empty tree and a missing collection must not look the same to the browser."""
    client, _ = client_and_revision
    assert client.get(_scope_url("tree/published/nope")).status_code == 404


def test_unreadable_stored_blob_is_502_not_404(client_and_revision, tmp_path):
    """The object IS there; calling it missing sends the caller after the wrong problem."""
    client, revision = client_and_revision
    bad = tmp_path / "users" / "local-dev" / "assets" / COLLECTION / COLLECTION / revision / "hierarchy.json"
    bad.write_bytes(b'{"schema": "ada.assets/hierarchy@99"}')
    r = client.get(_scope_url(f"tree/published/{COLLECTION}"))
    assert r.status_code == 502
    assert "unknown hierarchy schema" in r.json()["detail"]


# --- one level at a time -----------------------------------------------------------------------------
#
# `parent=` answers ONE level of a stored spine: expanding a row must cost that row's children, not
# the whole site's subtree.


def _level(client, revision, parent: str, root: str = COLLECTION) -> dict:
    r = client.get(
        _scope_url(f"tree/published/{COLLECTION}"), params={"root": root, "revision": revision, "parent": parent}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _by_id(body: dict) -> dict:
    cols = body["cols"]
    return {row[cols.index("id")]: dict(zip(cols, row)) for row in body["rows"]}


def test_parent_equal_to_the_subject_answers_its_first_level(client_and_revision):
    client, revision = client_and_revision
    body = _level(client, revision, COLLECTION)
    assert body["cols"][-1] == "children"
    assert body["parent"] == COLLECTION
    rows = _by_id(body)
    assert set(rows) == {"site"}
    assert rows["site"]["children"] == 2  # unit-1, unit-2
    assert rows["site"]["label"] == "Fixture Site"


def test_parent_answers_only_that_nodes_direct_children(client_and_revision):
    client, revision = client_and_revision
    rows = _by_id(_level(client, revision, "site"))
    assert set(rows) == {"unit-1", "unit-2"}
    assert rows["unit-1"]["children"] == 2
    assert rows["unit-2"]["children"] == 1
    assert all(r["parent"] == "site" for r in rows.values())

    pumps = _by_id(_level(client, revision, "unit-1"))
    assert set(pumps) == {"pump-a", "pump-b"}
    assert {r["children"] for r in pumps.values()} == {0}
    # Same columns as the stored spine, plus the count.
    whole = client.get(_scope_url(f"tree/published/{COLLECTION}")).json()
    assert _level(client, revision, "unit-1")["cols"] == whole["cols"] + ["children"]


def test_a_parent_the_spine_does_not_hold_is_an_empty_level_not_a_404(client_and_revision):
    """Asked per expanded row: "nothing under it here" is the answer for a leaf and a stranger
    alike. A 404 stays reserved for a spine that is not there."""
    client, revision = client_and_revision
    assert _level(client, revision, "no-such-node")["rows"] == []
    assert _level(client, revision, "pump-a")["rows"] == []
    r = client.get(
        _scope_url(f"tree/published/{COLLECTION}"), params={"root": "pump-a", "revision": revision, "parent": "x"}
    )
    assert r.status_code == 404


def test_without_parent_the_whole_spine_comes_back_unchanged(client_and_revision):
    client, _ = client_and_revision
    body = client.get(_scope_url(f"tree/published/{COLLECTION}")).json()
    assert "children" not in body["cols"]
    assert "parent" not in body
    assert len(body["rows"]) == 6


def test_a_spine_is_read_and_parsed_once_per_revision(client_and_revision, monkeypatch):
    """Immutable at its key (the revision is in it), so every later level comes from the cache."""
    from ada.comms.rest.routes import assets as assets_routes
    from ada.comms.rest.storage import Storage

    client, revision = client_and_revision
    reads: list[str] = []
    real_get = Storage.get_bytes

    async def counting_get(self, scope, key):
        if key.endswith("/hierarchy.json"):
            reads.append(key)
        return await real_get(self, scope, key)

    monkeypatch.setattr(Storage, "get_bytes", counting_get)
    parses = []
    real_parse = assets_routes.parse_hierarchy
    monkeypatch.setattr(assets_routes, "parse_hierarchy", lambda raw: (parses.append(1), real_parse(raw))[1])

    for parent in (COLLECTION, "site", "unit-1", "unit-2"):
        _level(client, revision, parent)
    assert client.get(_scope_url(f"tree/published/{COLLECTION}"), params={"revision": revision}).status_code == 200
    assert len(reads) == 1
    assert len(parses) == 1


def test_published_spines_are_stored_gzipped_and_read_back_identically(tmp_path):
    """Core compresses every hierarchy.json AT REST when it applies a plan; the manifest keeps
    describing the document, and every reader (the routes, the published provider) gets it back."""
    import gzip as _gzip
    import hashlib

    from tests.core.assets.fixture_provider.provider import FixtureLinesProvider
    from tests.core.assets.fixture_provider.publisher import (
        FixtureLinesPublisher,
        stage_fixture_source,
    )

    from ada.assets.keys import ASSET_PREFIX, asset_key
    from ada.assets.manifest import (
        HIERARCHY_FILENAME,
        MANIFEST_FILENAME,
        Actor,
        parse_manifest,
    )
    from ada.assets.projection import parse_hierarchy
    from ada.assets.publish import apply_publish_plan

    store = FakeStore()
    stage_fixture_source(store, staging_id="up1")
    staged = {"source.jsonl": f"{ASSET_PREFIX}/_staging/up1/source.jsonl"}
    plan = FixtureLinesPublisher().derive(None, staged, storage=store, collection=COLLECTION, options={}, dry_run=False)
    planned = {w.key: w.data for w in plan.writes}
    apply_publish_plan(
        plan,
        published_by=Actor(id="local-dev", display="Local Dev"),
        published_via="user",
        dry_run=False,
        replace_existing=False,
        occupied=set(),
        write=store.put,
    )
    key = asset_key(COLLECTION, COLLECTION, plan.revision, HIERARCHY_FILENAME)
    stored = store.blobs[key]
    assert stored[:2] == b"\x1f\x8b"
    assert _gzip.decompress(stored) == planned[key]
    # The manifest describes the DOCUMENT, not its stored encoding.
    manifest = parse_manifest(store.blobs[asset_key(COLLECTION, COLLECTION, plan.revision, MANIFEST_FILENAME)])
    entry = next(a for a in manifest.artefacts if a.role == "hierarchy")
    assert entry.sha256 == hashlib.sha256(planned[key]).hexdigest()
    assert entry.size == len(planned[key])

    expected = parse_hierarchy(planned[key])
    assert parse_hierarchy(stored) == expected
    assert FixtureLinesProvider(store.reader()).hierarchy(None, COLLECTION) == expected

    scope_root = tmp_path / "users" / "local-dev"
    for k, data in store.blobs.items():
        dest = scope_root / k
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    assert (scope_root / key).read_bytes()[:2] == b"\x1f\x8b"
    with TestClient(create_app(_settings(tmp_path))) as client:
        body = client.get(_scope_url(f"tree/published/{COLLECTION}")).json()
        assert body["rows"] == [list(r) for r in expected.rows]
        assert set(_by_id(_level(client, plan.revision, "site"))) == {"unit-1", "unit-2"}


def test_files_listing_accepts_a_bounded_prefix(client_and_revision):
    """`?prefix=` keeps browsing one collection off the O(scope) path."""
    client, _ = client_and_revision
    r = client.get("/api/scopes/user:me/files", params={"prefix": f"assets/{COLLECTION}/pump-a/"})
    assert r.status_code == 200, r.text
    keys = [row["key"] for row in r.json()["files"]]
    assert keys, "expected the bounded listing to find the node's blobs"
    assert all(k.startswith(f"assets/{COLLECTION}/pump-a/") for k in keys)
    # ... and it is genuinely narrower than the unbounded listing.
    everything = client.get("/api/scopes/user:me/files").json()["files"]
    assert len(keys) < len(everything)


# --- attributes -----------------------------------------------------------------------------------
#
# The route serves ONE node out of a document that covers a subject, so these are about what
# crosses the wire and about the three absences that all mean "nothing recorded".


def test_attributes_answers_one_node_from_the_published_document(client_and_revision):
    client, revision = client_and_revision
    r = client.get(_scope_url(f"attributes/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node"] == "pump-a"
    assert body["provider"] == FIXTURE_PROVIDER_ID
    assert body["revision"] == revision
    assert body["own"]["ref"] == "pump-a"
    assert body["groups"]["vendor"]["cat"] == body["kind"]


def test_attributes_never_ships_the_whole_subject_to_answer_one_selection(client_and_revision):
    """The document covers a subtree; the reply is one node. That is the reason it is a route
    rather than a blob the browser fetches."""
    client, _ = client_and_revision
    body = client.get(_scope_url(f"attributes/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a")).json()
    assert set(body) == {"node", "provider", "revision", "kind", "own", "groups", "quantities"}


def test_a_subject_that_publishes_no_attributes_reads_as_nothing_recorded(client_and_revision):
    """``unit-1`` is a branch: the fixture writes attributes for leaves only."""
    client, _ = client_and_revision
    r = client.get(_scope_url(f"attributes/{FIXTURE_PROVIDER_ID}/{COLLECTION}/unit-1"))
    assert r.status_code == 404
    assert "publishes no attributes" in r.json()["detail"]


def test_a_node_the_document_does_not_mention_is_a_404_too(client_and_revision):
    """Same answer as "no artefact": a caller asking what something is cannot act differently."""
    client, _ = client_and_revision
    r = client.get(_scope_url(f"attributes/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a"), params={"subject": "pump-b"})
    assert r.status_code == 404


def test_an_unpublished_node_is_a_404_and_not_a_500(client_and_revision):
    client, _ = client_and_revision
    r = client.get(_scope_url(f"attributes/{FIXTURE_PROVIDER_ID}/{COLLECTION}/no-such-node"))
    assert r.status_code == 404


def test_an_unreadable_attributes_blob_is_a_502_because_the_blob_is_there(client_and_revision, tmp_path):
    """A stored document core cannot read is not a missing one -- calling it missing sends the
    caller looking for the wrong problem."""
    from ada.comms.rest.routes.assets import clear_asset_attributes_cache

    client, revision = client_and_revision
    blob = tmp_path / "users" / "local-dev" / "assets" / COLLECTION / "pump-a" / revision / "attributes.json"
    blob.write_text('{"schema": "ada.assets/attributes@99", "nodes": {}}', encoding="utf-8")
    clear_asset_attributes_cache()

    r = client.get(_scope_url(f"attributes/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a"))
    assert r.status_code == 502
    assert "schema" in r.json()["detail"]


def test_the_document_is_parsed_once_per_revision(client_and_revision, monkeypatch):
    """The cache needs no invalidation: a revision's bytes never change, and a republish is a NEW
    revision at a new key. So a second selection in the same subject must not re-parse."""
    from ada.comms.rest.routes import assets as assets_routes

    client, _ = client_and_revision
    assets_routes.clear_asset_attributes_cache()

    calls = []
    real = assets_routes.parse_attributes
    monkeypatch.setattr(assets_routes, "parse_attributes", lambda raw: (calls.append(1), real(raw))[1])

    for _ in range(3):
        assert client.get(_scope_url(f"attributes/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a")).status_code == 200
    assert len(calls) == 1


# --- managing the files: list every one, delete what can go on its own -----------------------------


def _put(tmp_path, key: str, data: bytes = b"x") -> None:
    dest = tmp_path / "users" / "local-dev" / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def test_every_asset_file_is_listed_and_classified(client_and_revision, tmp_path):
    client, revision = client_and_revision
    _put(tmp_path, "assets/_staging/abc123/site-0.glb", b"12345")
    _put(tmp_path, f"_derived/assets/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a/{revision}/all/fp01/model.glb", b"123")

    body = client.get(_scope_url("files")).json()
    by_area: dict[str, list] = {}
    for f in body["files"]:
        by_area.setdefault(f["area"], []).append(f)

    published = by_area["published"]
    assert any(f["subject"] == "pump-a" and f["revision"] == revision and f["file"] == "asset.json" for f in published)
    assert all(f["collection"] == COLLECTION for f in published)
    [staged] = by_area["staged"]
    assert (staged["staging_id"], staged["file"], staged["size"]) == ("abc123", "site-0.glb", 5)
    [derived] = by_area["derived"]
    assert (derived["provider"], derived["subject"], derived["node"], derived["file"]) == (
        FIXTURE_PROVIDER_ID,
        "pump-a",
        None,
        "model.glb",
    )
    assert body["totals"]["staged"] == {"files": 1, "size": 5}


def test_a_staged_or_cached_file_is_deleted_on_its_own(client_and_revision, tmp_path):
    client, revision = client_and_revision
    staged = "assets/_staging/abc123/site-0.glb"
    derived = f"_derived/assets/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a/{revision}/all/fp01/model.glb"
    _put(tmp_path, staged)
    _put(tmp_path, derived)

    for key in (staged, derived):
        r = client.delete(_scope_url("files"), params={"key": key})
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] == [key]
    assert not client.get(_scope_url("files")).json()["totals"].get("staged")
    assert client.delete(_scope_url("files"), params={"key": staged}).status_code == 404


def test_a_published_file_is_refused_and_names_its_revision(client_and_revision):
    """One file out of a published revision is a publish that lists and badges like a working one
    and fails at load; it goes with its revision, through the unpublish route."""
    client, revision = client_and_revision
    key = f"assets/{COLLECTION}/pump-a/{revision}/asset.json"
    r = client.delete(_scope_url("files"), params={"key": key})
    assert r.status_code == 409
    body = r.json()
    assert (body["subject"], body["revision"]) == ("pump-a", revision)
    assert "unpublish" in body["reason"]
    assert client.get(_scope_url(f"delivery/published/{COLLECTION}/pump-a")).status_code == 200, "still published"


def test_a_key_outside_the_asset_areas_is_refused(client_and_revision):
    client, _ = client_and_revision
    assert client.delete(_scope_url("files"), params={"key": "models/plant.ifc"}).status_code == 400


# --- sources: one entry per publish, deleted with everything derived from it ----------------------


def test_each_publish_is_one_source_with_its_derived_files(client_and_revision, tmp_path):
    client, revision = client_and_revision
    _put(tmp_path, f"_derived/assets/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a/{revision}/all/fp01/model.glb", b"123")
    _put(tmp_path, f"_derived/assets/{FIXTURE_PROVIDER_ID}/gone/pump-z/{revision}/all/fp02/model.glb", b"1")

    body = client.get(_scope_url("sources")).json()
    [src] = [s for s in body["sources"] if s["collection"] == COLLECTION]
    assert (src["provider"], src["revision"]) == (FIXTURE_PROVIDER_ID, revision)
    assert src["subjects"] >= 2 and src["derived_files"] > src["subjects"]
    assert [o["key"] for o in body["orphans"]] == [
        f"_derived/assets/{FIXTURE_PROVIDER_ID}/gone/pump-z/{revision}/all/fp02/model.glb"
    ], "a cached build whose source is gone is an orphan"

    detail = client.get(_scope_url(f"sources/{COLLECTION}/{revision}"), params={"provider": FIXTURE_PROVIDER_ID}).json()
    assert any(d["key"].endswith("/model.glb") for d in detail["derived"])
    assert any(p["key"].endswith("/asset.json") for p in detail["published"])


def test_deleting_a_source_removes_everything_derived_from_it(client_and_revision, tmp_path):
    client, revision = client_and_revision
    build = f"_derived/assets/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a/{revision}/all/fp01/model.glb"
    _put(tmp_path, build)

    r = client.delete(_scope_url(f"sources/{COLLECTION}/{revision}"), params={"provider": FIXTURE_PROVIDER_ID})
    assert r.status_code == 200, r.text
    deleted = r.json()["deleted"]
    assert build in deleted
    manifests = [i for i, k in enumerate(deleted) if k.endswith("/asset.json")]
    others = [i for i, k in enumerate(deleted) if not k.endswith("/asset.json")]
    assert max(manifests) < min(others), "manifests go first"
    assert not [s for s in client.get(_scope_url("sources")).json()["sources"] if s["collection"] == COLLECTION]
    assert client.get(_scope_url(f"delivery/published/{COLLECTION}/pump-a")).status_code == 404


def test_two_providers_on_one_node_are_two_sources_deleted_apart(two_providers):
    """Provider B's claim on pump-b is its own source: deleting it takes B's publish and leaves A's
    claim on the same node exactly as it was."""
    client, rev_a, rev_b = two_providers
    sources = {(s["provider"], s["revision"]) for s in client.get(_scope_url("sources")).json()["sources"]}
    assert {(FIXTURE_PROVIDER_ID, rev_a), (SECOND_PROVIDER_ID, rev_b)} <= sources

    r = client.delete(_scope_url(f"sources/{COLLECTION}/{rev_b}"), params={"provider": SECOND_PROVIDER_ID})
    assert r.status_code == 200, r.text
    assert client.get(_scope_url(f"delivery/{SECOND_PROVIDER_ID}/{COLLECTION}/pump-b")).status_code == 404
    assert client.get(_scope_url(f"delivery/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-b")).status_code == 200
