"""Regression: a cylinder face whose boundary circles are split into arcs and whose
wire crosses the parametric seam kept the surface's NATURAL (infinite) bounds after
``BRepBuilderAPI_MakeFace(surface, wire)`` — BRepMesh then emitted vertices millions
of units out, so a single such face exploded the converted model's bounding box in
the viewer (seen on assembly-placed STEP files read via the streaming reader).

The end-to-end assertion goes through ``active_backend()`` so BOTH CAD backends are
held to the same contract: tessellating this face must stay inside the cylinder's
true extent. Only the bug-trigger precondition and the triangle-soup filter unit
test are pythonocc-specific."""

import math

import numpy as np
import pytest

from ada.geom.curves import Circle, EdgeCurve, EdgeLoop, Line, OrientedEdge
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point
from ada.geom.surfaces import (
    AdvancedFace,
    CylindricalSurface,
    FaceBound,
    OpenShell,
    ShellBasedSurfaceModel,
)

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
    from ada.cad import active_backend
    from ada.geom import Geometry

    af = _seam_crossing_face()

    # Bug-trigger precondition, pythonocc only: the naive wire trim leaves the face
    # unbounded. If a future OCC trims this on its own the regression is moot there;
    # the backend-agnostic assertion below still runs on adacpp.
    try:
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace

        from ada.occ.geom.curves import make_wire_from_face_bound
        from ada.occ.geom.surfaces import _face_uv_unbounded, make_surface_from_geom
    except ImportError:
        pass
    else:
        surf = make_surface_from_geom(af.face_surface)
        wire = make_wire_from_face_bound(af.bounds[0])
        naive = BRepBuilderAPI_MakeFace(surf, wire).Face()
        assert _face_uv_unbounded(naive), "OCC now trims this wire on its own — precondition gone, simplify the fix?"

    shell = ShellBasedSurfaceModel(sbsm_boundary=[OpenShell(cfs_faces=[af])])
    be = active_backend()
    handle = be.build(Geometry(id="seam-face", geometry=shell, color=None, transforms=None))
    mesh = be.tessellate(handle)
    pos = np.asarray(mesh.positions, dtype=float).reshape(-1, 3)
    assert pos.size, "tessellation produced no vertices"

    # The meshed face must stay inside the cylinder's true extent.
    pad = 1.0
    assert pos[:, 0].min() >= -R - pad and pos[:, 0].max() <= R + pad
    assert pos[:, 1].min() >= -R - pad and pos[:, 1].max() <= R + pad
    assert pos[:, 2].min() >= -pad and pos[:, 2].max() <= H + pad

    # Every meshed vertex must lie ON the cylinder wall (radius ~R). A collapsed/flat mesh —
    # the arc edges built as straight chords (the adacpp encoder dropping a trim-less Circle to
    # a chord) — dips well inside R. This is the assertion that catches the collapse on either
    # backend; the bounded-box check above passes even when the face degenerates to a flat plane.
    radii = np.hypot(pos[:, 0], pos[:, 1])
    assert radii.min() >= R - pad, f"vertices collapsed toward the axis (min radius {radii.min():.2f} << {R})"


def test_planar_face_with_polyloop_hole_builds_on_backend():
    """A flat AdvancedFace bounded by PolyLoop outer + PolyLoop hole (how the analytic
    flat faces + their cut-outs arrive) builds + tessellates on the active backend, with
    the hole a real void. Regression for the adacpp encoder dropping PolyLoop bounds to an
    empty edge list (build_advanced_face_planar wire-build failure)."""
    from ada.cad import active_backend
    from ada.geom import Geometry
    from ada.geom.curves import PolyLoop
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.points import Point
    from ada.geom.surfaces import (
        AdvancedFace,
        FaceBound,
        OpenShell,
        Plane,
        ShellBasedSurfaceModel,
    )

    outer = [(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)]
    hole = [(1, 1, 0), (1, 2, 0), (2, 2, 0), (2, 1, 0)]
    plane = Plane(position=Axis2Placement3D(location=Point(0, 0, 0), axis=Direction(0, 0, 1)))
    bounds = [
        FaceBound(bound=PolyLoop(polygon=[Point(*p) for p in outer]), orientation=True),
        FaceBound(bound=PolyLoop(polygon=[Point(*p) for p in hole]), orientation=True),
    ]
    face = AdvancedFace(bounds=bounds, face_surface=plane, same_sense=True)
    shell = ShellBasedSurfaceModel(sbsm_boundary=[OpenShell(cfs_faces=[face])])

    be = active_backend()
    mesh = be.tessellate(be.build(Geometry(id="hole", geometry=shell, color=None, transforms=None)))
    pos = np.asarray(mesh.positions, dtype=float).reshape(-1, 3)
    assert pos.size, "planar face-with-hole produced no vertices (wire build failed)"
    tri = next(getattr(mesh, a) for a in ("indices", "faces", "index") if getattr(mesh, a, None) is not None)
    idx = np.asarray(tri).reshape(-1, 3)
    cen = pos[idx].mean(axis=1)
    in_hole = ((cen[:, 0] > 1.0) & (cen[:, 0] < 2.0) & (cen[:, 1] > 1.0) & (cen[:, 1] < 2.0)).sum()
    assert in_hole == 0, f"{in_hole} triangles inside the hole — void not honoured"


def test_circle_param_recovers_arc_angles():
    """_circle_param recovers a point's angular parameter on a circle (from ref about axis) —
    the math the adacpp encoder uses to turn a trim-less Circle arc into a real trimmed circle
    instead of a chord."""
    from ada.cad import _circle_param

    loc, axis, ref = (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    assert abs(_circle_param((R, 0, 0), loc, axis, ref) - 0.0) < 1e-9
    assert abs(_circle_param((0, R, 0), loc, axis, ref) - math.pi / 2) < 1e-9
    assert abs(_circle_param((-R, 0, 0), loc, axis, ref) - math.pi) < 1e-9
    # ref rotation shifts the origin: with ref=+y, the +y point is param 0
    assert abs(_circle_param((0, R, 0), loc, axis, (0.0, 1.0, 0.0)) - 0.0) < 1e-9


def test_runaway_triangles_dropped_from_tessellation():
    """The tessellation-level safety net: a triangle soup with vertices far outside
    the shape's edge hull (the signature of a face whose trim failed only after
    sewing rebuilt its pcurves) must lose exactly those triangles."""
    pytest.importorskip("OCC", reason="unit test of the pythonocc tessellation filter internals")
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
