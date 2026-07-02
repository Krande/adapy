"""Tier-2 recovery of runaway planar faces.

A planar AdvancedFace whose boundary fails to trim the plane builds with the huge
COMPLEMENTARY region (a wire-winding / trim-side failure) and was dropped as a runaway
face — the dominant runaway-drop bucket on real CAD. The plane's true area is exactly its
boundary polygon, so the recovery rebuilds the face from the projected polygon (winding
fixed by signed area), which is exact for planes. These tests exercise the recovery
machinery on pure ada.geom fixtures (no STEP parse).
"""

from __future__ import annotations

import pytest

pytest.importorskip("ada.occ.geom.surfaces")  # OCC-backend internals; skip where OCC isn't installed

import ada.geom.curves as geo_cu
import ada.geom.surfaces as geo_su
from ada.geom.placement import Axis2Placement3D, Direction, Point
from ada.occ.geom.surfaces import (
    _face_area,
    _planar_boundary_polygon,
    _planar_face_from_polygon,
    make_surface_from_geom,
)


def _line_edge(start, end) -> geo_cu.OrientedEdge:
    ec = geo_cu.EdgeCurve(
        start=start,
        end=end,
        edge_geometry=geo_cu.Line(start, [e - s for s, e in zip(start, end)]),
        same_sense=True,
    )
    return geo_cu.OrientedEdge(start=start, end=end, edge_element=ec, orientation=True)


def _loop(points) -> geo_su.FaceBound:
    edges = [_line_edge(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]
    return geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)


def _plane_face(bounds) -> geo_su.AdvancedFace:
    plane = geo_su.Plane(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))
    return geo_su.AdvancedFace(bounds=bounds, face_surface=plane)


def test_polygon_true_area_outer_minus_hole():
    # 10x10 square (area 100) with a centred 2x2 hole (area 4) → net 96
    outer = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    hole = [(4, 4, 0), (4, 6, 0), (6, 6, 0), (6, 4, 0)]
    af = _plane_face([_loop(outer), _loop(hole)])
    pln = make_surface_from_geom(af.face_surface)
    pln_, loops, outer_idx, true_area = _planar_boundary_polygon(af, pln)
    assert len(loops) == 2
    assert abs(true_area - 96.0) < 1e-6


def test_recovered_face_area_matches_polygon_regardless_of_winding():
    # A CW outer loop is exactly the winding that makes MakeFace bound the complement;
    # the recovery must still produce the small 100-unit face.
    cw_outer = [(0, 0, 0), (0, 10, 0), (10, 10, 0), (10, 0, 0)]  # clockwise in XY
    af = _plane_face([_loop(cw_outer)])
    pln = make_surface_from_geom(af.face_surface)
    poly = _planar_boundary_polygon(af, pln)
    assert poly is not None
    face = _planar_face_from_polygon(poly[0], poly[1], poly[2])
    assert face is not None and not face.IsNull()
    assert abs(_face_area(face) - 100.0) < 1e-6
