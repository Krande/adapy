"""Reinforced wall + penetration details in the topo_model demo.

The interior ServiceWater run crosses the reinforced internal wall at x=5;
the StandardPenetrations blueprint must emit exactly one pipe-sleeve detail
there and cut the through-hole in the wall plate. The deck-level systems
(CoolingWater/PowerFeed) run above the wall and must NOT penetrate."""

from __future__ import annotations

import pytest

import ada
from ada.topo_model import build_topo_model_with_systems
from ada.topo_model.penetration import (
    Penetration,
    find_face_crossings,
    standard_penetration_modeller,
)


@pytest.fixture(scope="module")
def demo() -> ada.Assembly:
    return build_topo_model_with_systems()


def test_reinforced_wall_built(demo):
    # the reinforced internal wall is nested under its cell's room part
    parts = {p.name for p in demo.get_all_parts_in_assembly()}
    assert any(n.startswith("Room_") for n in parts)
    wall_plates = [p for p in demo.get_all_physical_objects(by_type=ada.Plate) if p.name.startswith("Wall_")]
    assert len(wall_plates) == 1
    stiffeners = [b for b in demo.get_all_physical_objects(by_type=ada.Beam) if "_stf_" in b.name]
    assert len(stiffeners) == 12  # 5 m wall span @ 0.4 m spacing
    # the stiffener profile stands perpendicular to the x=5 wall plate (local up
    # along X) and faces inward toward the owning room (sign follows the cell
    # centroid, so either +X or -X)
    for stf in stiffeners:
        up = tuple(round(float(v), 6) for v in stf.up)
        assert abs(up[0]) == 1.0 and up[1] == 0.0 and up[2] == 0.0


def test_service_run_penetrates_the_wall(demo):
    parts = {p.name for p in demo.get_all_parts_in_assembly()}
    assert "Penetrations" in parts

    sleeves = [s for s in demo.get_all_physical_objects() if s.name.endswith("_sleeve")]
    assert [s.name for s in sleeves] == ["ServiceWater_pen_00_sleeve"]
    assert isinstance(sleeves[0], ada.PrimCyl)

    # the through-hole is cut in the wall plate
    (wall_pl,) = [p for p in demo.get_all_physical_objects(by_type=ada.Plate) if p.name.startswith("Wall_")]
    assert len(wall_pl.booleans) == 1

    # the routed path genuinely crosses the wall plane at x=5
    service = next(s for s in demo.systems if s.name == "ServiceWater")
    xs = [float(p[0]) for p in service.routed_path]
    assert any(a <= 5.0 <= b or b <= 5.0 <= a for a, b in zip(xs, xs[1:]))


def test_deck_systems_do_not_penetrate(demo):
    pen_names = {s.name for s in demo.get_all_physical_objects() if "_pen_" in s.name}
    assert not any(n.startswith(("CoolingWater", "PowerFeed")) for n in pen_names)


def test_find_face_crossings_direct(demo):
    # a synthetic system-like object crossing the wall plane must be detected
    from ada.topo_model import make_space_boxes
    from ada.topology import TopologyBuilder

    builder = TopologyBuilder.from_prim_boxes(make_space_boxes())
    walls = builder.cell_graph.get_internal_walls()
    assert len(walls) == 1

    class _FakeSystem:
        name = "fake"
        routed_path = [ada.Point(2, 2.5, 1.0), ada.Point(8, 2.5, 1.0)]

    crossings = find_face_crossings(_FakeSystem(), walls)
    assert len(crossings) == 1
    assert abs(float(crossings[0].point[0]) - 5.0) < 1e-6

    class _FakeAbove:
        name = "fake2"
        routed_path = [ada.Point(2, 2.5, 4.0), ada.Point(8, 2.5, 4.0)]  # above the wall

    assert find_face_crossings(_FakeAbove(), walls) == []


