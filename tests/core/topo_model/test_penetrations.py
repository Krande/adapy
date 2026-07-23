"""Reinforced wall + penetration details in the topo_model demo.

The interior ServiceWater run crosses the reinforced internal wall at x=5;
the StandardPenetrations blueprint must emit exactly one pipe-sleeve detail
there and cut the through-hole in the wall plate. The deck-level systems
(CoolingWater/PowerFeed) run above the wall and must NOT penetrate."""

from __future__ import annotations

import pytest

import ada
from ada.topo_model import build_topo_model_with_systems
from ada.topo_model.penetration import find_face_crossings


@pytest.fixture(scope="module")
def demo() -> ada.Assembly:
    return build_topo_model_with_systems()


def test_reinforced_wall_built(demo):
    parts = {p.name for p in demo.get_all_parts_in_assembly()}
    assert "walls" in parts
    wall_plates = [p for p in demo.get_all_physical_objects(by_type=ada.Plate) if p.name.startswith("Wall_")]
    assert len(wall_plates) == 1
    stiffeners = [b for b in demo.get_all_physical_objects(by_type=ada.Beam) if "_stf_" in b.name]
    assert len(stiffeners) == 12  # 5 m wall span @ 0.4 m spacing
    # the stiffener profile stands perpendicular to the plate plane: local up
    # equals the wall normal (+X for the x=5 wall), not an in-plane direction
    for stf in stiffeners:
        assert tuple(round(float(v), 6) for v in stf.up) == (1.0, 0.0, 0.0)


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
