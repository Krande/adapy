"""Phase 3 ingest tests: build a CellGraph from adapy models / IFC."""
from __future__ import annotations

import ada
import ada.topology as topo
from ada.cad import active_backend


def test_from_part_two_boxes():
    a = ada.Assembly("A") / (
        ada.Part("p")
        / [
            ada.PrimBox("RoomA", (0, 0, 0), (1, 1, 1), metadata={"NAME": "RoomA", "PRIORITY": 1}),
            ada.PrimBox("RoomB", (1, 0, 0), (2, 1, 1), metadata={"NAME": "RoomB", "PRIORITY": 2}),
        ]
    )
    cg = topo.from_part(a)
    assert len(cg.cells) == 2
    assert len(cg.get_external_faces()) == 10  # shared x=1 wall internal
    assert sorted(c.name for c in cg.cells) == ["RoomA", "RoomB"]
    assert len(cg.get_internal_walls()) == 1


def test_from_ifc_builds_graph(example_files):
    # adapy's own importer -> B-rep shapes -> cells (no re-tessellation).
    # box_rotated.ifc has box shapes; accept all shapes (ifc_types=None).
    cg = topo.from_ifc(example_files / "ifc_files/box_rotated.ifc", ifc_types=None)
    assert len(cg.cells) >= 1
    for cell in cg.cells:
        assert len(cell.faces) == 6  # each is a box cell
        # metadata carried the IFC type through
        assert cell.metadata.get("IFC_type", "").startswith("Ifc")


def test_unify_coplanar_faces_noop_on_box():
    be = active_backend()
    box = ada.PrimBox("x", (0, 0, 0), (1, 1, 1)).solid_occ()
    # A clean box has no adjacent coplanar faces to merge -> still 6 faces.
    assert len(be.faces(be.unify_coplanar_faces(box))) == 6
