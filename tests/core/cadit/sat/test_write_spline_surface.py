"""Encoders for the curved-plate SAT records, pinned against a Genie export.

The reference strings here are lifted verbatim from a Genie-authored hull model
(``spline-surface`` -36315 and one of its coedge pcurves) and reduced to a small
patch. They are ground truth for what Genie itself emits: the two conventions
that matter — the knot vector and the control-point order — are both invisible
in a self-consistent round trip and only show up against a real file.
"""

import re

import pytest

from ada.cadit.sat.write.sat_entities import (
    EllipseCurve,
    IntCurve,
    PCurve,
    SplineSurface,
    _acis_knots,
    circle_param_of,
)
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su


def _surface(u_degree=2, v_degree=3):
    """A 3x4 rational patch, the shape a Genie hull plate uses."""
    pts = [[(float(iu), float(iv), 0.0) for iv in range(4)] for iu in range(3)]
    return geo_su.RationalBSplineSurfaceWithKnots(
        u_degree=u_degree,
        v_degree=v_degree,
        control_points_list=pts,
        surface_form=geo_su.BSplineSurfaceForm.UNSPECIFIED,
        u_closed=False,
        v_closed=False,
        self_intersect=False,
        u_multiplicities=[u_degree + 1, u_degree + 1],
        v_multiplicities=[v_degree + 1, v_degree + 1],
        u_knots=[0.0, 1.0],
        v_knots=[0.0, 1.0],
        knot_spec=geo_cu.KnotType.UNSPECIFIED,
        weights_data=[[1.0] * 4 for _ in range(3)],
    )


class TestAcisKnots:
    """ACIS stores one fewer knot at each end than IFC does."""

    def test_end_multiplicities_drop_by_one(self):
        # IFC gives n + degree + 1 knots; ACIS wants n + degree - 1
        assert _acis_knots([0.0, 1.0], [3, 3], 2) == "0 2 1 2"
        assert _acis_knots([0.0, 1.0], [4, 4], 3) == "0 3 1 3"

    def test_interior_knots_are_untouched(self):
        assert _acis_knots([0.0, 0.5, 1.0], [3, 1, 3], 2) == "0 2 0.5 1 1 2"

    def test_control_point_count_reconciles(self):
        """ACIS derives n_ctrl = sum(mults) - degree + 1; that must give 3."""
        emitted = _acis_knots([0.0, 1.0], [3, 3], 2)
        mults = [int(x) for x in emitted.split()[1::2]]
        assert sum(mults) - 2 + 1 == 3

    def test_multiplicity_too_low_for_the_degree_raises(self):
        with pytest.raises(ValueError, match="too low"):
            _acis_knots([0.0, 1.0], [1, 1], 2)

    def test_mismatched_knots_and_multiplicities_raise(self):
        with pytest.raises(ValueError, match="multiplicities"):
            _acis_knots([0.0, 0.5, 1.0], [3, 3], 2)


class TestSplineSurface:
    def test_header_and_shape_match_genie(self):
        body = SplineSurface(1, _surface()).subtype()
        assert body.startswith("{ exactsur full nurbs 2 3 both open open none none 2 2 ")
        assert body.endswith("0 0 0 0 0 0 0 F 1 F 0 F 1 F 0 }")
        # 3x4 control points, each `x y z w`
        assert len(re.findall(r"\d+ \d+ \d+ 1", body)) >= 12

    def test_control_points_run_u_fastest(self):
        """The grid is [u][v]; ACIS writes the transpose.

        Genie's first run of points is n_u long — identifiable because it
        carries the 1, cos(t/2), 1 weights of a degree-2 rational arc.
        """
        s = _surface()
        # tag each control point with its (u, v) via x=u, y=v
        s.control_points_list = [[(float(iu), float(iv), 0.0) for iv in range(4)] for iu in range(3)]
        body = SplineSurface(1, s).subtype()
        pts = re.findall(r"(\d+) (\d+) 0 1", body)
        assert len(pts) == 12
        # first three points share v=0 and step through u
        assert [p[0] for p in pts[:3]] == ["0", "1", "2"]
        assert {p[1] for p in pts[:3]} == {"0"}

    def test_sense_is_written(self):
        assert " reversed { exactsur" in SplineSurface(1, _surface(), sense="reversed").to_string()
        assert " forward { exactsur" in SplineSurface(1, _surface()).to_string()

    def test_record_is_a_spline_surface(self):
        rec = SplineSurface(7, _surface()).to_string()
        assert rec.startswith("-7 spline-surface $-1 -1 -1 $-1 ")
        assert rec.endswith("I I I I #")


