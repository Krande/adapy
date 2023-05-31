import pytest

import ada


@pytest.fixture
def ifc_roundtrip_test_dir(ifc_test_dir):
    return ifc_test_dir / "roundtripping"


def test_roundtrip_ipe_beam(bm_ipe300, ifc_roundtrip_test_dir):
    sec_o = bm_ipe300.section
    ifc_beam_file = ifc_roundtrip_test_dir / "ipe300.ifc"
    fp = (ada.Assembly() / (ada.Part("MyPart") / bm_ipe300)).to_ifc(ifc_beam_file, file_obj_only=True)

    a = ada.from_ifc(fp)
    bm: ada.Beam = a.get_by_name("MyIPE300")
    p = bm.parent
    sec = bm.section

    assert p.name == "MyPart"
    assert bm.name == "MyIPE300"
    assert sec.type == sec_o.type

    assert tuple(bm.n1.p) == tuple(bm_ipe300.n1.p)
    assert tuple(bm.n2.p) == tuple(bm_ipe300.n2.p)

    # p.fem = bm.to_fem_obj(0.1, "shell")
    # a.to_fem("MyFEM_from_ifc_file", "usfos", overwrite=True)


def test_beam_offset(ifc_roundtrip_test_dir):
    bm1 = ada.Beam(
        "bm1",
        n1=[0, 0, 0],
        n2=[2, 0, 0],
        sec="IPE300",
        color="red",
        up=(0, 0, 1),
        e1=(0, 0, -0.1),
        e2=(0, 0, -0.1),
    )
    bm2 = ada.Beam(
        "bm2",
        n1=[0, 0, 0],
        n2=[2, 0, 0],
        sec="IPE300",
        color="blue",
        up=(0, 0, -1),
        e1=(0, 0, -0.1),
        e2=(0, 0, -0.1),
    )

    a = ada.Assembly("Toplevel") / [ada.Part("MyPart") / [bm1, bm2]]
    _ = a.to_ifc(ifc_roundtrip_test_dir / "beams_offset.ifc", file_obj_only=True)


def test_beam_orientation(ifc_roundtrip_test_dir):
    props = dict(n1=[0, 0, 0], n2=[2, 0, 0], sec="HP200x10")
    bm1 = ada.Beam("bm_up", **props, up=(0, 0, 1))
    bm2 = ada.Beam("bm_down", **props, up=(0, 0, -1))
    fp = (ada.Assembly("MyAssembly") / (ada.Part("MyPart") / [bm1, bm2])).to_ifc(
        ifc_roundtrip_test_dir / "up_down", file_obj_only=True
    )

    a = ada.from_ifc(fp)

    bm_d: ada.Beam = a.get_by_name("bm_down")
    bm_u: ada.Beam = a.get_by_name("bm_up")

    assert tuple(bm_u.up) == tuple(bm1.up)
    assert tuple(bm_d.up) == tuple(bm2.up)


def test_beam_directions(ifc_roundtrip_test_dir):
    sec = "HP200x10"

    beams = [
        ada.Beam("bm_test2X0", n1=[0, 0, 0], n2=[5, 0, 0], angle=0, sec=sec),
        ada.Beam("bm_test2X90", n1=[0, 0, 1], n2=[5, 0, 1], angle=90, sec=sec),
        ada.Beam("bm_test2Y0", n1=[0, 0, 2], n2=[0, 5, 2], angle=0, sec=sec),
        ada.Beam("bm_test2Y90", n1=[0, 0, 3], n2=[0, 5, 3], angle=90, sec=sec),
    ]
    a = ada.Assembly("AdaRotatedProfiles") / (ada.Part("Part") / beams)
    _ = a.to_ifc(ifc_roundtrip_test_dir / "my_angled_profiles.ifc", file_obj_only=True)
