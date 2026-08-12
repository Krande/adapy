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


def _render_z_bounds(objs):
    import numpy as np

    scene = (ada.Assembly("s") / (ada.Part("p") / list(objs))).to_trimesh_scene()
    v = np.vstack([g.vertices for g in scene.geometry.values()])
    return float(v[:, 2].min()), float(v[:, 2].max())


def test_deck_beams_seated_flush_and_attached(demo_assembly):
    # The deck plate's TOP sits at the deck line (z=0 here). The girder top flange
    # must be flush with that (top at the deck line), and the stringer must hang
    # UNDER the plate — its top attached to the plate bottom (z - pl_thick), not
    # floating below. Seating is a per-section eccentricity from the profile's true
    # top offset, so it is correct for the centred IPE girder AND the top-referenced
    # HP stringer.
    import numpy as np

    pl_thick = 10e-3

    # z=0 deck plate: top at the deck line, bottom one thickness below.
    pl = next(
        p
        for p in demo_assembly.get_all_physical_objects(by_type=ada.Plate)
        if abs(float(np.mean([q[2] for q in p.poly.points3d]))) < 1e-6
    )
    pl_lo, pl_hi = _render_z_bounds([pl])
    assert pl_hi == pytest.approx(0.0, abs=2e-3)  # plate top at deck line
    assert pl_lo == pytest.approx(-pl_thick, abs=2e-3)

    # Girder flange top flush with the deck line (= plate top).
    g = next(
        b
        for b in demo_assembly.get_all_physical_objects(by_type=ada.Beam)
        if b.name.startswith("Girder") and abs(float(b.n1.p[2])) < 1e-6
    )
    _, g_top = _render_z_bounds([ada.Beam("g", g.n1.p, g.n2.p, "IPE200", e1=g.e1, e2=g.e2)])
    assert g_top == pytest.approx(pl_hi, abs=2e-3), f"girder top {g_top} not flush with plate top {pl_hi}"

    # Stringer top attached to the plate bottom (hangs under the deck).
    st = next(
        b
        for b in demo_assembly.get_all_physical_objects(by_type=ada.Beam)
        if "_str_" in b.name and abs(float(b.n1.p[2])) < 1e-6
    )
    _, st_top = _render_z_bounds([ada.Beam("s", st.n1.p, st.n2.p, st.section.name, e1=st.e1, e2=st.e2)])
    assert st_top == pytest.approx(pl_lo, abs=2e-3), f"stringer top {st_top} not attached to plate bottom {pl_lo}"


def test_stacked_cells_have_single_deck_per_plane():
    # Two stacked cells (or an enclosed upper cell) share ONE deck plane; it must be
    # plated exactly once. The internal-floor face and the enclosing cell's bottom
    # face are distinct objects for the same plane, so a guid-only dedup produced a
    # double plate — the plane-based dedup collapses them to one.
    import numpy as np

    from ada.topo_model.blueprint import SteelStru
    from ada.topology import TopologyBuilder

    boxes = [
        ada.PrimBox("Lower", (0, 0, 0), (5, 5, 3)),
        ada.PrimBox("Upper", (0, 0, 3), (5, 5, 6)),
    ]
    builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=SteelStru(enclosed_cells=["Upper"]))
    builder.build()
    a = builder.get_output_assembly("stacked")

    at_z3 = [
        p
        for p in a.get_all_physical_objects(by_type=ada.Plate)
        if abs(float(np.mean([q[2] for q in p.poly.points3d])) - 3.0) < 1e-6
    ]
    assert len(at_z3) == 1, f"shared z=3 deck plated {len(at_z3)}x: {[p.name for p in at_z3]}"


