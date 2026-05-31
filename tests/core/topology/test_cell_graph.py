"""Golden tests for the ada.topology cell graph (both backends)."""
from __future__ import annotations

import ada
from ada.topology import CellGraph, TopologyMetadata
from ada.cad import active_backend


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
