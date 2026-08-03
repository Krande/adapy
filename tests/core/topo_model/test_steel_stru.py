"""The SteelStru demo blueprint: 2x1 cells -> reinforced floors, girders, columns.

Counts pinned from the verified first run: 2 cells sharing one wall give
4 external floor faces (2 per elevation), 14 deduped girder edges (7 x 2
elevations), 6 deduped column edges (4 corners + 2 on the shared wall) and
12 stringers per 5x5 floor face.
"""

from __future__ import annotations

import pytest

import ada
from ada.topo_model import build_topo_model


@pytest.fixture(scope="module")
def demo_assembly() -> ada.Assembly:
    return build_topo_model()


def test_steel_stru_counts(demo_assembly):
    plates = list(demo_assembly.get_all_physical_objects(by_type=ada.Plate))
    assert len(plates) == 4

    beams_by_sec: dict[str, int] = {}
    for bm in demo_assembly.get_all_physical_objects(by_type=ada.Beam):
        beams_by_sec[bm.section.name] = beams_by_sec.get(bm.section.name, 0) + 1

    assert beams_by_sec == {"HEB200": 6, "IPE200": 14, "HP140x8": 48}


def test_steel_stru_nests_per_room_and_frame(demo_assembly):
    # The output nests per cell: a "Room_<cell>" part per space holds its decks,
    # and shared steel goes under a single "Frame" (Girders + Columns).
    part_names = {p.name for p in demo_assembly.get_all_parts_in_assembly()}
    assert {"Frame", "Girders", "Columns"} <= part_names
    assert any(n.startswith("Room_") for n in part_names)


@pytest.mark.parametrize(
    "inward, want_side",
    [((1, 0, 0), "positive"), ((-1, 0, 0), "negative")],
)
def test_reinforced_wall_stiffeners_face_inward(inward, want_side):
    # A wall stiffener's web must stand INTO the room (toward the inward vector),
    # not out of it. HP-type profiles grow their web on one side of the beam
    # centreline, so the fix is a sign on the profile up-vector: verify the
    # stiffener material lands on the room-interior side of the plate.
    import numpy as np

    from ada.topo_model.blueprint import _build_reinforced_wall

    # a vertical wall in the Y-Z plane at x=0
    pts = [ada.Point(0, 0, 0), ada.Point(0, 5, 0), ada.Point(0, 5, 3), ada.Point(0, 0, 3)]
    wall = _build_reinforced_wall("W", pts, 8e-3, "HP140x8", 0.5, inward=inward)
    stf = next(iter(wall.get_all_physical_objects(by_type=ada.Beam)))
    scene = (ada.Assembly("t") / (ada.Part("p") / stf)).to_trimesh_scene()
    v = np.vstack([g.vertices for g in scene.geometry.values()])
    xmin, xmax = float(v[:, 0].min()), float(v[:, 0].max())
    if want_side == "positive":
        assert xmax > 1e-4 and xmin >= -1e-6  # material into +X room
    else:
        assert xmin < -1e-4 and xmax <= 1e-6  # material into -X room


def test_shared_floor_between_stacked_cells_is_built():
    # Two open (non-enclosed) cells stacked vertically share a horizontal face at
    # z=3 — the deck of the lower room / floor of the upper. It is an *internal*
    # floor, so the old build (external floors only) left it missing. Assert a
    # reinforced deck plate is built at the shared elevation.
    import numpy as np

    from ada.topo_model.blueprint import SteelStru
    from ada.topology import TopologyBuilder

    boxes = [
        ada.PrimBox("Lower", (0, 0, 0), (5, 5, 3)),
        ada.PrimBox("Upper", (0, 0, 3), (5, 5, 6)),
    ]
    builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=SteelStru())
    builder.build()
    a = builder.get_output_assembly("stacked")

    # A plate whose outline sits on the z=3 shared plane must exist.
    def plate_z(pl):
        return float(np.mean([p[2] for p in pl.poly.points3d]))

    zlevels = {round(plate_z(pl), 3) for pl in a.get_all_physical_objects(by_type=ada.Plate)}
    assert 3.0 in zlevels, f"shared deck at z=3 missing; plate elevations = {sorted(zlevels)}"