def test_detail_mode_trims_deck_to_girder_flanges():
    # DETAIL mode insets each deck plate's OUTLINE inboard by the girder top-flange
    # half-width (IPE200 w_top = 0.1 => 0.05 per edge), so the plate spans the clear
    # opening between the surrounding girders' flanges. It is a genuine outline inset
    # (not a boolean notch), so it renders in every path — asserted here directly on
    # the plate outline (poly.points3d), no tessellation needed. Simulation mode
    # (default) stays byte-identical: the plate reaches the cell edge, no booleans.
    import numpy as np

    from ada.topo_model.blueprint import SteelStru
    from ada.topo_model.build import make_space_boxes
    from ada.topology import TopologyBuilder

    def build(detail):
        builder = TopologyBuilder.from_prim_boxes(make_space_boxes(), blueprint=SteelStru(detail=detail))
        builder.build()
        return builder.get_output_assembly("m")

    def deck_xy_extent(a):
        # a z=0 deck plate — its base OUTLINE extent (no boolean, no stream needed).
        pl = next(
            p
            for p in a.get_all_physical_objects(by_type=ada.Plate)
            if abs(float(np.mean([q[2] for q in p.poly.points3d]))) < 1e-6
        )
        xy = np.asarray([tuple(q)[:2] for q in pl.poly.points3d], dtype=float)
        return float(xy[:, 0].min()), float(xy[:, 0].max()), float(xy[:, 1].min()), float(xy[:, 1].max()), pl

    setback = 0.1 / 2  # IPE200 top-flange half-width

    sx0, sx1, sy0, sy1, sim_pl = deck_xy_extent(build(False))
    dx0, dx1, dy0, dy1, det_pl = deck_xy_extent(build(True))

    # Simulation mode unchanged: the deck reaches the 5x5 cell edges, no trim cuts.
    assert (sx0, sx1, sy0, sy1) == pytest.approx((0.0, 5.0, 0.0, 5.0), abs=1e-6)
    assert not any("_trim_" in b.name for b in sim_pl.booleans)

    # Detail mode: the OUTLINE itself recedes inboard by exactly the flange
    # half-width on every edge — and via an inset, not a boolean cut.
    assert dx0 - sx0 == pytest.approx(setback, abs=1e-3)
    assert sx1 - dx1 == pytest.approx(setback, abs=1e-3)
    assert dy0 - sy0 == pytest.approx(setback, abs=1e-3)
    assert sy1 - dy1 == pytest.approx(setback, abs=1e-3)
    assert not any("_trim_" in b.name for b in det_pl.booleans)


def test_detail_mode_resolves_all_beam_clashes():
    # DETAIL mode severs every interpenetrating frame member (stringers into
    # girders, girders into columns, girder–girder corners) with a boolean cut, so
    # no two beams clash. Simulation mode leaves the members full (no clash cuts).
    import numpy as np

    from ada.topo_model.blueprint import SteelStru
    from ada.topo_model.build import make_space_boxes
    from ada.topology import TopologyBuilder

    def build(detail):
        # wide box girders make the overlaps large — the worst case to resolve
        bp = SteelStru(detail=detail, girder_sec="BG400x300x12x16")
        builder = TopologyBuilder.from_prim_boxes(make_space_boxes(), blueprint=bp)
        builder.build()
        return builder.get_output_assembly("m")

    def aabb(bm):
        (x1, y1, z1), (x2, y2, z2) = bm.bbox().minmax
        return np.array([x1, y1, z1]), np.array([x2, y2, z2])

    def clash_boxes(bm):
        return [
            (np.minimum(bl.primitive.p1, bl.primitive.p2), np.maximum(bl.primitive.p1, bl.primitive.p2))
            for bl in bm.booleans
            if "_clash_" in bl.primitive.name
        ]

    def covered(lo, hi, boxes):
        return any(np.all(blo <= lo + 1e-9) and np.all(bhi >= hi - 1e-9) for blo, bhi in boxes)

    # Simulation mode: members are left full — no clash cuts at all.
    sim = build(False)
    sim_beams = list(sim.get_all_physical_objects(by_type=ada.Beam))
    assert not any("_clash_" in bl.primitive.name for b in sim_beams for bl in b.booleans)

    # Detail mode: every overlapping beam pair has its overlap subtracted from one
    # of the two members, so nothing clashes.
    det = build(True)
    beams = list(det.get_all_physical_objects(by_type=ada.Beam))
    boxes = [aabb(b) for b in beams]
    cuts = [clash_boxes(b) for b in beams]
    assert sum(len(c) for c in cuts) > 0, "detail mode produced no clash cuts"

    unresolved = 0
    for i in range(len(beams)):
        for j in range(i + 1, len(beams)):
            lo = np.maximum(boxes[i][0], boxes[j][0])
            hi = np.minimum(boxes[i][1], boxes[j][1])
            d = hi - lo
            if (d > 1e-4).all() and float(np.prod(d)) > 1e-8:
                if not (covered(lo, hi, cuts[i]) or covered(lo, hi, cuts[j])):
                    unresolved += 1
    assert unresolved == 0, f"{unresolved} beam clashes left unresolved in detail mode"


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
