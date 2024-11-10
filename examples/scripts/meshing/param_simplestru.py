import pytest

import ada
from ada.core.utils import roundoff
from ada.param_models.basic_module import SimpleStru


def main():
    p = SimpleStru("ParametricModel")

    p.fem = p.to_fem_obj(0.1, use_quads=True)
    p.add_bcs()
    p.fem.show()

    a = ada.Assembly("ParametricSite") / p

    param_model: SimpleStru = a.get_by_name("ParametricModel")

    assert len(param_model.fem.bcs) == 1
    assert len(param_model.fem.elements) == pytest.approx(1584, rel=10)
    assert len(param_model.fem.nodes) == pytest.approx(5331, rel=80)

    cog = param_model.fem.elements.calc_cog()
    tol = 0.01
    my_step = a.fem.add_step(ada.fem.StepImplicitStatic("static", total_time=1, max_incr=1, init_incr=1, nl_geom=False))
    my_step.add_load(ada.fem.Load("Gravity", "gravity", -9.81))

    # a.to_fem("SimpleStru_ca", fem_format="code_aster", overwrite=True, execute=False)
    # a.to_ifc(tmp_path / "SimpleStru", file_obj_only=True, validate=True)

    assert abs(roundoff(cog.p[0]) - 2.5) < tol
    assert abs(roundoff(cog.p[1]) - 2.5) < tol
    assert abs(roundoff(cog.p[2]) - 1.4695) < tol
    assert abs(roundoff(cog.tot_mass) - 6790.6268) < tol
    assert abs(roundoff(cog.tot_vol) - 0.865048) < tol


if __name__ == "__main__":
    main()
