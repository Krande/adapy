"""Boundary-edge sampling must capture a curved edge's interior extent.

``_sample_edge_points`` previously returned only the two endpoints for a B-spline
(or ellipse) edge. When such an edge carries a face's axial extent but its two
endpoints happen to share that coordinate, the projected parameter range collapsed
and the face was wrongly dropped (~1500 curved faces on a large real CAD assembly).
Walking the edge's 3D curve recovers the swept set. These tests pin that sampling.
"""

from __future__ import annotations

import ada.geom.curves as geo_cu


def _bulging_bspline_edge() -> geo_cu.OrientedEdge:
    # Degree-2 single Bezier through (0,0,0) -> (5,0,10) -> (10,0,0): both endpoints
    # at z=0 but the curve bulges to z=5 at its midpoint. Endpoint-only sampling sees
    # zero z-extent; curve sampling sees the bulge.
    start = (0.0, 0.0, 0.0)
    end = (10.0, 0.0, 0.0)
    bs = geo_cu.BSplineCurveWithKnots(
        degree=2,
        control_points_list=[(0.0, 0.0, 0.0), (5.0, 0.0, 10.0), (10.0, 0.0, 0.0)],
        curve_form=geo_cu.BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[3, 3],
        knots=[0.0, 1.0],
        knot_spec=geo_cu.KnotType.UNSPECIFIED,
    )
    ec = geo_cu.EdgeCurve(start=start, end=end, edge_geometry=bs, same_sense=True)
    return geo_cu.OrientedEdge(start=start, end=end, edge_element=ec, orientation=True)


def test_bspline_edge_samples_interior_not_just_endpoints():
    from ada.occ.geom.surfaces import _sample_edge_points

    pts = _sample_edge_points(_bulging_bspline_edge())
    assert len(pts) > 2, "B-spline edge must be sampled along its curve, not just endpoints"
    zspan = max(p[2] for p in pts) - min(p[2] for p in pts)
    # endpoints are both z=0; the bulge peaks near z=5 — interior sampling must see it
    assert zspan > 3.0, f"interior bulge not captured (z-span {zspan:.3f})"


def test_line_edge_uses_endpoints_only():
    from ada.occ.geom.surfaces import _sample_edge_points

    start, end = (0.0, 0.0, 0.0), (1.0, 2.0, 3.0)
    ec = geo_cu.EdgeCurve(
        start=start, end=end, edge_geometry=geo_cu.Line(start, [1.0, 2.0, 3.0]), same_sense=True
    )
    oe = geo_cu.OrientedEdge(start=start, end=end, edge_element=ec, orientation=True)
    pts = _sample_edge_points(oe)
    # a straight edge is fully described by its endpoints — no need to walk it
    assert pts == [start, end]
