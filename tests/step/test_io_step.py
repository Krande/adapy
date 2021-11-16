import pytest

from ada import Beam, CurvePoly, Section
from ada.param_models.basic_module import make_it_complex


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


def test_complex_stru(test_step_dir):
    a = make_it_complex()
    a.to_stp(test_step_dir / "MyComplexStru.stp")
