import pytest
from common import build_test_simplestru_fem

from ada.config import Settings
from ada.core.utils import roundoff
from ada.fem import Load, StepImplicit
from ada.param_models.basic_module import SimpleStru

test_dir = Settings.test_dir / "param_models"


def test_to_fem():
    a = build_test_simplestru_fem()

    param_model: SimpleStru = a.get_by_name("ParametricModel")
    param_model.fem.sections.merge_by_properties()

    # a.to_ifc(test_dir / "my_simple_stru_weight.ifc")

    assert len(param_model.fem.bcs) == 1
    assert len(param_model.fem.elements) == pytest.approx(1584, rel=10)
    assert len(param_model.fem.nodes) == pytest.approx(5331, rel=80)

    cog = param_model.fem.elements.calc_cog()
    tol = 0.01

    my_step = a.fem.add_step(StepImplicit("static", total_time=1, max_incr=1, init_incr=1, nl_geom=False))
    my_step.add_load(Load("Gravity", "gravity", -9.81))
    # a.to_fem("SimpleStru_ufo", fem_format="usfos", overwrite=True, execute=False)

    assert abs(roundoff(cog.p[0]) - 2.5) < tol
    assert abs(roundoff(cog.p[1]) - 2.5) < tol
    assert abs(roundoff(cog.p[2]) - 1.5) < tol
    assert abs(roundoff(cog.tot_mass) - 6672.406) < tol
    assert abs(roundoff(cog.tot_vol) - 0.85) < tol
