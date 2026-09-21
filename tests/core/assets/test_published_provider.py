"""The built-in ``published`` provider, driven end to end by a private-format fixture.

The claim under test is Decision 1's: a provider with its own source format needs NO runtime
hierarchy code. FixtureLinesProvider adds nothing but an id -- everything below is served by
PublishedAssetProvider reading blobs written under the key grammar.
"""

import pytest
from tests.core.assets.fixture_provider import FIXTURE_PROVIDER_ID, FixtureLinesProvider
from tests.core.assets.fixture_provider.provider import (
    BUILD_CAPABILITY,
    FakeStore,
    publish_fixture,
    register_fixture_provider,
)

from ada.assets.keys import asset_key
from ada.assets.manifest import MANIFEST_FILENAME
from ada.assets.provider import BuildDelivery, MeshDelivery, provider_capabilities
from ada.assets.registry import (
    AssetProviderError,
    asset_provider,
    asset_providers,
    clear_asset_providers,
    register_asset_provider,
)


@pytest.fixture
def store_and_provider():
    store = FakeStore()
    revision = publish_fixture(store)
    yield store, FixtureLinesProvider(store.reader()), revision
    clear_asset_providers()


def test_hierarchy_comes_back_in_cores_vocabulary(store_and_provider):
    """The vendor's ref/up/title/cat became core's id/parent/label/kind at publish time."""
    _, provider, _ = store_and_provider
    slice_ = provider.hierarchy(None, "fixture-a")
    by_id = {r["id"]: r for r in slice_.records()}
    assert by_id["pump-a"]["label"] == "Pump A"
    assert by_id["pump-a"]["kind"] == "equipment"
    assert by_id["pump-a"]["parent"] == "unit-1"
    assert by_id["pump-a"]["leaf"] is True
    assert by_id["site"]["leaf"] is False


def test_collections_are_folded_from_the_listing(store_and_provider):
    _, provider, revision = store_and_provider
    (info,) = provider.collections()
    assert info.id == "fixture-a"
    assert info.latest_revision == revision
    assert info.node_count == 7  # 6 nodes + the collection-level subject


def test_mesh_claim_resolves_to_the_stored_artefact(store_and_provider):
    store, provider, revision = store_and_provider
    claim = provider.delivery(None, "fixture-a", "pump-a")
    assert isinstance(claim, MeshDelivery)
    assert claim.revision == revision
    assert store.get_bytes(claim.url)[:4] == b"glTF"  # a real GLB, not a placeholder


def test_build_claim_carries_opaque_options(store_and_provider):
    """Core forwards `options` verbatim and never derives a key from anything inside it."""
    _, provider, revision = store_and_provider
    claim = provider.delivery(None, "fixture-a", "pump-b")
    assert isinstance(claim, BuildDelivery)
    assert claim.capability == BUILD_CAPABILITY
    assert claim.revision == revision
    assert claim.options["ref"] == "pump-b"  # a vendor field core does not interpret


def test_branch_nodes_have_no_delivery(store_and_provider):
    _, provider, _ = store_and_provider
    assert provider.delivery(None, "fixture-a", "unit-1") is None


def test_half_written_revision_does_not_shadow_the_last_good_one(store_and_provider):
    """Manifests are written last, so a newer revision without one is a dead publish."""
    store, provider, good = store_and_provider
    newer = "20270101T000000Z"
    store.put(asset_key("fixture-a", "pump-a", newer, "source.jsonl"), b"{}")
    assert provider.manifest("fixture-a", "pump-a").revision == good


def test_manifest_stored_where_it_says_it_is(store_and_provider):
    store, provider, revision = store_and_provider
    key = asset_key("fixture-a", "pump-a", revision, MANIFEST_FILENAME)
    doc = store.get_bytes(key).replace(b'"subject": "pump-a"', b'"subject": "pump-z"')
    store.put(key, doc)
    with pytest.raises(ValueError, match="not where it is stored"):
        provider.manifest("fixture-a", "pump-a")


# --- registry ----------------------------------------------------------------------------------


def test_registry_is_idempotent_by_id_but_refuses_a_conflict(store_and_provider):
    store, _, _ = store_and_provider
    factory = lambda: FixtureLinesProvider(store.reader())  # noqa: E731
    register_asset_provider("x", factory)
    register_asset_provider("x", factory)  # same factory: a no-op, as an entry point may load twice
    with pytest.raises(AssetProviderError, match="already registered with a different factory"):
        register_asset_provider("x", lambda: FixtureLinesProvider(store.reader()))


def test_providers_listing_reports_capabilities_by_presence(store_and_provider):
    store, _, _ = store_and_provider
    register_fixture_provider(store)
    (entry,) = [p for p in asset_providers() if p["id"] == FIXTURE_PROVIDER_ID]
    assert entry["label"] == "Fixture (lines)"
    assert entry["live"] is True
    assert entry["delivery"] == ["mesh", "build"]
    assert "tree" in entry["capabilities"]


def test_a_broken_provider_is_visible_rather_than_absent(store_and_provider):
    """A silently missing provider looks identical to one that was never installed."""

    def boom():
        raise RuntimeError("catalogue unreachable")

    register_asset_provider("broken", boom, label="Broken")
    (entry,) = [p for p in asset_providers() if p["id"] == "broken"]
    assert "catalogue unreachable" in entry["error"]


def test_capabilities_are_read_off_the_object(store_and_provider):
    _, provider, _ = store_and_provider
    caps = provider_capabilities(provider)
    assert caps["tree"] is True
    assert caps["publish"] is False and caps["build"] is False  # it publishes out-of-band


def test_unknown_provider_id_names_what_is_registered(store_and_provider):
    store, _, _ = store_and_provider
    register_fixture_provider(store)
    with pytest.raises(AssetProviderError, match=f"registered: {FIXTURE_PROVIDER_ID}"):
        asset_provider("nope")