def test_penetration_members_include_built_external_walls():
    # Both built INTERNAL and built EXTERNAL walls are penetrable (a riser through
    # the built outer skin gets a cutout like an interior crossing); unbuilt walls
    # and cross-list duplicates are excluded.
    from ada.topo_model.compile import _penetration_members

    class _W:
        def __init__(self, name, built):
            self.name = name
            self.associated_part = object() if built else None

    iw_built, iw_unbuilt = _W("int_built", True), _W("int_unbuilt", False)
    ew_built, ew_unbuilt = _W("ext_built", True), _W("ext_unbuilt", False)

    class _CG:
        def get_internal_walls(self):
            return [iw_built, iw_unbuilt]

        def get_external_walls(self):
            return [ew_built, ew_unbuilt, iw_built]  # iw_built also listed here

    members = _penetration_members(_CG())
    assert iw_built in members and ew_built in members  # built internal + external
    assert iw_unbuilt not in members and ew_unbuilt not in members  # unbuilt excluded
    assert members.count(iw_built) == 1  # deduped across the two lists
    assert _penetration_members(None) == []


# --- rectangular tray/duct cutout vs round pipe sleeve ---------------------


class _FakeFace:
    def __init__(self, part):
        self.associated_part = part


def _wall_plate():
    # a vertical wall plate in the Y-Z plane at X=5 (normal +X)
    pts = [(5, 0, 0), (5, 4, 0), (5, 4, 3), (5, 0, 3)]
    pl = ada.Plate.from_3d_points("wall_pl", pts, 8e-3)
    return ada.Part("Wall") / pl, pl


def _model_crossing(system, name, clearance=0.02):
    part, pl = _wall_plate()
    pen = Penetration(system, ada.Point(5, 2, 1.5), ada.Direction(1, 0, 0), _FakeFace(part))
    detail = standard_penetration_modeller(pen, name, tray_duct_clearance=clearance)
    return detail, pl


def test_cable_tray_cut_is_rectangular_section_plus_tolerance():
    from ada.api.systems import CableSystem

    tray = CableSystem("Trays", tray_width=0.3, tray_height=0.1)
    detail, pl = _model_crossing(tray, "Cable_pen")

    hole = pl.booleans[-1].primitive
    assert isinstance(hole, ada.PrimBox)  # rectangular, not round
    lo, hi = hole.p1, hole.p2
    # width along the lateral (Y) axis, height along the vertical (Z) axis,
    # each = section + tolerance on both sides
    assert round(float(hi[1] - lo[1]), 4) == round(0.3 + 2 * 0.02, 4)
    assert round(float(hi[2] - lo[2]), 4) == round(0.1 + 2 * 0.02, 4)
    # a tray is wider than tall
    assert (hi[1] - lo[1]) > (hi[2] - lo[2])


def test_duct_cut_is_rectangular_section_plus_tolerance():
    from ada.api.systems import DuctSystem

    duct = DuctSystem("HVAC", duct_width=0.4, duct_height=0.3)
    detail, pl = _model_crossing(duct, "Duct_pen")

    hole = pl.booleans[-1].primitive
    assert isinstance(hole, ada.PrimBox)
    lo, hi = hole.p1, hole.p2
    assert round(float(hi[1] - lo[1]), 4) == round(0.4 + 2 * 0.02, 4)
    assert round(float(hi[2] - lo[2]), 4) == round(0.3 + 2 * 0.02, 4)


def test_tray_duct_tolerance_is_overridable():
    from ada.api.systems import DuctSystem

    duct = DuctSystem("HVAC", duct_width=0.4, duct_height=0.3)
    _, pl = _model_crossing(duct, "Duct_pen", clearance=0.05)
    hole = pl.booleans[-1].primitive
    assert round(float(hole.p2[1] - hole.p1[1]), 4) == round(0.4 + 2 * 0.05, 4)


def test_pipe_cut_stays_round():
    from ada.api.systems import PipingSystem

    pipe = PipingSystem("Water", pipe_radius=0.05)
    detail, pl = _model_crossing(pipe, "Pipe_pen")
    hole = pl.booleans[-1].primitive
    assert isinstance(hole, ada.PrimCyl)  # pipes keep the round sleeve
    (sleeve,) = detail.get_all_physical_objects()
    assert isinstance(sleeve, ada.PrimCyl)
