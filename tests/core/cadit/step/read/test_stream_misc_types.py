"""STEP coverage tail: 2D placement, point-on, replicas, offset/intersection curves,
composite surface — the last canonical types. No geometry left behind.
"""

from __future__ import annotations

import ada.geom.curves as gc
import ada.geom.surfaces as gs
from ada.cadit.step.read import stream_reader as sr
from ada.geom.placement import Axis2Placement3D


class _IdResolver:
    def deref(self, x):
        return x


def test_axis2_placement_2d_promotes_to_3d():
    p = sr._b_axis2_placement_2d(_IdResolver(), ["", (0.0, 0.0, 0.0)])
    assert isinstance(p, Axis2Placement3D)


def test_point_on_curve_and_surface():
    poc = sr._b_point_on_curve(_IdResolver(), ["", gc.Line((0, 0, 0), (1, 0, 0)), 0.5])
    assert isinstance(poc, gc.PointOnCurve) and poc.parameter == 0.5
    pos = sr._b_point_on_surface(_IdResolver(), ["", gs.Plane(None), 0.2, 0.7])
    assert isinstance(pos, gs.PointOnSurface) and (pos.u, pos.v) == (0.2, 0.7)


def test_offset_curve_3d():
    oc = sr._b_offset_curve_3d(_IdResolver(), ["", gc.Line((0, 0, 0), (1, 0, 0)), 2.0, sr._Enum("F")])
    assert isinstance(oc, gc.OffsetCurve3D) and oc.distance == 2.0


def test_replicas_return_parent():
    parent = gc.Line((0, 0, 0), (1, 0, 0))
    assert sr._b_replica(_IdResolver(), ["", parent, "XFORM"]) is parent


def test_rectangular_composite_surface():
    patch = gs.Plane(None)

    class _R:
        def deref(self, x):
            return x if x is not patch else patch

    rcs = sr._b_rectangular_composite_surface(_IdResolver(), ["", [[patch, patch]]])
    assert isinstance(rcs, gs.RectangularCompositeSurface) and len(rcs.segments) == 2


def test_misc_types_registered():
    for t in ("AXIS2_PLACEMENT_2D", "POINT_ON_CURVE", "POINT_ON_SURFACE", "OFFSET_CURVE_3D",
              "INTERSECTION_CURVE", "CURVE_REPLICA", "SURFACE_REPLICA", "RECTANGULAR_COMPOSITE_SURFACE"):
        assert t in sr._BUILDERS
