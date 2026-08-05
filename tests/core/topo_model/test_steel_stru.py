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


def test_deck_beams_seated_flush_with_deck(demo_assembly):
    # Girders/stringers must be dropped so the TOP of their section sits flush with
    # the deck plate top (top-of-steel = deck), not straddling the deck line with
    # the flange sticking up. The seating is a positive z-eccentricity of
    # h/2 - pl_thick (profile moves opposite to e).
    pl_thick = 10e-3
    girder = next(b for b in demo_assembly.get_all_physical_objects(by_type=ada.Beam) if b.name.startswith("Girder"))
    assert girder.e1 is not None and girder.e2 is not None
    assert float(girder.e1[2]) == pytest.approx(girder.section.h / 2 - pl_thick)

    stringer = next(b for b in demo_assembly.get_all_physical_objects(by_type=ada.Beam) if "_str_" in b.name)
    assert float(stringer.e1[2]) == pytest.approx(stringer.section.h / 2 - pl_thick)

    # Render one z=0 girder and confirm its section top lands at the deck top, not
    # h/2 above it.
    g = next(
        b
        for b in demo_assembly.get_all_physical_objects(by_type=ada.Beam)
        if b.name.startswith("Girder") and abs(float(b.n1.p[2])) < 1e-6
    )
    solo = ada.Assembly("s") / (ada.Part("p") / [ada.Beam("g", g.n1.p, g.n2.p, "IPE200", e1=g.e1, e2=g.e2)])
    top_z = float(solo.to_trimesh_scene().bounds[1][2])
    assert top_z == pytest.approx(pl_thick, abs=2e-3), f"girder top {top_z} not flush with deck top {pl_thick}"


def test_door_opening_cuts_crossing_stiffeners():
    from ada.topo_model.blueprint import SteelStru
    from ada.topo_model.compile import _apply_openings
    from ada.topology import TopologyBuilder
    from ada.topology.entities import TopoSpace

    # One enclosed cell so its walls carry vertical stiffeners; a door in the x=5
    # wall must cut the studs that cross its width.
    spaces = [TopoSpace(NAME="Cell1", X=0, Y=0, Z=0, DX=5, DY=5, DZ=3)]
    boxes = [ada.PrimBox("Cell1", (0, 0, 0), (5, 5, 3))]
    bp = SteelStru(enclosed_cells=["Cell1"])
    builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=bp)
    builder.build()
    a = builder.get_output_assembly("m")

    door = {
        "NAME": "DOOR",
        "SUBTYPE": "door",
        "USE_GLOBAL_COORDS": True,
        "INCLUDE": True,
        "X": 4.8,
        "Y": 2.0,
        "Z": 0.0,
        "DX": 0.3,
        "DY": 1.0,
        "DZ": 2.1,
    }
    _apply_openings(bp, a, spaces, [door])

    # Vertical wall studs whose axis crosses the doorway (x≈5, y in [2,3]) must be
    # cut (carry a boolean); studs elsewhere must not.
    crossing = 0
    for b in a.get_all_physical_objects(by_type=ada.Beam):
        if "_stf_" not in b.name:
            continue
        import numpy as np

        p1 = np.asarray([float(c) for c in b.n1.p])
        p2 = np.asarray([float(c) for c in b.n2.p])
        lo, hi = np.minimum(p1, p2), np.maximum(p1, p2)
        if abs(lo[0] - 5.0) < 0.2 and lo[1] >= 1.9 and hi[1] <= 3.1 and hi[2] > 2.0:
            crossing += 1
            assert len(b.booleans) > 0, f"doorway stud {b.name} not cut"
    assert crossing > 0, "expected at least one wall stud crossing the doorway"
