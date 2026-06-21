"""STEP curve-type coverage: conics + polyline + trimmed + composite + pcurve.

Each must import into its native ada.geom curve type so no geometry is left behind.
Tests pin the reader builders (parser -> geom mapping).
"""

from __future__ import annotations

import ada.geom.curves as gc
from ada.cadit.step.read import stream_reader as sr


class _IdResolver:
    def deref(self, x):
        return x


_T = sr._Enum("T")
_CONT = sr._Enum("CONTINUOUS")


def test_parabola_imports():
    p = sr._b_parabola(_IdResolver(), ["", "POS", 2.5])
    assert isinstance(p, gc.Parabola) and p.focal_dist == 2.5


def test_hyperbola_imports():
    h = sr._b_hyperbola(_IdResolver(), ["", "POS", 3.0, 1.5])
    assert isinstance(h, gc.Hyperbola) and h.semi_axis == 3.0 and h.semi_imag_axis == 1.5


def test_polyline_imports():
    pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
    pl = sr._b_polyline(_IdResolver(), ["", pts])
    assert isinstance(pl, gc.PolyLine) and len(pl.points) == 3


def test_trimmed_curve_imports_with_parameter_trims():
    basis = gc.Line((0, 0, 0), (1, 0, 0))
    tc = sr._b_trimmed_curve(_IdResolver(), ["", basis, [0.0], [5.0], _T, sr._Enum("PARAMETER")])
    assert isinstance(tc, gc.TrimmedCurve)
    assert tc.trim1 == 0.0 and tc.trim2 == 5.0 and tc.sense_agreement is True


def test_composite_curve_imports():
    seg = sr._b_composite_curve_segment(_IdResolver(), [_CONT, _T, gc.Line((0, 0, 0), (1, 0, 0))])
    assert isinstance(seg, gc.CompositeCurveSegment) and seg.same_sense is True
    cc = sr._b_composite_curve(_IdResolver(), ["", [seg], _T])
    assert isinstance(cc, gc.CompositeCurve) and len(cc.segments) == 1


def test_pcurve_imports():
    pc = sr._b_pcurve(_IdResolver(), ["", "SURF", "REFCURVE"])
    assert isinstance(pc, gc.PCurve) and pc.basis_surface == "SURF"


def test_curve_types_registered():
    for t in ("PARABOLA", "HYPERBOLA", "POLYLINE", "TRIMMED_CURVE", "COMPOSITE_CURVE",
              "COMPOSITE_CURVE_SEGMENT", "PCURVE"):
        assert t in sr._BUILDERS
