"""Golden tests for the ada.topology cell graph (both backends)."""
from __future__ import annotations

import ada
from ada.topology import CellGraph, TopologyMetadata
from ada.topology.entities import TopoSpace
from ada.cad import active_backend


def _cell_solids(*specs):
    """Build (solid, TopoSpace) pairs from (name, p1, p2, kwargs) specs."""
    pairs = []
    for name, p1, p2, kw in specs:
        sp = TopoSpace(NAME=name, X=p1[0], Y=p1[1], Z=p1[2], DX=p2[0] - p1[0], DY=p2[1] - p1[1], DZ=p2[2] - p1[2], **kw)
        pairs.append((ada.PrimBox(name, p1, p2).solid_occ(), sp))
    return pairs


def _box_faces(p1, p2):
    return active_backend().faces(ada.PrimBox("b", p1, p2).solid_occ())


def test_cellgraph_from_faces_two_abutting_boxes():
    # Two unit cells sharing the x=1 wall.
    cg = CellGraph.from_faces(_box_faces((0, 0, 0), (1, 1, 1)) + _box_faces((1, 0, 0), (2, 1, 1)))
    assert len(cg.cells) == 2

    # The shared x=1 face is internal (one per cell, connected); 10 external faces.
    ext = cg.get_external_faces()
    assert len(ext) == 10

    # Exactly one shared connection between the two cells: each cell has one
    # wall whose shared_face_connection is set.
    shared = [f for c in cg.cells for f in c.faces if f.shared_face_connection is not None]
    assert len(shared) == 2  # the two coincident faces, mutually linked
    a_face, b_face = shared
    assert a_face.get_adjacent_cell() is b_face.parent_cell
    assert b_face.get_adjacent_cell() is a_face.parent_cell

    # Wall classification: 2 cells * 4 walls = 8 total walls, minus the 2 shared
    # (x=1) walls -> 6 external; the shared pair -> 1 internal (priority pick).
    assert len(cg.get_external_walls()) == 6
    assert len(cg.get_internal_walls()) == 1


def test_cellgraph_from_prim_boxes_carries_metadata():
    boxes = [
        ada.PrimBox("RoomA", (0, 0, 0), (1, 1, 1), metadata={"NAME": "RoomA", "PRIORITY": 1}),
        ada.PrimBox("RoomB", (1, 0, 0), (2, 1, 1), metadata={"NAME": "RoomB", "PRIORITY": 2}),
    ]
    cg = CellGraph.from_prim_boxes(boxes)
    assert len(cg.cells) == 2
    names = sorted(c.name for c in cg.cells)
    assert names == ["RoomA", "RoomB"]
    # Metadata round-tripped onto the cells (IFC_-prefixed).
    a = cg.get_cell("RoomA")
    assert a.metadata.get("IFC_PRIORITY") == 1


def test_cellgraph_face_geometry():
    cg = CellGraph.from_faces(_box_faces((0, 0, 0), (1, 1, 1)))
    assert len(cg.cells) == 1
    cell = cg.cells[0]
    assert len(cell.faces) == 6
    # Each face is a 4-point planar quad with a unit-square area centroid.
    for f in cell.faces:
        pts = f.get_points()
        assert len(pts) == 4
        assert f.is_horizontal() or f.is_wall()
    # The cell centroid is the box centre.
    c = cell.get_centroid()
    assert (round(c.x, 3), round(c.y, 3), round(c.z, 3)) == (0.5, 0.5, 0.5)


def test_face_domain_accessors_from_topospace_metadata():
    # TopoSpace metadata drives the face's domain-reading API with no subclass.
    cg = CellGraph.from_cell_solids(
        _cell_solids(
            ("RoomA", (0, 0, 0), (1, 1, 1), {"STRUCTURE_NAME": "S1", "AREA": "Deck1", "PRIORITY": 1}),
            ("RoomB", (1, 0, 0), (2, 1, 1), {"STRUCTURE_NAME": "S1", "AREA": "Deck1", "PRIORITY": 5}),
        )
    )
    face = cg.get_external_walls()[0]
    assert face.get_stru_name() == "S1"
    assert face.get_area_name() == "Deck1"
    assert face.name.startswith("S1_Deck1_")
    # x-direction lies in the face plane (perpendicular to the normal).
    assert abs(float(face.get_xdir().dot(face.normal))) < 1e-9
    # No builder attached -> blueprint is simply None (no crash).
    assert face.get_blueprint() is None
    # PRIORITY (read off TopoSpace) decides internal-wall ownership: RoomB wins.
    walls = cg.get_internal_walls()
    assert len(walls) == 1
    assert walls[0].parent_cell.name == "RoomB"


def test_cantilevered_deck_excluded_from_walls():
    # A cantilevered deck contributes no walls (skipped in non-horizontal iter).
    cg = CellGraph.from_cell_solids(
        _cell_solids(
            ("Deck", (0, 0, 0), (2, 2, 0.2), {"FUNCTION": "cantilevered_deck"}),
        ),
        merge=False,
    )
    assert cg._is_cantilever(cg.cells[0]) is True
    assert cg.get_external_walls() == []
    # but its horizontal faces are still available
    assert len(cg.get_external_floors()) >= 1
