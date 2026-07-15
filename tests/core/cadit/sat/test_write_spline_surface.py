"""Encoders for the curved-plate SAT records, pinned against a Genie export.

The reference strings here are lifted verbatim from a Genie-authored hull model
(``spline-surface`` -36315 and one of its coedge pcurves) and reduced to a small
patch. They are ground truth for what Genie itself emits: the two conventions
that matter — the knot vector and the control-point order — are both invisible
in a self-consistent round trip and only show up against a real file.
"""

import re

import pytest

from ada.cadit.sat.write.sat_entities import PCurve, SplineSurface, _acis_knots
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
