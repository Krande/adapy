import ada


def test_roundtrip_beam_tapered(tmp_path):
    bm = ada.BeamTapered("MyTaper", (0, 0, 0), (0, 0, 1), "IPE400", "IPE300")
    fp = (ada.Assembly() / (ada.Part("MyPart") / bm)).to_ifc(tmp_path / "taper.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    got = a.get_by_name("MyTaper")

    assert isinstance(got, ada.BeamTapered)
    assert got.section.h == bm.section.h  # start IPE400
    assert got.taper.h == bm.taper.h  # end IPE300
    assert tuple(got.n1.p) == tuple(bm.n1.p)
    assert tuple(got.n2.p) == tuple(bm.n2.p)


def test_roundtrip_beam_sweep(tmp_path):
    curve = ada.CurvePoly2d.from_3d_points([(0, 0, 0), (1, 0, 0), (1, 1, 0)])
    bm = ada.BeamSweep("MySweep", curve=curve, sec="IPE300")
    fp = (ada.Assembly() / (ada.Part("MyPart") / bm)).to_ifc(tmp_path / "sweep.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    got = a.get_by_name("MySweep")

    assert isinstance(got, ada.BeamSweep)
    assert got.section.type == bm.section.type
    # sweep curve preserved (3 points)
    assert len(got.curve.points3d) == 3
