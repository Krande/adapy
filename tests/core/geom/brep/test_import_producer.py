"""Stage 3: Genie SAT -> BRepStore import producer (identity-preserving)."""

import pytest

from ada.cadit.sat.read.to_brep import genie_xml_to_brep, sat_store_to_brep
from ada.cadit.sat.store import SatReaderFactory
from ada.geom.brep import BRepStore


@pytest.fixture
def curved_plates_xml(fem_files):
    return (fem_files / "sesam/curved_plates.xml").resolve().absolute()


def test_import_builds_a_store(curved_plates_xml):
    store = genie_xml_to_brep(curved_plates_xml)
    assert isinstance(store, BRepStore)
    s = store.summary()
    assert s["faces"] > 0
    assert s["edges"] > 0
    assert s["vertices"] > 0
    assert s["lumps"] >= 1


def test_import_preserves_shared_identity(curved_plates_xml):
    """Two faces meeting along an edge must reference ONE shared BEdge — proven by
    at least one edge carrying two coedges (a shared boundary), and by there being
    fewer edges than the sum of per-face loop lengths (i.e. sharing happened)."""
    store = genie_xml_to_brep(curved_plates_xml)

    shared = [e for e in store.edges.values() if len(store.coedges_on(e)) >= 2]
    assert shared, "a welded plate field must have at least one shared edge"

    # sharing check: coedges outnumber edges (each shared edge is used >1x)
    assert store.summary()["coedges"] > store.summary()["edges"]


def test_import_source_ids_recorded(curved_plates_xml):
    store = genie_xml_to_brep(curved_plates_xml)
    # every entity should carry the SAT record index it came from
    assert all(v.source_id is not None for v in store.vertices.values())
    assert all(e.source_id is not None for e in store.edges.values())
    assert all(f.source_id is not None for f in store.faces.values())


def test_import_is_complete_no_silent_drops(curved_plates_xml):
    """Completeness contract: every SAT edge/face is either built or recorded as
    unresolved — never silently absent. Faces match exactly; edges built plus
    unresolved-edge records equal the SAT edge count."""
    from ada.cadit.gxml.sat_helpers import write_xml_sat_text_to_file

    sat_path = curved_plates_xml.with_suffix(".sat")
    if not sat_path.exists():
        write_xml_sat_text_to_file(xml_file=curved_plates_xml, out_file=sat_path)
    factory = SatReaderFactory(sat_path)
    factory.load_sat_data_from_file()
    ss = factory.sat_store

    n_face = sum(1 for r in ss.iter() if r.type == "face")
    n_edge = sum(1 for r in ss.iter() if r.type == "edge")

    store = sat_store_to_brep(ss)
    s = store.summary()

    unres_edges = sum(1 for u in store.unresolved if u.kind == "edge")
    unres_faces = sum(1 for u in store.unresolved if u.kind == "face")
    # every edge accounted for
    assert s["edges"] + unres_edges == n_edge, (s, unres_edges, n_edge)
    # every face accounted for
    assert s["faces"] + unres_faces == n_face, (s, unres_faces, n_face)
