"""``asset-publish-ifc`` (as a plain callable, ``publish_ifc``) driven against the ``plant-a``
corpus: whole-file fan-out, the ``--root`` then ``--leaf`` update model, ``dry_run``, and the
``replace`` occupancy refusal.
"""

from __future__ import annotations

import pathlib

import ifcopenshell
import pytest

from ada.assets.ifc.publish import IfcPublishError, publish_ifc
from ada.assets.keys import ASSET_PREFIX
from ada.assets.manifest import parse_manifest
from ada.assets.published import PublishedAssetProvider

from .fake_store import FakeStore

CORPUS = pathlib.Path(__file__).parents[1] / "corpus" / "plant-a_v1.ifc"
STAGED_KEY = "assets/_staging/x/source.ifc"


def _raw() -> bytes:
    return CORPUS.read_bytes()


@pytest.fixture
def store():
    s = FakeStore()
    s.put_bytes(STAGED_KEY, _raw())
    return s


@pytest.fixture
def node_ids():
    f = ifcopenshell.file.from_string(_raw().decode())
    by_label = {p.Name: p.GlobalId for p in f.by_type("IfcProduct") if p.Name}
    return by_label


# --- whole-file fan-out -------------------------------------------------------------------------


def test_whole_file_publish_fans_out_two_sites_one_source_one_index(store):
    result = publish_ifc(store, collection="plant-a", staged_key=STAGED_KEY, extracted_at="2026-01-01T00:00:00Z")

    assert len(result.subjects) == 2  # SiteA + SiteB, not the nested-under count

    source_keys = [k for k in result.written if k.endswith("/source.ifc")]
    assert len(source_keys) == 1  # written ONCE, shared by both site manifests

    index_manifest_key = f"{ASSET_PREFIX}/plant-a/plant-a/{result.revision}/asset.json"
    assert index_manifest_key in result.written
    collection_manifest = parse_manifest(store.get_bytes(index_manifest_key))
    assert collection_manifest.node is None  # the collection subject, not a product
    assert collection_manifest.counts["sites"] == 2

    for subject in result.subjects:
        manifest = parse_manifest(store.get_bytes(f"{ASSET_PREFIX}/plant-a/{subject}/{result.revision}/asset.json"))
        assert manifest.delivery == "build"
        assert manifest.build.capability == "asset-build-ifc"
        # every site manifest references the ONE shared source blob by absolute key
        source_entry = next(a for a in manifest.artefacts if a.role == "source")
        assert source_entry.key == source_keys[0]


def test_manifests_are_written_last(store):
    """Every 'asset.json' key comes after its own subject's 'hierarchy.json' in write order, and
    the collection manifest is the very last key written."""
    result = publish_ifc(store, collection="plant-a", staged_key=STAGED_KEY, extracted_at="2026-01-01T00:00:00Z")
    order = {key: i for i, key in enumerate(result.written)}
    for subject in result.subjects:
        h = f"{ASSET_PREFIX}/plant-a/{subject}/{result.revision}/hierarchy.json"
        m = f"{ASSET_PREFIX}/plant-a/{subject}/{result.revision}/asset.json"
        assert order[h] < order[m]
    assert result.written[-1] == f"{ASSET_PREFIX}/plant-a/plant-a/{result.revision}/asset.json"


# --- --root then --leaf (leaf-addressable publishing) -------------------------------------------


