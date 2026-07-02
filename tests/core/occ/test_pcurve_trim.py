"""Shared-pcurve trim for split coedges (``_pcurve_trim_range``).

A single SAT pcurve is often shared by several coedges along one UV side of a
face — each coedge is a sub-segment, but the explicit pcurve carries the whole
side's trajectory. Without trimming each edge to its own sub-range, the wire
traces the side multiple times, self-intersects in UV, and BRepMesh grids only
part of the face (the hull-skin "missing triangles"). This pins that the trim
maps an edge's 3D endpoints back to the correct pcurve sub-range.
"""

from __future__ import annotations

import pytest

occ = pytest.importorskip("OCC.Core.Geom2d")
from OCC.Core.Geom import Geom_Plane  # noqa: E402
from OCC.Core.Geom2d import Geom2d_BSplineCurve  # noqa: E402
from OCC.Core.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Pnt2d  # noqa: E402
from OCC.Core.TColgp import TColgp_Array1OfPnt2d  # noqa: E402
from OCC.Core.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal  # noqa: E402

from ada.occ.geom.surfaces import _pcurve_trim_range  # noqa: E402


def _line_pcurve(p0, p1):
    """Degree-1 2D BSpline from ``p0`` to ``p1`` with native param range [0, 1]."""
    poles = TColgp_Array1OfPnt2d(1, 2)
    poles.SetValue(1, gp_Pnt2d(*p0))
    poles.SetValue(2, gp_Pnt2d(*p1))
    knots = TColStd_Array1OfReal(1, 2)
    knots.SetValue(1, 0.0)
    knots.SetValue(2, 1.0)
    mults = TColStd_Array1OfInteger(1, 2)
    mults.SetValue(1, 2)
    mults.SetValue(2, 2)
    return Geom2d_BSplineCurve(poles, knots, mults, 1)


def _xy_plane():
    return Geom_Plane(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)))


def test_trim_picks_subrange_for_split_edge():
    surf = _xy_plane()
    # pcurve runs the full v side u=0, v: 0 -> 10 (native param 0..1).
    c2d = _line_pcurve((0.0, 0.0), (0.0, 10.0))
    # This coedge only covers the upper half (3D y: 5 -> 10); its t-range
    # (length 5) is half the pcurve's mapped length (10) -> trim required.
    lo, hi = _pcurve_trim_range(c2d, surf, edge_start=(0.0, 5.0, 0.0), edge_end=(0.0, 10.0, 0.0))
    # native param s: 0..1 maps to v 0..10, so y in [5,10] -> s in [0.5, 1.0].
    assert lo == pytest.approx(0.5, abs=0.02)
    assert hi == pytest.approx(1.0, abs=0.02)


def test_no_trim_when_edge_uses_full_pcurve():
    surf = _xy_plane()
    c2d = _line_pcurve((0.0, 0.0), (0.0, 10.0))
    # Edge length (10) == pcurve mapped length -> not a split edge -> no trim.
    assert _pcurve_trim_range(c2d, surf, edge_start=(0.0, 0.0, 0.0), edge_end=(0.0, 10.0, 0.0)) is None


def test_no_trim_without_endpoints():
    surf = _xy_plane()
    c2d = _line_pcurve((0.0, 0.0), (0.0, 10.0))
    assert _pcurve_trim_range(c2d, surf, None, None) is None
