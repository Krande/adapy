"""adapy -> IFC -> adapy cross-section round-trip fidelity.

Every parametric section type carries its parameters through IFC via an
``IfcProfileProperties`` bag attached to the profile, so re-import reproduces the
exact section regardless of whether it was written as a parametric or polyline
profile.
"""

import pytest

import ada

PARAMETRIC_SECTIONS = [
    "BG800x600x20x30",  # box (written as a polyline profile)
    "IPE300",  # i-profile
    "HEA260",  # i-profile (from db)
    "TG650x300x25x40",  # t-profile
    "HP200x10",  # angular (written as a polyline profile)
    "FB100x10",  # flatbar (written as a polyline profile)
    "TUB200x10",  # tubular
]


@pytest.mark.parametrize("sec_str", PARAMETRIC_SECTIONS)
def test_section_roundtrip(sec_str, tmp_path):
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), sec=sec_str)
    sec_o = bm.section

    fp = (ada.Assembly() / (ada.Part("P") / bm)).to_ifc(tmp_path / "sec.ifc", file_obj_only=True)
    a = ada.from_ifc(fp)
    sec_re = a.get_by_name("bm").section

    assert sec_re.type == sec_o.type
    assert sec_re.equal_props(sec_o), f"{sec_str}: {sec_re.unique_props()} != {sec_o.unique_props()}"


def test_general_section_roundtrip(tmp_path):
    """GeniE-style general beams (numeric properties only) survive the round-trip.

    They are exported with a visual circle profile but carry the full
    GeneralProperties via the ADA parameter bag.
    """
    from ada.sections.concept import GeneralProperties

    gp = GeneralProperties(
        Ax=0.00188594277,
        Ix=7.178e-08,
        Iy=6.1383e-06,
        Iz=1.2361e-07,
        Iyz=-4.5553e-07,
        Wxmin=4e-06,
        Wymin=5.62e-05,
        Wzmin=4.9e-06,
        Shary=0.0003924,
        Sharz=0.00103,
        Shceny=-0.0039015,
        Shcenz=-0.061909,
        Sy=4.76742207e-05,
        Sz=5.61828483e-06,
        Sfy=1,
        Sfz=1,
    )
    bm = ada.Beam("bm", (0, 0, 0), (10, 0, 0), sec=ada.Section("gp1", "GENBEAM", genprops=gp))
    sec_o = bm.section

    fp = (ada.Assembly() / (ada.Part("P") / bm)).to_ifc(tmp_path / "gen.ifc", file_obj_only=True)
    sec_re = ada.from_ifc(fp).get_by_name("bm").section

    assert sec_re.type == sec_o.type
    assert sec_re.equal_props(sec_o)
    assert sec_re.properties.Ax == pytest.approx(gp.Ax)
    assert sec_re.properties.Shcenz == pytest.approx(gp.Shcenz)


def test_box_profile_props_written(tmp_path):
    """The box profile carries an ADA parameter bag on the IfcProfileDef."""
    from ada.cadit.ifc.sections_props import ADA_SECTION_PSET

    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), sec="BG800x600x20x30")
    fp = (ada.Assembly() / (ada.Part("P") / bm)).to_ifc(tmp_path / "box.ifc", file_obj_only=True)

    prof_props = fp.by_type("IfcProfileProperties")
    assert any(pp.Name == ADA_SECTION_PSET for pp in prof_props)
    pp = next(pp for pp in prof_props if pp.Name == ADA_SECTION_PSET)
    assert pp.ProfileDefinition.is_a("IfcArbitraryProfileDefWithVoids")
    keys = {p.Name for p in pp.Properties}
    assert {"sec_type", "h", "w_top", "t_w", "t_ftop"} <= keys
