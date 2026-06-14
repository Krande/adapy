import ada
from ada.api.mass import MassPoint


def test_roundtrip_mass_point(tmp_path):
    mp = MassPoint("M1", (1, 2, 3), mass=250.0, radius=0.15)
    fp = (ada.Assembly() / (ada.Part("MyPart") / mp)).to_ifc(tmp_path / "mass.ifc", file_obj_only=True)

    got = ada.from_ifc(fp).get_by_name("M1")

    assert isinstance(got, MassPoint)  # not a plain PrimSphere
    assert got.mass == 250.0
    assert got.radius == 0.15
    assert tuple(float(c) for c in got.cog) == (1.0, 2.0, 3.0)


def test_roundtrip_weld(tmp_path):
    pl_points = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pl1 = ada.Plate("pl1", pl_points, 0.01)
    pl2 = ada.Plate("pl2", pl_points, 0.01, placement=ada.Placement((1.0, 0, 0)))
    weld_profile = [(-0.005, 0.01), (0, 0), (0.005, 0.01)]
    wld = ada.Weld("weld1", (1, 0, 0), (1, 1, 0), "V", [pl1, pl2], weld_profile, xdir=(-1, 0, 0))

    fp = (ada.Assembly() / (ada.Part("MyPart") / (pl1, pl2, wld))).to_ifc(tmp_path / "weld.ifc", file_obj_only=True)

    got = ada.from_ifc(fp).get_by_name("weld1")

    assert isinstance(got, ada.Weld)
    assert got.type == ada.WeldType.from_str("V")
    assert tuple(float(c) for c in got.p1.p) == (1.0, 0.0, 0.0)
    assert tuple(float(c) for c in got.p2.p) == (1.0, 1.0, 0.0)
    assert tuple(got.xdir) == (-1.0, 0.0, 0.0)
