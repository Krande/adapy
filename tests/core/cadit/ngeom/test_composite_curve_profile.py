"""Composite-curve profile boundaries + face-based surface models serialize to NGEOM.

Regression for the trimmed-curve-parameters and surface-model audit OCC fallbacks:
  * an ArbitraryClosedProfileDef whose outer curve is an IfcCompositeCurve of trimmed conic / line
    segments (curve-parameters-in-degrees/radians), and
  * an IfcFaceBasedSurfaceModel of plain polyloop faces (surface-model),
both had no NGEOM path and fell back to OCC.
"""

from __future__ import annotations

import math

import numpy as np

import ada.geom.curves as cu
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def test_trimmed_line_respects_sense_agreement():
    from ada.cadit.ngeom.serialize import _sample_trimmed_curve

    line = cu.Line(pnt=Point(0, 0, 0), dir=(1.0, 0.0, 0.0))
    fwd = _sample_trimmed_curve(cu.TrimmedCurve(basis_curve=line, trim1=0.0, trim2=2.0, sense_agreement=True))
    rev = _sample_trimmed_curve(cu.TrimmedCurve(basis_curve=line, trim1=0.0, trim2=2.0, sense_agreement=False))
    assert np.allclose(fwd[0], [0, 0, 0]) and np.allclose(fwd[-1], [2, 0, 0])
    assert np.allclose(rev[0], [2, 0, 0]) and np.allclose(rev[-1], [0, 0, 0])  # reversed


def test_trimmed_circle_arc_short_way():
    from ada.cadit.ngeom.serialize import _sample_trimmed_curve

    circ = cu.Circle(position=Axis2Placement3D(location=(0, 0, 0)), radius=1.0)
    # 0 -> 90deg, sense True -> a quarter arc from (1,0) to (0,1)
    pts = np.array(_sample_trimmed_curve(cu.TrimmedCurve(circ, trim1=0.0, trim2=math.pi / 2, sense_agreement=True)))
    assert np.allclose(pts[0], [1, 0, 0], atol=1e-6)
    assert np.allclose(pts[-1], [0, 1, 0], atol=1e-6)
    assert np.allclose(np.linalg.norm(pts, axis=1), 1.0, atol=1e-6)  # every sample on the unit circle


def test_composite_pie_slice_area():
    from ada.cadit.ngeom.serialize import _composite_curve_loop_points

    circ = cu.Circle(position=Axis2Placement3D(location=(0, 0, 0)), radius=1.0)
    lx = cu.Line(pnt=Point(0, 0, 0), dir=(1.0, 0.0, 0.0))  # origin -> (1,0)
    ly = cu.Line(pnt=Point(0, 0, 0), dir=(0.0, 1.0, 0.0))  # origin -> (0,1)
    cc = cu.CompositeCurve(
        segments=[
            cu.CompositeCurveSegment(parent_curve=cu.TrimmedCurve(circ, 0.0, math.pi / 2, sense_agreement=True)),
            cu.CompositeCurveSegment(parent_curve=cu.TrimmedCurve(ly, 0.0, 1.0, sense_agreement=False)),  # (0,1)->0
            cu.CompositeCurveSegment(parent_curve=cu.TrimmedCurve(lx, 0.0, 1.0, sense_agreement=True)),  # 0->(1,0)
        ]
    )
    p = np.array(_composite_curve_loop_points(cc))
    x, y = p[:, 0], p[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    assert abs(area - math.pi / 4) < 0.01  # quarter unit disk
