import pytest

import ada
from ada.core.utils import roundoff
from ada.param_models.basic_module import SimpleStru


def test_to_fem(param_models_test_dir):
    a = build_test_simplestru_fem(use_quads=True)

    param_model: SimpleStru = a.get_by_name("ParametricModel")

    assert len(param_model.fem.bcs) == 1
    assert len(param_model.fem.elements) == pytest.approx(1584, rel=10)
    assert len(param_model.fem.nodes) == pytest.approx(5331, rel=80)

    cog = param_model.fem.elements.calc_cog()
    tol = 0.01

    my_step = a.fem.add_step(ada.fem.StepImplicit("static", total_time=1, max_incr=1, init_incr=1, nl_geom=False))
    my_step.add_load(ada.fem.Load("Gravity", "gravity", -9.81))

    a.to_fem("SimpleStru_ca", fem_format="code_aster", overwrite=True, execute=False)
    a.to_ifc(param_models_test_dir / "SimpleStru", file_obj_only=True, validate=True)

    assert abs(roundoff(cog.p[0]) - 2.5) < tol
    assert abs(roundoff(cog.p[1]) - 2.5) < tol
    assert abs(roundoff(cog.p[2]) - 1.4695) < tol
    assert abs(roundoff(cog.tot_mass) - 6790.6268) < tol
    assert abs(roundoff(cog.tot_vol) - 0.865048) < tol


def build_test_simplestru_fem(mesh_size=0.3, make_fem=True, use_quads=False) -> ada.Assembly:
    p = SimpleStru("ParametricModel")

    if make_fem:
        p.fem = p.to_fem_obj(mesh_size, use_quads=use_quads)
        p.add_bcs()

    return ada.Assembly("ParametricSite") / p
