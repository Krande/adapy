"""Attributes as the IFC provider publishes them: precomputed, keyed by node, read without a kernel.

The route these feed is the whole point of the choice -- attributes are written at publish time so
that a click costs a dictionary lookup rather than opening the source file. So the assertions are
about what lands in the blob, and about the published provider reading it back the way a request
would.
"""

from __future__ import annotations

import pathlib

import ifcopenshell
import pytest

from ada.assets.attributes import ATTRIBUTES_FILENAME, ATTRIBUTES_ROLE, parse_attributes
from ada.assets.ifc.attributes import node_attributes
from ada.assets.ifc.publish import publish_ifc
from ada.assets.keys import asset_key
from ada.assets.manifest import MANIFEST_FILENAME, parse_manifest
from ada.assets.published import PublishedAssetProvider

from .fake_store import FakeStore

CORPUS = pathlib.Path(__file__).parents[1] / "corpus" / "plant-a_v1.ifc"
STAGED_KEY = "assets/_staging/x/source.ifc"


def _raw() -> bytes:
    return CORPUS.read_bytes()


@pytest.fixture
def published():
    store = FakeStore()
    store.put_bytes(STAGED_KEY, _raw())
    result = publish_ifc(store, collection="plant-a", staged_key=STAGED_KEY, extracted_at="2026-01-01T00:00:00Z")
    return store, result


@pytest.fixture
def model():
    return ifcopenshell.open(str(CORPUS))


# --- extraction ---------------------------------------------------------------------------------


def test_a_beams_own_attributes_are_read_off_the_entity(model):
    beam = next(b for b in model.by_type("IfcBeam") if b.Name == "a1-bm0")
    attrs = node_attributes(beam)
    assert attrs.kind == "IfcBeam"
    assert attrs.own["Name"] == "a1-bm0"
    assert attrs.own["GlobalId"] == beam.GlobalId


def test_the_material_is_the_set_behind_the_usage_not_the_usage(model):
    """``IfcMaterialProfileSetUsage`` has no Name of its own.

    Asking the usage yields its CLASS where the set behind it says ``IPE200`` -- the exact shape
    of "a panel that technically showed a material and told nobody anything".
    """
    beam = next(b for b in model.by_type("IfcBeam") if b.Name == "a1-bm0")
    assert node_attributes(beam).own["Material"] == "IPE200"


def test_property_sets_are_grouped_by_their_own_names(model):
    """Grouping is the source's; flattening would lose which set a property came from."""
    site = model.by_type("IfcSite")[0]
    groups = node_attributes(site).groups
    assert groups["Properties"]["project"] == "plant-a"
    assert "schema" in groups["Properties"]


def test_ifcopenshells_own_step_id_marker_is_not_a_property(model):
    """``id`` is which entity the set came from -- a STEP line number, so it moves on every
    re-export. Keeping it would put noise into a document a reader diffs."""
    site = model.by_type("IfcSite")[0]
    assert "id" not in node_attributes(site).groups["Properties"]


def test_a_product_with_nothing_recorded_says_so_rather_than_failing(model):
    """Most spatial containers carry a name and nothing else. That is not an error state."""
    storey = next(s for s in model.by_type("IfcBuildingStorey") if s.Name == "StoreyA1")
    attrs = node_attributes(storey)
    assert attrs.groups == {}
    assert attrs.own["Name"] == "StoreyA1"


# --- what the publish writes --------------------------------------------------------------------


def test_every_published_subject_carries_an_attributes_artefact(published):
    store, result = published
    for subject in result.subjects:
        manifest = parse_manifest(store.get_bytes(asset_key("plant-a", subject, result.revision, MANIFEST_FILENAME)))
        entry = next(a for a in manifest.artefacts if a.role == ATTRIBUTES_ROLE)
        assert entry.file == ATTRIBUTES_FILENAME
        assert entry.size > 0


def test_the_artefacts_declared_size_and_hash_match_the_blob(published):
    """A manifest that misdescribes its own blob is unauditable -- the same rule the other
    artefacts are held to."""
    import hashlib

    store, result = published
    subject = result.subjects[0]
    manifest = parse_manifest(store.get_bytes(asset_key("plant-a", subject, result.revision, MANIFEST_FILENAME)))
    entry = next(a for a in manifest.artefacts if a.role == ATTRIBUTES_ROLE)
    raw = store.get_bytes(asset_key("plant-a", subject, result.revision, ATTRIBUTES_FILENAME))
    assert entry.size == len(raw)
    assert entry.sha256 == hashlib.sha256(raw).hexdigest()


def test_the_document_covers_the_products_in_the_subtree(published, model):
    store, result = published
    subject = result.subjects[0]
    doc = parse_attributes(store.get_bytes(asset_key("plant-a", subject, result.revision, ATTRIBUTES_FILENAME)))
    beam_guids = {b.GlobalId for b in model.by_type("IfcBeam")}
    assert beam_guids & set(doc.nodes), "no beam from the model reached the attributes document"
    for guid in beam_guids & set(doc.nodes):
        assert doc.node(guid).kind == "IfcBeam"


def test_the_manifest_is_still_written_last(published):
    """Attributes join the per-subject writes BEFORE the manifest: a half-written publish must
    stay invisible rather than discoverable-and-incomplete."""
    _, result = published
    subject = result.subjects[0]
    keys = list(result.written)
    attrs_at = keys.index(asset_key("plant-a", subject, result.revision, ATTRIBUTES_FILENAME))
    manifest_at = keys.index(asset_key("plant-a", subject, result.revision, MANIFEST_FILENAME))
    assert attrs_at < manifest_at


# --- reading it back the way a request does -------------------------------------------------------


def test_the_published_provider_answers_a_node_from_the_blob(published, model):
    store, result = published
    provider = PublishedAssetProvider(store.reader(), provider_id="ifc")
    subject = result.subjects[0]
    doc = parse_attributes(store.get_bytes(asset_key("plant-a", subject, result.revision, ATTRIBUTES_FILENAME)))
    node = next(iter(doc.nodes))

    attrs = provider.attributes(None, "plant-a", node, subject=subject)
    assert attrs is not None
    assert attrs.kind == doc.node(node).kind


def test_a_covered_node_is_reached_through_the_subject_that_covers_it(published, model):
    """A leaf published under a root has no manifest of its own; naming the covering subject is
    how it is asked for, exactly as a build request does it."""
    store, result = published
    provider = PublishedAssetProvider(store.reader(), provider_id="ifc")
    subject = result.subjects[0]
    doc = parse_attributes(store.get_bytes(asset_key("plant-a", subject, result.revision, ATTRIBUTES_FILENAME)))
    covered = next(n for n in doc.nodes if n != subject)

    assert provider.attributes(None, "plant-a", covered, subject=subject) is not None
    # ...and asking for it as its OWN subject finds no manifest, which is an absence, not a fault.
    assert provider.attributes(None, "plant-a", covered) is None


def test_a_node_the_document_does_not_mention_is_absent_not_an_error(published):
    store, result = published
    provider = PublishedAssetProvider(store.reader(), provider_id="ifc")
    assert provider.attributes(None, "plant-a", "0notaguid0notaguid00", subject=result.subjects[0]) is None
