"""Foreign IFC (no adapy parameters) must be reconstructed from geometry.

This reproduces the viewer audit failure where an XML->IFC converted box section
arrived as an ``IfcArbitraryProfileDefWithVoids`` named ``mdo_B4650x2000x25x25x25``
with no parametric metadata, which used to raise ``UnableToConvertSectionError``.
"""

import ifcopenshell
import pytest

from ada.cadit.ifc.read.read_beam_section import import_section_from_ifc
from ada.sections.categories import BaseTypes


def _polyline(f, pts):
    cps = [f.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y))) for x, y in pts]
    cps.append(cps[0])  # closed
    return f.create_entity("IfcPolyLine", Points=cps)


@pytest.fixture
def ifc4_file():
    return ifcopenshell.file(schema="IFC4")


def test_foreign_box_polyline_recognised(ifc4_file):
    f = ifc4_file
    h, w, tw, tf = 4.650, 2.000, 0.025, 0.025
    outer = _polyline(f, [(-w / 2, h / 2), (w / 2, h / 2), (w / 2, -h / 2), (-w / 2, -h / 2)])
    inner = _polyline(
        f, [(-w / 2 + tw, h / 2 - tf), (w / 2 - tw, h / 2 - tf), (w / 2 - tw, -h / 2 + tf), (-w / 2 + tw, -h / 2 + tf)]
    )
    prof = f.create_entity(
        "IfcArbitraryProfileDefWithVoids",
        ProfileType="AREA",
        ProfileName="mdo_B4650x2000x25x25x25",
        OuterCurve=outer,
        InnerCurves=[inner],
    )

    sec = import_section_from_ifc(prof)

    assert sec.type == BaseTypes.BOX
    assert sec.h == pytest.approx(h, abs=1e-6)
    assert sec.w_top == pytest.approx(w, abs=1e-6)
    assert sec.t_w == pytest.approx(tw, abs=1e-6)
    assert sec.t_ftop == pytest.approx(tf, abs=1e-6)
    assert sec.t_fbtn == pytest.approx(tf, abs=1e-6)


def test_foreign_solid_rect_is_flatbar(ifc4_file):
    f = ifc4_file
    outer = _polyline(f, [(-0.05, 0.1), (0.05, 0.1), (0.05, -0.1), (-0.05, -0.1)])
    prof = f.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA", ProfileName="solid", OuterCurve=outer)

    sec = import_section_from_ifc(prof)

    assert sec.type == BaseTypes.FLATBAR
    assert sec.h == pytest.approx(0.2, abs=1e-6)
    assert sec.w_top == pytest.approx(0.1, abs=1e-6)


def test_foreign_unrecognised_falls_back_to_poly(ifc4_file):
    f = ifc4_file
    outer = _polyline(f, [(0, 0), (1, 0), (0.5, 1)])  # triangle -> no parametric match
    prof = f.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA", ProfileName="tri", OuterCurve=outer)

    sec = import_section_from_ifc(prof)

    assert sec.type == BaseTypes.POLY
    assert sec.poly_outer is not None
