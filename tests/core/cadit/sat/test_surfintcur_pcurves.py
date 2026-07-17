"""surfintcur → SurfaceCurve: the curve-on-surface parsed whole (3D spline + the
per-surface 2D pcurves), so reference-form coedge pcurves (``±n $intcurve``) can be
reconstructed and a spline face's boundary keeps its UV image."""

from ada.cadit.sat.read.bsplinecurves import create_surface_curve_from_sat
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.geom import curves as gc

# Real records from a Genie hull export, abbreviated ids. Two variants:
# infinite surface ranges (I I I I) and finite ones (F <val> x4).
_SURFINTCUR_INFINITE = """-100 intcurve-curve $-1 -1 -1 $-1 forward { surfintcur full nubs 3 open 3
-0.99999999999999645 3 -0.49999999999999822 2 -0 3
-27.96285638848876 -34.558333333333344 11.800000000000006
-27.962856388488756 -34.558333333333344 11.966666666666672
-27.96285638848876 -34.558333333333344 12.133333333333338
-27.96285638848876 -34.558333333333344 12.46666666666667
-27.962856388488756 -34.558333333333344 12.633333333333336
-27.96285638848876 -34.558333333333344 12.800000000000002
9.9999999999999994e-12
plane -27.044021511305111 -34.558333333333337 6.8999999999999995 8.4345607342616348e-28 1 1.4244589322049544e-30 0 -1.4244589322049544e-30 1 reverse_v I I I I
spline forward { ref 516 } I I I I
nullbs
nubs 3 open 3
-0.99999999999999645 3 -0.49999999999999822 3 -0 3
0.89100208275113801 0
0.89100208275113801 -0.16666666666666807
0.89100208275113801 -0.3333333333333337
0.89100208275113801 -0.5
0.89100208275113801 -0.6666666666666663
0.89100208275113801 -0.83333333333333193
0.89100208275113801 -1
-1
-1
I I
0
0
0
-1
none F } I I #"""

_SURFINTCUR_FINITE = _SURFINTCUR_INFINITE.replace(
    "spline forward { ref 516 } I I I I",
    "spline forward { ref 516 } F 0.78539816339744828 F 1.1780972450961724 F -1.3000000000000023 F 0",
)


def _record(text: str) -> AcisRecord:
    return AcisRecord.from_string(" ".join(text.split()))


def _check(sc: gc.SurfaceCurve):
    assert isinstance(sc, gc.SurfaceCurve)
    # 3D curve: degree 3, clamped end mults -> 6 control points
    assert isinstance(sc.curve_3d, gc.BSplineCurveWithKnots)
    assert sc.curve_3d.degree == 3
    assert len(sc.curve_3d.control_points_list) == 6
    # pcurve 1 (the plane) is None; pcurve 2 (the spline surface) is the 2D nubs
    assert len(sc.associated_pcurves) == 2
    assert sc.associated_pcurves[0] is None
    pc2 = sc.associated_pcurves[1]
    assert isinstance(pc2, gc.Pcurve2dBSpline)
    assert pc2.degree == 3
    assert len(pc2.control_points_2d) == 7


def test_surfintcur_with_infinite_surface_ranges():
    _check(create_surface_curve_from_sat(_record(_SURFINTCUR_INFINITE)))


def test_surfintcur_with_finite_surface_ranges():
    """Bounded surface ranges are `F <value>` pairs (two tokens each), not `I`."""
    _check(create_surface_curve_from_sat(_record(_SURFINTCUR_FINITE)))


def test_non_surfintcur_returns_none():
    rec = _record("-1 intcurve-curve $-1 -1 -1 $-1 forward { exactcur full nubs 1 open 2 0 2 1 2 0 0 0 1 0 0 0 } I I #")
    assert create_surface_curve_from_sat(rec) is None
