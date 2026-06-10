"""Regression: the param-extent face rebuild (the repair path for faces whose wire
fails to trim an infinite cylinder/cone) must not OVER-COVER. Pre-fix,
``_sample_edge_points`` always sampled circular arcs in the circle's positive
parametric direction, so the complement arc could be sampled, the u-extent snapped
to the full period, and a small wedge face rebuilt as a spurious full revolution
band — large converted models showed phantom cones/cylinders obfuscating the view.

The arc's occupied point-set is determined by the EdgeCurve alone (start -> end,
positive direction iff ``same_sense``); ``OrientedEdge.orientation`` only reverses
traversal and is reader-convention-dependent, so sampling must ignore it."""

import math

import pytest

from ada.geom.curves import Circle, EdgeCurve, OrientedEdge
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

R = 10.0


def _pt(angle_deg: float, z: float = 0.0) -> Point:
    a = math.radians(angle_deg)
    return Point(R * math.cos(a), R * math.sin(a), z)


def _arc_edge(p_start: Point, p_end: Point, *, same_sense: bool, orientation: bool) -> OrientedEdge:
    """An arc on the canonical z-axis circle. ``p_start``/``p_end`` are the
    EdgeCurve's OWN endpoints; the OrientedEdge endpoints follow ``orientation``
    the way the STEP stream reader builds them (swapped when reversed)."""
    circ = Circle(position=Axis2Placement3D(location=Point(0, 0, 0)), radius=R)
    ec = EdgeCurve(start=p_start, end=p_end, edge_geometry=circ, same_sense=same_sense)
    s, e = (p_start, p_end) if orientation else (p_end, p_start)
    return OrientedEdge(start=s, end=e, edge_element=ec, orientation=orientation)


def _sampled_angles(oe) -> list[float]:
    pytest.importorskip("OCC", reason="samples via the pythonocc face-rebuild helpers")
    from ada.occ.geom.surfaces import _sample_edge_points

    out = []
    for p in _sample_edge_points(oe):
        out.append(math.degrees(math.atan2(p[1], p[0])) % 360.0)
    return out


def _assert_within_first_quadrant(angles: list[float]) -> None:
    # The physical arc is 0..90 deg; tolerate endpoint rounding.
    for a in angles:
        ok = a <= 90.5 or a >= 359.5
        assert ok, f"sample at {a:.1f} deg lies on the complement arc: {angles}"


def test_arc_sampling_follows_edge_curve_sense_not_orientation():
    """The same physical 90-degree arc (first quadrant), authored three ways, must
    sample the same angular window — never the 270-degree complement."""
    a, b = _pt(0), _pt(90)

    # 1) Plain forward: EdgeCurve 0->90 with same_sense, traversed forward.
    _assert_within_first_quadrant(_sampled_angles(_arc_edge(a, b, same_sense=True, orientation=True)))

    # 2) STEP-reader form: same EdgeCurve, OrientedEdge reversed (endpoints
    #    pre-swapped by the reader). Occupied arc unchanged.
    _assert_within_first_quadrant(_sampled_angles(_arc_edge(a, b, same_sense=True, orientation=False)))

    # 3) Reversed-sense authoring: EdgeCurve 90->0 with same_sense=False — the
    #    occupied arc still goes 90 -> 0 the NEGATIVE way through the first
    #    quadrant (pre-fix this sampled 90 -> 360 (the complement)).
    _assert_within_first_quadrant(_sampled_angles(_arc_edge(b, a, same_sense=False, orientation=True)))


