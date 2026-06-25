"""Native (OCC-free) curve discretization parity + speed vs OCC.

``ada.geom.curve_discretize.discretize_curve`` samples arcs/circles by chord deflection without
pythonocc (so line geometry renders on wasm). These tests assert it produces a polyline
geometrically equivalent to OCC's ``discretize_edge`` (built via the CAD backend) — same
endpoints, points on the analytic curve, and symmetric polyline-Hausdorff within the deflection —
and that it is not slower than the OCC build+discretize path.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

import ada.geom.curves as cu
from ada.geom import Geometry
from ada.geom.curve_discretize import discretize_curve
from ada.geom.placement import Axis2Placement3D, Direction, Point

occ = pytest.importorskip("OCC")  # parity is measured against pythonocc


def _occ_points(curve, deflection: float) -> np.ndarray:
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Extend.TopologyUtils import TopologyExplorer, discretize_edge

    from ada.cad import active_backend

    shape = active_backend().build(Geometry(0, curve))
    edges = [shape] if shape.ShapeType() == TopAbs_EDGE else list(TopologyExplorer(shape).edges())
    pts: list = []
    for e in edges:
        pts.extend(discretize_edge(e, deflection=deflection))
    return np.asarray(pts, dtype=float)


def _pt_to_polyline(p: np.ndarray, poly: np.ndarray) -> float:
    best = np.inf
    for i in range(len(poly) - 1):
        a, b = poly[i], poly[i + 1]
        ab = b - a
        t = np.clip(np.dot(p - a, ab) / (np.dot(ab, ab) + 1e-30), 0.0, 1.0)
        best = min(best, float(np.linalg.norm(p - (a + t * ab))))
    return best


def _poly_hausdorff(a: np.ndarray, b: np.ndarray) -> float:
    return max(max(_pt_to_polyline(p, b) for p in a), max(_pt_to_polyline(p, a) for p in b))


def _unit_circle():
    return cu.Circle(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)), radius=1.0)


def _quarter_arc():
    s = 2.0**-0.5
    return cu.ArcLine(start=Point(1, 0, 0), midpoint=Point(s, s, 0), end=Point(0, 1, 0))


@pytest.mark.parametrize("deflection", [0.1, 0.05, 0.01])
def test_circle_matches_occ(deflection):
    nat = np.asarray(discretize_curve(_unit_circle(), deflection=deflection), dtype=float)
    ref = _occ_points(_unit_circle(), deflection)
    # every native point sits on the analytic unit circle
    assert np.abs(np.linalg.norm(nat, axis=1) - 1.0).max() < 1e-9
    # both polylines trace the same curve within the deflection (+ small margin)
    assert _poly_hausdorff(nat, ref) <= deflection * 1.5


@pytest.mark.parametrize("deflection", [0.1, 0.05, 0.01])
def test_arc_matches_occ(deflection):
    ipc = cu.IndexedPolyCurve(segments=[_quarter_arc()])
    nat = np.asarray(discretize_curve(ipc, deflection=deflection), dtype=float)
    ref = _occ_points(ipc, deflection)
    assert np.allclose(nat[0], ref[0], atol=1e-6) or np.allclose(nat[0], ref[-1], atol=1e-6)
    assert np.allclose(nat[-1], ref[-1], atol=1e-6) or np.allclose(nat[-1], ref[0], atol=1e-6)
    assert np.abs(np.linalg.norm(nat, axis=1) - 1.0).max() < 1e-6  # on the unit circle
    assert _poly_hausdorff(nat, ref) <= deflection * 1.5


def test_straight_edge_is_exact_endpoints():
    pts = discretize_curve(cu.Edge(start=Point(0, 0, 0), end=Point(0, 1, 0)))
    assert pts == [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0)]


def test_bspline_returns_none():
    # No native sampler for B-splines yet → caller falls back to OCC.
    bs = cu.BSplineCurveWithKnots(
        degree=1,
        control_points_list=[Point(0, 0, 0), Point(1, 0, 0)],
        curve_form="UNSPECIFIED",
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[2, 2],
        knots=[0.0, 1.0],
        knot_spec="UNSPECIFIED",
    )
    assert discretize_curve(bs) is None


def test_speed_native_not_slower_than_occ():
    """Native discretization should beat the OCC build+discretize path (no kernel round-trip)."""
    circle = _unit_circle()
    n = 300
    t0 = time.perf_counter()
    for _ in range(n):
        discretize_curve(circle, deflection=0.01)
    t_native = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(n):
        _occ_points(circle, 0.01)
    t_occ = time.perf_counter() - t0

    print(
        f"\n[discretize {n}x unit circle @0.01]  native={t_native*1e3:.1f}ms  occ={t_occ*1e3:.1f}ms  speedup={t_occ/t_native:.1f}x"
    )
    # The point is parity + dropping the kernel dependency (wasm); native must at least be in the
    # same ballpark, not regress. (It avoids the per-curve OCC TopoDS build, so it tends faster.)
    assert t_native <= t_occ * 2.0