class TestPCurve:
    @staticmethod
    def _pcurve(fit_tolerance=0.0):
        return geo_cu.Pcurve2dBSpline(
            degree=1,
            control_points_2d=[[0.0, -4.0], [0.5, -4.0]],
            knots=[0.0, 0.5],
            knot_multiplicities=[2, 2],
            fit_tolerance=fit_tolerance,
        )

    def test_exppc_head_matches_genie(self):
        rec = PCurve(2, self._pcurve(), SplineSurface(1, _surface())).to_string()
        assert rec.startswith("-2 pcurve $-1 -1 -1 $-1 0 forward { exppc nubs 1 open 2 ")
        # degree-1 knots: IFC [2, 2] -> ACIS [1, 1]
        assert "{ exppc nubs 1 open 2 0 1 0.5 1 0 -4 0.5 -4 " in rec
        assert rec.endswith("I I I I } 0 0 #")

    def test_fit_tolerance_is_written_not_assumed(self):
        """0 claims the pcurve is exact; Genie says 0.001 on a quarter of them."""
        assert " 0.5 -4 0.001 -1 spline " in PCurve(2, self._pcurve(0.001), SplineSurface(1, _surface())).to_string()
        assert " 0.5 -4 0 -1 spline " in PCurve(2, self._pcurve(0.0), SplineSurface(1, _surface())).to_string()

    def test_the_surface_is_embedded(self):
        """A pcurve is meaningless without the surface its uv space belongs to."""
        surf = SplineSurface(1, _surface(), sense="reversed")
        rec = PCurve(2, self._pcurve(), surf).to_string()
        assert "spline reversed { exactsur full nurbs 2 3 " in rec

    def test_rational_pcurve_refuses_rather_than_dropping_weights(self):
        pc = self._pcurve()
        pc.weights = [1.0, 0.9]
        with pytest.raises(NotImplementedError, match="rational pcurve"):
            PCurve(2, pc, SplineSurface(1, _surface())).to_string()


class TestEllipseCurve:
    @staticmethod
    def _circle(radius=3.5):
        from ada import Direction, Point
        from ada.geom.placement import Axis2Placement3D

        pos = Axis2Placement3D(
            location=Point(-89.3, -31.0, 3.5),
            axis=Direction(0.0, 0.0, 1.0),
            ref_direction=Direction(1.0, 0.0, 0.0),
        )
        return geo_cu.Circle(pos, radius)

    def test_record_matches_genie(self):
        rec = EllipseCurve(1, self._circle(), 0.0, 0.3926990816987237).to_string()
        # centre, unit normal, major axis as a VECTOR of length radius, ratio 1
        assert rec == ("-1 ellipse-curve $-1 -1 -1 $-1 -89.3 -31 3.5 0 0 1 3.5 0 0 1 F 0 F 0.3926990816987237 #")

    def test_major_axis_carries_the_radius(self):
        assert " 3.5 0 0 1 F " in EllipseCurve(1, self._circle(3.5), 0.0, 1.0).to_string()
        assert " 7 0 0 1 F " in EllipseCurve(1, self._circle(7.0), 0.0, 1.0).to_string()

    def test_a_descending_range_is_refused(self):
        """The coedge sense carries direction; the curve's range always ascends."""
        with pytest.raises(ValueError, match="must ascend"):
            EllipseCurve(1, self._circle(), 0.3926990816987237, 0.0)


