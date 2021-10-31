from ada import Assembly, Beam, CurvePoly, Material, Part, Section
from ada.config import Settings

test_dir = Settings.test_dir / "beams"


def test_iprofiles():
    for sec in ["IPE300"]:
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
        assert isinstance(bm.section, Section)
        assert isinstance(bm.material, Material)


def test_hea_profiles():
    for sec in ["HEA300"]:
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
        assert isinstance(bm.section, Section)
        assert isinstance(bm.material, Material)


def test_heb_profiles():
    for sec in ["HEB300"]:
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
        assert isinstance(bm.section, Section)
        assert isinstance(bm.material, Material)


def test_igirders():
    for sec in ["IG1200x600x20x30", "IG.1200x600x20x30"]:
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
        assert isinstance(bm.section, Section)
        assert isinstance(bm.material, Material)


def test_box_girder():
    for sec in ["BGA.1000x400x20x30", "BG.1000x400x20x30"]:
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
        assert isinstance(bm.section, Section)
        assert isinstance(bm.material, Material)


def test_hp_profile():
    for sec in ["HP200x10"]:
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
        assert isinstance(bm.section, Section)
        assert isinstance(bm.material, Material)


def test_tub_profile():
    validation = [dict(r=0.3, wt=0.04)]
    for i, sec in enumerate(["PIPE300x40"]):
        bm = Beam("my_beam", (0, 0, 0), (0, 0, 1), sec)
        assert isinstance(bm.section, Section)
        assert isinstance(bm.material, Material)
        assert validation[i]["r"] == bm.section.r
        assert validation[i]["wt"] == bm.section.wt


def test_circ_profile():
    validation = [dict(r=0.125)]
    for i, sec in enumerate(["CIRC125"]):
        bm = Beam("my_beam", (0, 0, 0), (1, 1, 1), sec)
        assert isinstance(bm.section, Section)
        assert isinstance(bm.material, Material)
        assert validation[i]["r"] == bm.section.r


def test_tapered_profile():
    bm = Beam("MyTaperedBeam", (0, 0, 0), (1, 1, 1), "TUB300/200x20")
    sec = bm.section
    tap = bm.taper
    assert sec.r == 0.3
    assert tap.r == 0.2
    # a = Assembly() / (Part("Test") / bm)
    # a.to_ifc(test_dir / "tapered.ifc")


def test_cone_beam():
    s_o = [(375.0, 375.0, 375.0), (375.0, -375.0, 375.0), (-375.0, -375.0, 375.0), (-375.0, 375.0, 375.0)]
    s_i = [(325.0, 325.0, 325.0), (-325.0, 325.0, 325.0), (-325.0, -325.0, 325.0), (325.0, -325.0, 325.0)]

    e_o = [(525.0, 525.0, 525.0), (525.0, -525.0, 525.0), (-525.0, -525.0, 525.0), (-525.0, 525.0, 525.0)]
    e_i = [(475.0, 475.0, 475.0), (-475.0, 475.0, 475.0), (-475.0, -475.0, 475.0), (475.0, -475.0, 475.0)]
    poly_s_o = CurvePoly(s_o, (0, 0, 0), (0, 0, 1), (1, 0, 0))
    poly_s_i = CurvePoly(s_i, (0, 0, 0), (0, 0, 1), (1, 0, 0))
    section_s = Section("MyStartCrossSection", "poly", outer_poly=poly_s_o, inner_poly=poly_s_i, units="mm")

    poly_e_o = CurvePoly(e_o, (0, 0, 0), (0, 0, 1), (1, 0, 0))
    poly_e_i = CurvePoly(e_i, (0, 0, 0), (0, 0, 1), (1, 0, 0))
    section_e = Section("MyEndCrossSection", "poly", outer_poly=poly_e_o, inner_poly=poly_e_i, units="mm")

    bm = Beam("MyCone", (2, 2, 2), (4, 4, 4), sec=section_s, tap=section_e)
    a = Assembly("Level1", project="Project0", units="mm") / (Part("Level2") / bm)
    a.to_ifc(test_dir / "cone_ex.ifc")
