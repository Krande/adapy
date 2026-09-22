"""``ada.assets.catalogue_adapter`` over ``StubExternalModelCatalog`` -- the ``mesh`` delivery
kind's live witness (Decision 1: "the mesh path is driven in CI by the fixture provider ... and by
the catalogue adapter over StubExternalModelCatalog").
"""

from __future__ import annotations

from ada.assets.catalogue_adapter import (
    CatalogueAssetProvider,
    _decode_node,
    _encode_node,
    register_catalogue_asset_provider,
)
from ada.assets.provider import MeshDelivery
from ada.assets.registry import asset_provider, asset_providers, clear_asset_providers
from ada.plugins.external_models.catalog import (
    Collection,
    ExternalModel,
    StubExternalModelCatalog,
)


def _adapter(provider_id: str = "stub-catalogue") -> CatalogueAssetProvider:
    return CatalogueAssetProvider(StubExternalModelCatalog(), provider_id=provider_id)


# --- collections -------------------------------------------------------------------------------


def test_collections_come_straight_from_list_collections():
    adapter = _adapter()
    by_id = {c.id: c for c in adapter.collections()}
    assert set(by_id) == {"demo", "samples"}
    assert by_id["demo"].node_count == 2  # kitchen.glb, pipe-rack.glb
    assert by_id["samples"].node_count == 1


# --- hierarchy -----------------------------------------------------------------------------


def test_hierarchy_is_a_depth_two_tree_with_mesh_leaves():
    adapter = _adapter()
    slice_ = adapter.hierarchy(None, "demo")
    assert slice_.schema == "ada.assets/hierarchy@1"
    assert slice_.provider == "stub-catalogue"
    assert slice_.collection == "demo"

    records = list(slice_.records())
    root = next(r for r in records if r["id"] == "demo")
    assert root["parent"] is None
    assert root["leaf"] is False
    assert root["delivery"] == ""

    models = {r["id"]: r for r in records if r["id"] != "demo"}
    assert set(models) == {"demo$kitchen", "demo$pipe-rack"}
    for row in models.values():
        assert row["parent"] == "demo"
        assert row["leaf"] is True
        assert row["delivery"] == "mesh"


def test_node_ids_are_reversible():
    node_id = _encode_node("demo", "kitchen")
    assert node_id == "demo$kitchen"
    assert _decode_node(node_id) == ("demo", "kitchen")


def test_a_model_id_that_would_break_reversibility_is_skipped_not_mis_encoded():
    """A model id containing the separator would be ambiguous to decode -- skip it rather than
    emit a node the adapter itself could not read back."""

    class _DollarModelCatalog:
        def list_collections(self):
            return [Collection(id="ok", name="ok")]

        def list_models(self, collection):
            return [
                ExternalModel(id="good", name="good", collection=collection, key="ok/good.glb"),
                ExternalModel(id="has$dollar", name="bad", collection=collection, key="ok/has$dollar.glb"),
            ]

        def model_download_url(self, collection, model_id, *, expires_in_seconds=900):
            return f"stub://{collection}/{model_id}"

    adapter = CatalogueAssetProvider(_DollarModelCatalog(), provider_id="dollar")
    slice_ = adapter.hierarchy(None, "ok")
    ids = {r["id"] for r in slice_.records()}
    assert "ok$good" in ids
    assert "ok$has$dollar" not in ids
    assert len(ids) == 2  # root + the one encodable model; the ambiguous one is gone, not renamed


def test_a_collection_id_that_cannot_ride_in_a_route_path_is_skipped():
    class _SpaceCollectionCatalog:
        def list_collections(self):
            return [Collection(id="ok", name="ok"), Collection(id="bad col", name="bad")]

        def list_models(self, collection):
            return []

        def model_download_url(self, collection, model_id, *, expires_in_seconds=900):
            return f"stub://{collection}/{model_id}"

    adapter = CatalogueAssetProvider(_SpaceCollectionCatalog(), provider_id="space")
    ids = {c.id for c in adapter.collections()}
    assert ids == {"ok"}


# --- delivery ------------------------------------------------------------------------------


def test_delivery_returns_a_mesh_claim():
    adapter = _adapter()
    claim = adapter.delivery(None, "demo", "demo$kitchen")
    assert isinstance(claim, MeshDelivery)
    assert claim.kind == "mesh"
    assert claim.url.startswith("stub://external-models/demo/kitchen")


def test_delivery_is_minted_per_call_never_cached():
    """Two independent calls must each reach the catalogue -- a mesh URL may be presigned and
    expire, so nothing here may hand back a stored answer."""

    class _CountingCatalog:
        def __init__(self):
            self.calls = 0

        def list_collections(self):
            return [Collection(id="c", name="c")]

        def list_models(self, collection):
            return [ExternalModel(id="m", name="m", collection=collection, key=f"{collection}/m.glb")]

        def model_download_url(self, collection, model_id, *, expires_in_seconds=900):
            self.calls += 1
            return f"stub://{collection}/{model_id}?n={self.calls}"

    catalog = _CountingCatalog()
    adapter = CatalogueAssetProvider(catalog, provider_id="counting")
    first = adapter.delivery(None, "c", "c$m")
    second = adapter.delivery(None, "c", "c$m")
    assert catalog.calls == 2
    assert first.url != second.url


def test_delivery_reads_the_catalogues_current_revision_when_it_versions_the_model():
    adapter = _adapter()
    claim = adapter.delivery(None, "demo", "demo$kitchen")
    # StubExternalModelCatalog's fixture marks "2024-01-02" current for demo/kitchen.
    assert claim.revision == "2024-01-02T00:00:00Z"


def test_delivery_uses_the_documented_placeholder_for_an_unversioned_model():
    """samples/beam-assembly carries no revision history in the stub -- the adapter must not
    invent an instant for it."""
    adapter = _adapter()
    claim = adapter.delivery(None, "samples", "samples$beam-assembly")
    assert claim.revision == "unversioned"


def test_delivery_passes_an_explicit_revision_through():
    adapter = _adapter()
    claim = adapter.delivery(None, "demo", "demo$kitchen", revision="2024-01-01")
    assert claim.revision == "2024-01-01"
    assert claim.url.endswith("@2024-01-01")


def test_delivery_is_none_for_a_node_naming_a_different_collection():
    adapter = _adapter()
    assert adapter.delivery(None, "samples", "demo$kitchen") is None


def test_delivery_is_none_for_an_undecodable_node():
    adapter = _adapter()
    assert adapter.delivery(None, "demo", "not-a-composed-id") is None


# --- registration --------------------------------------------------------------------------


def test_register_catalogue_asset_provider_is_opt_in_not_an_import_side_effect():
    clear_asset_providers()
    try:
        assert "stub-cat" not in {p["id"] for p in asset_providers()}
        register_catalogue_asset_provider("stub-cat", StubExternalModelCatalog, label="Stub catalogue")
        provider = asset_provider("stub-cat")
        assert isinstance(provider, CatalogueAssetProvider)
        assert provider.delivery_kinds == ("mesh",)
    finally:
        clear_asset_providers()
