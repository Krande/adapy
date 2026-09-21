"""One collection, two providers, two unrelated source formats, one tree.

This is the property that makes the asset store worth having over a per-provider panel: a project
does not have to pick one source of truth. Some branches can be fed by a provider whose source is
one format while sibling branches are developed in another, each with its own build capability,
and the browser renders them as a single hierarchy.

Core learns neither format. Both fixtures translate into core's schemas at publish time.
"""

import json

from tests.core.assets.fixture_provider.provider import (
    BUILD_CAPABILITY,
    FIXTURE_PROVIDER_ID,
    FIXTURE_SOURCE_LINES,
    FakeStore,
    publish_fixture,
)
from tests.core.assets.fixture_provider.second import (
    SECOND_BUILD_CAPABILITY,
    SECOND_PROVIDER_ID,
    publish_second_branch,
    second_branch_nodes,
)

from ada.assets.keys import asset_key
from ada.assets.manifest import HIERARCHY_FILENAME, MANIFEST_FILENAME, parse_manifest
from ada.assets.projection import build_hierarchy, parse_hierarchy
from ada.assets.published import PublishedAssetProvider

COLLECTION = "fixture-a"
INSTANT = "2026-09-21T14:30:01Z"


def _mixed_store() -> tuple[FakeStore, str]:
    """Publish provider A's whole tree, then graft provider B's branch into the same collection."""
    store = FakeStore()
    revision = publish_fixture(store, collection=COLLECTION, instant=INSTANT)
    publish_second_branch(store, collection=COLLECTION, instant=INSTANT, attach_to="site")

    # Rewrite the collection spine to span both. The spine is the COLLECTION's; the branches are
    # each provider's -- which is exactly how a real mixed publish composes.
    existing = parse_hierarchy(store.get_bytes(asset_key(COLLECTION, COLLECTION, revision, HIERARCHY_FILENAME)))
    rows = [dict(r) for r in existing.records()]
    for row in rows:
        row["provider"] = FIXTURE_PROVIDER_ID
    combined = build_hierarchy(
        provider=FIXTURE_PROVIDER_ID,
        collection=COLLECTION,
        produced_at=INSTANT,
        nodes=rows + second_branch_nodes(attach_to="site"),
        depth=3,
    )
    store.put(asset_key(COLLECTION, COLLECTION, revision, HIERARCHY_FILENAME), combined.to_json())
    return store, revision


def test_one_tree_spans_both_providers():
    store, revision = _mixed_store()
    provider = PublishedAssetProvider(store.reader())
    slice_ = provider.hierarchy(None, COLLECTION)
    by_id = {r["id"]: r for r in slice_.records()}

    # Provider B's branch hangs off provider A's node, in the same tree.
    assert by_id["unit-3"]["parent"] == "site"
    assert by_id["pump-a"]["parent"] == "unit-1"
    assert slice_.is_mixed is True


def test_each_node_names_its_own_producer():
    store, _ = _mixed_store()
    slice_ = PublishedAssetProvider(store.reader()).hierarchy(None, COLLECTION)
    by_id = {r["id"]: r for r in slice_.records()}
    assert by_id["pump-a"]["provider"] == FIXTURE_PROVIDER_ID
    assert by_id["valve-x"]["provider"] == SECOND_PROVIDER_ID


def test_sibling_branches_carry_different_build_capabilities():
    """The point of the whole exercise: which builder runs is per node, not per collection."""
    store, _ = _mixed_store()
    provider = PublishedAssetProvider(store.reader())
    assert provider.delivery(None, COLLECTION, "pump-b").capability == BUILD_CAPABILITY
    assert provider.delivery(None, COLLECTION, "valve-x").capability == SECOND_BUILD_CAPABILITY


def test_each_manifest_records_the_provider_that_produced_it():
    store, revision = _mixed_store()
    a = parse_manifest(store.get_bytes(asset_key(COLLECTION, "pump-b", revision, MANIFEST_FILENAME)))
    b = parse_manifest(store.get_bytes(asset_key(COLLECTION, "valve-x", revision, MANIFEST_FILENAME)))
    assert a.provider == FIXTURE_PROVIDER_ID
    assert b.provider == SECOND_PROVIDER_ID
    # Their options are in their OWN vocabularies; core interprets neither.
    assert "ref" in a.build.options
    assert "outline_ref" in b.build.options


def test_the_two_sources_have_nothing_in_common():
    """Guards the fixtures themselves: if they converged, this suite would stop proving anything."""
    from tests.core.assets.fixture_provider.second import SECOND_SOURCE_TEXT

    assert "|" in SECOND_SOURCE_TEXT and "{" not in SECOND_SOURCE_TEXT  # outline, not JSON
    assert isinstance(FIXTURE_SOURCE_LINES[0], dict)  # records, not an outline
    json.dumps(FIXTURE_SOURCE_LINES)  # A is JSON-serialisable; B is not JSON at all


def test_single_source_collection_needs_no_provider_column():
    """A mixed collection pays for the column; a single-source one does not."""
    store = FakeStore()
    publish_fixture(store, collection="solo", instant=INSTANT)
    slice_ = PublishedAssetProvider(store.reader()).hierarchy(None, "solo")
    assert "provider" not in slice_.cols
    assert slice_.is_mixed is False
    assert all(r["provider"] == FIXTURE_PROVIDER_ID for r in slice_.records())
