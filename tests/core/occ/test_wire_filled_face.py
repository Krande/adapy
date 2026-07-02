"""``make_face_from_wire_filled`` (the WireFilledFace -> OCC plate fallback).

This fallback fits a smooth plate through a boundary wire with
``BRepOffsetAPI_MakeFilling`` when the original analytic surface is
unrecoverable. The plate solver (GeomPlate's curve-on-surface Newton
projection) is bounded with conservative parameters so a pathological
boundary can't grind for minutes and blow the per-solid stream timeout.
These tests pin the correctness side of that bound: a valid, positive-area,
boundary-spanning face still comes out — on both a planar and a non-planar
(saddle) wire.
"""

from __future__ import annotations

import math

import ada.geom.curves as geo_cu
import ada.geom.surfaces as geo_su


def _oriented_line_edge(start, end) -> geo_cu.OrientedEdge:
    ec = geo_cu.EdgeCurve(
        start=start,
        end=end,
        edge_geometry=geo_cu.Line(start, [e - s for s, e in zip(start, end)]),
        same_sense=True,
    )
    return geo_cu.OrientedEdge(start=start, end=end, edge_element=ec, orientation=True)


def _wire_filled_face(points) -> geo_su.WireFilledFace:
    edges = [_oriented_line_edge(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]
    loop = geo_cu.EdgeLoop(edge_list=edges)
    return geo_su.WireFilledFace(bounds=[geo_su.FaceBound(bound=loop, orientation=True)])


def _face_area(face) -> float:
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps

    props = GProp_GProps()
    brepgprop.SurfaceProperties(face, props)
    return abs(props.Mass())  # Mass is signed by face orientation


def _shape_diag(face) -> float:
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib

    box = Bnd_Box()
    brepbndlib.Add(face, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return math.dist((xmin, ymin, zmin), (xmax, ymax, zmax))


def test_wire_filled_planar_quad_builds_positive_area():
    from ada.occ.geom.surfaces import make_face_from_wire_filled

    pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    face = make_face_from_wire_filled(_wire_filled_face(pts))
    assert face is not None and not face.IsNull()
    # ~unit square; bounded MakeFilling must still cover the boundary.
    assert _face_area(face) > 0.5
    # the plate must not balloon past its boundary (the runaway-disk failure mode)
    assert _shape_diag(face) < 5.0


def test_wire_filled_nonplanar_saddle_builds_and_is_bounded():
    from ada.occ.geom.surfaces import make_face_from_wire_filled

    # A non-planar (saddle) quad: opposite corners lifted +/-z. This is where the
    # plate solver actually iterates, so it exercises the bounded GeomPlate path.
    pts = [(0.0, 0.0, 0.2), (1.0, 0.0, -0.2), (1.0, 1.0, 0.2), (0.0, 1.0, -0.2)]
    face = make_face_from_wire_filled(_wire_filled_face(pts))
    assert face is not None and not face.IsNull()
    assert _face_area(face) > 0.5
    assert math.isfinite(_shape_diag(face)) and _shape_diag(face) < 5.0
