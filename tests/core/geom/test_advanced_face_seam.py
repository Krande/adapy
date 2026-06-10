"""Regression: a cylinder face whose boundary circles are split into arcs and whose
wire crosses the parametric seam kept the surface's NATURAL (infinite) bounds after
``BRepBuilderAPI_MakeFace(surface, wire)`` — BRepMesh then emitted vertices millions
of units out, so a single such face exploded the converted model's bounding box in
the viewer (seen on assembly-placed STEP files read via the streaming reader).
``make_face_from_geom`` must hand back a *bounded* face (ShapeFix_Face re-trim, then
parameter-extent rebuild, else skip)."""

import math

import pytest

from ada.geom.curves import Circle, EdgeCurve, EdgeLoop, Line, OrientedEdge
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point
from ada.geom.surfaces import AdvancedFace, CylindricalSurface, FaceBound

R = 10.0
H = 20.0


def _pt(angle_deg: float, z: float) -> Point:
    a = math.radians(angle_deg)
    return Point(R * math.cos(a), R * math.sin(a), z)


def _arc(p1: Point, p2: Point, z: float) -> OrientedEdge:
    circ = Circle(position=Axis2Placement3D(location=Point(0, 0, z)), radius=R)
    ec = EdgeCurve(start=p1, end=p2, edge_geometry=circ, same_sense=True)
    return OrientedEdge(start=p1, end=p2, edge_element=ec, orientation=True)


def _line(p1: Point, p2: Point) -> OrientedEdge:
    d = Direction(p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2])
    ec = EdgeCurve(start=p1, end=p2, edge_geometry=Line(pnt=p1, dir=d), same_sense=True)
    return OrientedEdge(start=p1, end=p2, edge_element=ec, orientation=True)


def _seam_crossing_face() -> AdvancedFace:
    a_t, b_t = _pt(45, H), _pt(200, H)
    a_b, b_b = _pt(45, 0.0), _pt(200, 0.0)
    edges = [
        _arc(a_t, b_t, H),  # top arc 45 deg -> 200 deg (CCW, away from the seam)
        _line(b_t, b_b),
        _arc(b_b, a_b, 0.0),  # bottom arc 200 deg -> 45 deg CCW: crosses the seam at 0/360
        _line(a_b, a_t),
    ]
    bound = FaceBound(bound=EdgeLoop(edge_list=edges), orientation=True)
    surf = CylindricalSurface(position=Axis2Placement3D(location=Point(0, 0, 0)), radius=R)
    return AdvancedFace(bounds=[bound], face_surface=surf, same_sense=True)


def test_seam_crossing_arc_wire_yields_bounded_face():
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh

    from ada.occ.geom.curves import make_wire_from_face_bound
    from ada.occ.geom.surfaces import (
        _face_uv_unbounded,
        make_face_from_geom,
        make_surface_from_geom,
    )

    af = _seam_crossing_face()

    # Precondition: the naive wire trim leaves the face unbounded (the bug trigger).
    surf = make_surface_from_geom(af.face_surface)
    wire = make_wire_from_face_bound(af.bounds[0])
    naive = BRepBuilderAPI_MakeFace(surf, wire).Face()
    if not _face_uv_unbounded(naive):
        pytest.skip("OCC trims this wire on its own; the regression precondition is gone")

    face = make_face_from_geom(af)
    assert not _face_uv_unbounded(face)

    # The meshed face must stay inside the cylinder's true extent.
    BRepMesh_IncrementalMesh(face, 0.5, False, 0.5, True)
    box = Bnd_Box()
    brepbndlib.Add(face, box, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    pad = 1.0
    assert -R - pad <= xmin and xmax <= R + pad
    assert -R - pad <= ymin and ymax <= R + pad
    assert -pad <= zmin and zmax <= H + pad


def test_runaway_triangles_dropped_from_tessellation():
    """The tessellation-level safety net: a triangle soup with vertices far outside
    the shape's edge hull (the signature of a face whose trim failed only after
    sewing rebuilt its pcurves) must lose exactly those triangles."""
    import numpy as np
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

    from ada.occ.tessellating import _drop_runaway_triangles

    shape = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()
    good = [0, 0, 0, 1, 0, 0, 0, 1, 0]
    bad = [0, 0, 0, 1e9, 0, 0, 0, 1e9, 0]  # vertices ~1e8x the edge hull
    verts = np.array(good + bad, dtype="float32")
    nrms = np.array([0, 0, 1] * 6, dtype="float32")

    fv, fn = _drop_runaway_triangles(shape, verts, nrms)
    assert fv.tolist() == good
    assert fn.size == 9

    # An all-sane soup passes through unchanged.
    fv2, _ = _drop_runaway_triangles(shape, np.array(good, dtype="float32"), None)
    assert fv2.tolist() == good
