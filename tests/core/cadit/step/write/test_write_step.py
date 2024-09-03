import ada
from ada import Beam, CurvePoly2d, Section


def test_simple_beam(tmp_path):
    bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), Section("mysec", from_str="HP200x10"))
    bm.to_stp(tmp_path / "MySimpleBeam.stp")


def test_advanced_beam(tmp_path):
    poly = CurvePoly2d([(0, 0), (0.1, 0, 0.01), (0.1, 0.1, 0.01), (0, 0.1)], (0, 0, 0), (1, 0, 0), (0, 1, 0))
    bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), Section("MySec", outer_poly=poly))
    bm.to_stp(tmp_path / "MySimpleBeamPoly.stp")


def test_plate1_w_hole(tmp_path, plate1):
    plate1.add_boolean(ada.PrimCyl("MyPen", (0.5, 0.5, -0.2), (0.5, 0.5, 0.2), 0.2))
    plate1.to_stp(tmp_path / "MyPlateWithHole.stp")
