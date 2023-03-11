import pytest

import ada
from ada import Beam, CurvePoly, Section


@pytest.fixture
def test_step_dir(test_dir):
    return test_dir / "step_basics"


def test_simple_beam(test_step_dir):
    bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), Section("mysec", from_str="IPE300"))
    bm.to_stp(test_step_dir / "MySimpleBeam.stp")


def test_advanced_beam(test_step_dir):
    poly = CurvePoly([(0, 0), (0.1, 0, 0.01), (0.1, 0.1, 0.01), (0, 0.1)], (0, 0, 0), (1, 0, 0), (0, 1, 0))
    bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), Section("MySec", outer_poly=poly))
    bm.to_stp(test_step_dir / "MySimpleBeamPoly.stp")


def test_plate1_w_hole(test_step_dir, plate1):
    plate1.add_penetration(ada.PrimCyl("MyPen", (0.5, 0.5, -0.2), (0.5, 0.5, 0.2), 0.2))
    plate1.to_stp(test_step_dir / "MyPlateWithHole.stp")
