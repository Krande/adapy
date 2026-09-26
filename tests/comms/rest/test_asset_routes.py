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
    FakeStore,
    publish_fixture,
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


def test_providers_always_offers_the_built_in_published_one(client_and_revision):
    """A scope may hold assets from a provider THIS process has never heard of."""
    client, _ = client_and_revision
    r = client.get(_scope_url("providers"))
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()["providers"]]
    assert "published" in ids


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
