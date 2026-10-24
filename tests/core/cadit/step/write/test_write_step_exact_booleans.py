"""Exact solids with exact cuts stay analytic in STEP: a filleted extrusion with an extruded bore and
round bolt holes cut out is written as one B-rep with planar and CYLINDRICAL faces, at the
analytic volume -- never as facets. And a POLY section beam built from plain (x, y) points has
the profile's own area times its length."""

import math

import pytest

import ada
from ada import Beam, Section
from ada.api.curves import CurvePoly2d
from ada.base.units import Units
from ada.sections.categories import BaseTypes


def _backend():
    from ada.cad import active_backend

    return active_backend()


def _flange_with_holes():
    """A 0.7 x 1.0 x 0.005 flange with 50 mm corner fillets, a 0.6 x 0.9 bore and 4 holes of 12 mm."""
    outer = [(-0.35, -0.5, 0.05), (0.35, -0.5, 0.05), (0.35, 0.5, 0.05), (-0.35, 0.5, 0.05)]
    flange = ada.PrimExtrude("flange", outer, 0.005, normal=(0, 0, 1), origin=(0, 0, 0), xdir=(1, 0, 0))
    bore = [(-0.3, -0.45), (0.3, -0.45), (0.3, 0.45), (-0.3, 0.45)]
    flange.add_boolean(ada.PrimExtrude("bore", bore, 0.007, normal=(0, 0, 1), origin=(0, 0, -0.001), xdir=(1, 0, 0)))
    for i, (x, y) in enumerate([(-0.325, -0.475), (0.325, -0.475), (0.325, 0.475), (-0.325, 0.475)]):
        flange.add_boolean(ada.PrimCyl(f"hole{i}", (x, y, -0.001), (x, y, 0.006), 0.006))
    return flange


def _analytic_flange_volume():
    r = 0.05
    outer = 0.7 * 1.0 - 4 * (r * r - math.pi * r * r / 4)
    return (outer - 0.6 * 0.9 - 4 * math.pi * 0.006**2) * 0.005


def test_a_cut_filleted_extrusion_is_built_at_its_analytic_volume():
    backend = _backend()
    solid = backend.build(_flange_with_holes().solid_geom())
    assert backend.volume(solid) == pytest.approx(_analytic_flange_volume(), rel=1e-9)


def test_a_cut_filleted_extrusion_is_written_as_analytic_faces(tmp_path):
    out = tmp_path / "flange.stp"
    (ada.Assembly("a") / (ada.Part("p") / _flange_with_holes())).to_stp(out)
    text = out.read_text()
    assert "MANIFOLD_SOLID_BREP" in text
    # The bolt holes and the fillets are cylinders, not facets.
    assert "CYLINDRICAL_SURFACE" in text
    assert "TRIANGULATED" not in text.upper()


def test_a_poly_section_of_plain_points_has_its_own_area():
    """(x, y) points: a third coordinate is read as a fillet radius, and (x, y, 0) points are not
    the polygon they look like."""
    pts = [(0.0, 0.0), (0.008, 0.0), (0.008, 0.154525), (0.033, 0.168959), (0.033, 0.18), (0.0, 0.18)]
    area = sum(pts[i][0] * pts[(i + 1) % 6][1] - pts[(i + 1) % 6][0] * pts[i][1] for i in range(6)) / 2
    sec = Section("bulb", sec_type=BaseTypes.POLY, outer_poly=CurvePoly2d(pts), units=Units.M)
    bm = Beam("bm", (0, 0, 0), (0, 1, 0), sec=sec, up=(0, 0, 1), units=Units.M)
    backend = _backend()
    assert backend.volume(backend.build(bm.solid_geom())) == pytest.approx(area * 1.0, rel=1e-9)