class TestIntCurve:
    @staticmethod
    def _bspline(rational=False):
        pts = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (3.0, 0.0, 0.0)]
        kw = dict(
            degree=3,
            control_points_list=pts,
            curve_form=geo_cu.BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=False,
            self_intersect=False,
            knot_multiplicities=[4, 4],
            knots=[0.0, 2.33],
            knot_spec=geo_cu.KnotType.UNSPECIFIED,
        )
        if rational:
            return geo_cu.RationalBSplineCurveWithKnots(**kw, weights_data=[1.0, 0.9, 0.9, 1.0])
        return geo_cu.BSplineCurveWithKnots(**kw)

    def test_record_matches_genie(self):
        rec = IntCurve(1, self._bspline()).to_string()
        # degree 3, IFC mults [4, 4] -> ACIS [3, 3], then 4 control points
        assert rec.startswith(
            "-1 intcurve-curve $-1 -1 -1 $-1 forward { exactcur full nubs 3 open 2 0 3 2.33 3 "
            "0 0 0 1 1 0 2 1 0 3 0 0 0 "
        )
        # lies on no surface, and needs no approximating data
        assert "null_surface null_surface nullbs nullbs -1 -1 I I 0 0 0 -1 none F F 1 F 0 } I I #" in rec

    def test_control_point_count_follows_the_knots(self):
        """ACIS reads n_ctrl back as sum(mults) - degree + 1; it must give 4."""
        rec = IntCurve(1, self._bspline()).to_string()
        mults = [3, 3]  # what _acis_knots emits for [4, 4] at degree 3
        assert sum(mults) - 3 + 1 == 4
        assert rec.count(" 0 0 0 1 1 0 2 1 0 3 0 0 ") == 1

    def test_sense_is_written(self):
        assert " reversed { exactcur" in IntCurve(1, self._bspline(), sense="reversed").to_string()

    def test_fit_tolerance_is_written(self):
        assert " 3 0 0 0.001 null_surface" in IntCurve(1, self._bspline(), fit_tolerance=0.001).to_string()

    def test_rational_curve_refuses_rather_than_dropping_weights(self):
        """nubs has nowhere to put a weight; writing one anyway moves the curve."""
        with pytest.raises(NotImplementedError, match="rational edge curve"):
            IntCurve(1, self._bspline(rational=True)).to_string()


class TestCircleParamOf:
    """The angle convention: about the axis, from the reference direction."""

    @staticmethod
    def _circle():
        from ada import Direction, Point
        from ada.geom.placement import Axis2Placement3D

        pos = Axis2Placement3D(
            location=Point(0.0, 0.0, 0.0),
            axis=Direction(0.0, 0.0, 1.0),
            ref_direction=Direction(1.0, 0.0, 0.0),
        )
        return geo_cu.Circle(pos, 2.0)

    def test_reference_direction_is_zero(self):
        assert circle_param_of(self._circle(), (2.0, 0.0, 0.0)) == pytest.approx(0.0)

    def test_quarter_turn(self):
        import math

        assert circle_param_of(self._circle(), (0.0, 2.0, 0.0)) == pytest.approx(math.pi / 2)

    def test_wraps_rather_than_going_negative(self):
        import math

        # just short of the reference direction reads as ~2pi, not a small negative
        p = circle_param_of(self._circle(), (2.0, -1e-9, 0.0))
        assert p > math.pi, f"expected a wrap to ~2pi, got {p}"


def test_fit_tolerance_survives_a_pcurve_reversal():
    """Reversing changes direction, not how well the curve fits."""
    from ada.cadit.sat.read.curves import _reverse_pcurve_2d

    pc = geo_cu.Pcurve2dBSpline(
        degree=1,
        control_points_2d=[[0.0, 0.0], [1.0, 0.0]],
        knots=[0.0, 1.0],
        knot_multiplicities=[2, 2],
        fit_tolerance=0.001,
    )
    assert _reverse_pcurve_2d(pc).fit_tolerance == 0.001