def test_full_circle_still_samples_whole_period():
    pytest.importorskip("OCC", reason="samples via the pythonocc face-rebuild helpers")
    angles = _sampled_angles(_arc_edge(_pt(0), _pt(0), same_sense=True, orientation=True))
    # Samples must cover all four quadrants.
    quadrants = {int(a // 90) % 4 for a in angles}
    assert quadrants == {0, 1, 2, 3}, angles


# --------------------------------------------------------------------------- #
# Param-extent rebuild: closure faces survive, holes are re-added, the area
# gate rejects rebuilds that over-cover their own boundary evidence.
# --------------------------------------------------------------------------- #
def _cyl_pt(angle_deg: float, z: float, r: float = R) -> Point:
    a = math.radians(angle_deg)
    return Point(r * math.cos(a), r * math.sin(a), z)


def _full_circle(z: float) -> OrientedEdge:
    p = _cyl_pt(0, z)
    circ = Circle(position=Axis2Placement3D(location=Point(0, 0, z)), radius=R)
    ec = EdgeCurve(start=p, end=p, edge_geometry=circ, same_sense=True)
    return OrientedEdge(start=p, end=p, edge_element=ec, orientation=True)


def _arc(a_deg: float, b_deg: float, z: float, *, reverse_traversal: bool = False) -> OrientedEdge:
    """Arc occupying a_deg..b_deg (positive direction). ``reverse_traversal``
    authors it the way STEP writes a wire walking the arc backwards: the SAME
    EdgeCurve wrapped in ORIENTED_EDGE(.F.) with swapped traversal endpoints."""
    p1, p2 = _cyl_pt(a_deg, z), _cyl_pt(b_deg, z)
    circ = Circle(position=Axis2Placement3D(location=Point(0, 0, z)), radius=R)
    ec = EdgeCurve(start=p1, end=p2, edge_geometry=circ, same_sense=True)
    if reverse_traversal:
        return OrientedEdge(start=p2, end=p1, edge_element=ec, orientation=False)
    return OrientedEdge(start=p1, end=p2, edge_element=ec, orientation=True)


def _vline(angle_deg: float, z1: float, z2: float) -> OrientedEdge:
    from ada.geom.curves import Line
    from ada.geom.direction import Direction

    p1, p2 = _cyl_pt(angle_deg, z1), _cyl_pt(angle_deg, z2)
    ec = EdgeCurve(
        start=p1, end=p2, edge_geometry=Line(pnt=p1, dir=Direction(0, 0, 1 if z2 > z1 else -1)), same_sense=True
    )
    return OrientedEdge(start=p1, end=p2, edge_element=ec, orientation=True)


def _cyl_surface() -> "object":
    from ada.geom.surfaces import CylindricalSurface

    return CylindricalSurface(position=Axis2Placement3D(location=Point(0, 0, 0)), radius=R)


def _make_advanced_face(bounds) -> "object":
    from ada.geom.surfaces import AdvancedFace

    return AdvancedFace(bounds=bounds, face_surface=_cyl_surface(), same_sense=True)


def _bound(edges, orientation=True):
    from ada.geom.curves import EdgeLoop
    from ada.geom.surfaces import FaceBound

    return FaceBound(bound=EdgeLoop(edge_list=edges), orientation=orientation)


def _meshed_area(face) -> float:
    pytest.importorskip("OCC")
    from ada.occ.geom.surfaces import _face_area

    return _face_area(face)


H = 20.0


def test_full_cylinder_survives_area_gate():
    """A genuinely closed cylinder band (two full-circle rim bounds) must rebuild
    to ~2*pi*r*h — the gate must not reject legitimate closure faces."""
    pytest.importorskip("OCC")
    from ada.occ.geom.surfaces import make_face_from_geom

    af = _make_advanced_face([_bound([_full_circle(0.0)]), _bound([_full_circle(H)])])
    face = make_face_from_geom(af)
    area = _meshed_area(face)
    expect = 2 * math.pi * R * H
    assert abs(area - expect) / expect < 0.1, f"area {area} vs expected {expect}"


def test_inner_hole_bound_is_readded():
    """A non-closure bound on a closed cylinder is a cutout: the rebuilt face's
    area must be ~2*pi*r*h minus the hole patch."""
    pytest.importorskip("OCC")
    from ada.occ.geom import surfaces as su

    hole = _bound(
        [
            _arc(30, 60, 8.0),
            _vline(60, 8.0, 12.0),
            _arc(30, 60, 12.0, reverse_traversal=True),
            _vline(30, 12.0, 8.0),
        ],
        orientation=False,
    )
    af = _make_advanced_face([_bound([_full_circle(0.0)]), _bound([_full_circle(H)]), hole])
    su.consume_param_rebuild_stats()  # reset
    face = su.make_face_from_geom(af)
    stats = su.consume_param_rebuild_stats()
    area = _meshed_area(face)
    full = 2 * math.pi * R * H
    hole_area = (math.radians(30) * R) * 4.0  # 30deg arc span * 4 height
    assert stats.get("inner_bound_readded", 0) == 1, stats
    assert abs(area - (full - hole_area)) / full < 0.1, f"area {area}, expected ~{full - hole_area}"


def test_area_gate_drops_forced_overcover(monkeypatch):
    """Force the u-extent to the full period on a small wedge: the rebuilt band
    over-covers the wedge's boundary evidence and must be rejected (None)."""
    pytest.importorskip("OCC")
    from ada.occ.geom import surfaces as su

    h = 2.0
    wedge = _bound(
        [
            _arc(0, 20, 0.0),
            _vline(20, 0.0, h),
            _arc(0, 20, h, reverse_traversal=True),
            _vline(0, h, 0.0),
        ]
    )
    af = _make_advanced_face([wedge])
    occ_surf = su.make_surface_from_geom(af.face_surface)

    def _force_full(vals, periodic, period):
        if periodic:
            return 0.0, period
        return min(vals), max(vals)

    monkeypatch.setattr(su, "_param_extent", _force_full)
    su.consume_param_rebuild_stats()
    face = su._make_face_from_param_extent(af, occ_surf)
    stats = su.consume_param_rebuild_stats()
    assert face is None
    assert stats.get("area_gate_dropped", 0) == 1, stats