def test_root_then_leaf_records_hierarchy_revision(store, node_ids):
    storey_guid = node_ids["StoreyA1"]
    beam_guid = node_ids["a1-bm0"]

    root_result = publish_ifc(
        store, collection="plant-a", staged_key=STAGED_KEY, root=storey_guid, extracted_at="2026-01-01T00:00:00Z"
    )
    leaf_result = publish_ifc(
        store, collection="plant-a", staged_key=STAGED_KEY, leaf=beam_guid, extracted_at="2026-01-02T00:00:00Z"
    )

    assert leaf_result.revision > root_result.revision  # lexical == chronological (Decision 2a)

    provider = PublishedAssetProvider(store.reader(), provider_id="ifc")
    storey_manifest = provider.manifest("plant-a", storey_guid)
    beam_manifest = provider.manifest("plant-a", beam_guid)
    assert beam_manifest.hierarchy_revision == storey_manifest.revision
    assert storey_manifest.hierarchy_revision is None  # nothing published above it yet

    # No collection index was written by either scoped publish.
    assert f"{ASSET_PREFIX}/plant-a/plant-a/{root_result.revision}/asset.json" not in root_result.written
    assert f"{ASSET_PREFIX}/plant-a/plant-a/{leaf_result.revision}/asset.json" not in leaf_result.written


def test_leaf_publish_gets_its_own_fresh_source_by_default(store, node_ids):
    beam_guid = node_ids["a1-bm0"]
    result = publish_ifc(
        store, collection="plant-a", staged_key=STAGED_KEY, leaf=beam_guid, extracted_at="2026-01-01T00:00:00Z"
    )
    manifest = parse_manifest(store.get_bytes(f"{ASSET_PREFIX}/plant-a/{beam_guid}/{result.revision}/asset.json"))
    source_entry = next(a for a in manifest.artefacts if a.role == "source")
    assert source_entry.file == "source.ifc"  # a SIBLING blob, not a shared absolute key
    assert manifest.counts == {"nodes": 1, "leaves": 1}


# --- dry_run -------------------------------------------------------------------------------------


def test_dry_run_writes_nothing_and_says_so(store):
    result = publish_ifc(
        store, collection="plant-a", staged_key=STAGED_KEY, extracted_at="2026-01-01T00:00:00Z", dry_run=True
    )
    assert result.dry_run is True
    # one shared source + the collection's hierarchy/asset + per subject a
    # hierarchy/ifc-index/attributes/asset -- the plan is still derived...
    assert len(result.written) == 11
    assert store.blobs == {STAGED_KEY: _raw()}  # ...but nothing beyond the staged input exists


# --- replace refusal ------------------------------------------------------------------------------


def test_replace_is_the_only_way_into_an_occupied_prefix(store, node_ids):
    storey_guid = node_ids["StoreyA1"]
    publish_ifc(
        store, collection="plant-a", staged_key=STAGED_KEY, root=storey_guid, extracted_at="2026-01-01T00:00:00Z"
    )

    with pytest.raises(IfcPublishError, match="already occupied"):
        publish_ifc(
            store, collection="plant-a", staged_key=STAGED_KEY, root=storey_guid, extracted_at="2026-01-01T00:00:00Z"
        )

    result = publish_ifc(
        store,
        collection="plant-a",
        staged_key=STAGED_KEY,
        root=storey_guid,
        extracted_at="2026-01-01T00:00:00Z",
        replace=True,
    )
    assert result.revision == "20260101T000000Z"


# --- instant refusal -------------------------------------------------------------------------------


def test_refuses_when_neither_owner_history_nor_extracted_at_is_available(store):
    """plant-a_v1.ifc's IfcProject has no OwnerHistory (adapy's writer only stamps one on the
    spatial elements it creates, `store.py:157`), so a publish with no `extracted_at` must name
    the fix rather than guess a revision."""
    with pytest.raises(IfcPublishError, match="extracted_at"):
        publish_ifc(store, collection="plant-a", staged_key=STAGED_KEY)


def test_both_root_and_leaf_is_refused(store, node_ids):
    with pytest.raises(IfcPublishError, match="at most one"):
        publish_ifc(
            store,
            collection="plant-a",
            staged_key=STAGED_KEY,
            root=node_ids["StoreyA1"],
            leaf=node_ids["a1-bm0"],
            extracted_at="2026-01-01T00:00:00Z",
        )
