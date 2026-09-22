"""The projection round-trip: the leaf set of a published spine equals the descendant PRODUCTS of
that root -- checked against an ORACLE independent of ``ada.assets.ifc.walk`` itself
(``ifcopenshell.util.element.get_decomposition``, ifcopenshell's own stdlib decomposition walker),
so a bug in ``walk.py`` cannot confirm itself.
"""

from __future__ import annotations

import pathlib

import ifcopenshell
import ifcopenshell.util.element as ifc_element
import pytest

from ada.assets.ifc.publish import publish_ifc
from ada.assets.published import PublishedAssetProvider

from .fake_store import FakeStore

CORPUS = pathlib.Path(__file__).parents[1] / "corpus" / "plant-a_v1.ifc"


def _raw() -> bytes:
    return CORPUS.read_bytes()


@pytest.fixture
def published():
    store = FakeStore()
    store.put_bytes("assets/_staging/x/source.ifc", _raw())
    result = publish_ifc(
        store, collection="plant-a", staged_key="assets/_staging/x/source.ifc", extracted_at="2026-01-01T00:00:00Z"
    )
    return store, result


def test_leaf_set_of_a_published_spine_equals_descendant_products(published):
    store, _ = published
    provider = PublishedAssetProvider(store.reader(), provider_id="ifc")
    f = ifcopenshell.file.from_string(_raw().decode())
    site_b = next(s for s in f.by_type("IfcSite") if s.Name == "SiteB")

    slice_ = provider.hierarchy(None, "plant-a", root=site_b.GlobalId)
    published_leaf_ids = {r["id"] for r in slice_.records() if r["leaf"]}

    oracle = {e.GlobalId for e in ifc_element.get_decomposition(site_b) if e.Representation is not None}
    assert published_leaf_ids == oracle
    assert len(oracle) == 20  # 6 beams + 4 plates per storey, 2 storeys under SiteB


def test_branch_rows_in_the_spine_are_not_leaves(published):
    store, _ = published
    provider = PublishedAssetProvider(store.reader(), provider_id="ifc")
    f = ifcopenshell.file.from_string(_raw().decode())
    site_a = next(s for s in f.by_type("IfcSite") if s.Name == "SiteA")

    slice_ = provider.hierarchy(None, "plant-a", root=site_a.GlobalId)
    by_label = {r["label"]: r for r in slice_.records()}
    assert by_label["StoreyA1"]["leaf"] is False
    assert by_label["StoreyA1"]["kind"] == "ifcbuildingstorey"
    assert by_label["AssemblyAA"]["leaf"] is False
    assert by_label["AssemblyAA"]["kind"] == "ifcelementassembly"
    assert by_label["a1-bm0"]["leaf"] is True
    assert by_label["a1-bm0"]["kind"] == "ifcbeam"
