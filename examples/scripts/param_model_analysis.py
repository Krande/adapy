import pathlib

import ada
from ada.param_models.basic_module import SimpleStru

SCRATCH = pathlib.Path("temp")


def gravity_step():
    step = ada.fem.StepImplicitStatic("gravity", nl_geom=False, init_incr=50.0, total_time=100.0)
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))
    return step


def eigen_step():
    return ada.fem.StepEigen("EIGEN", 20)


def main():
    fem_res = "ca_param_model_ca"
    res_file = (SCRATCH / fem_res / fem_res).with_suffix(".rmed")

    p = SimpleStru("MyStru")
    p.fem = p.to_fem_obj(0.1, use_quads=False)
    p.add_bcs()
    a = ada.Assembly("ParametricSite") / p
    # a.fem.add_step(gravity_step())
    a.fem.add_step(eigen_step())
    if not res_file.exists():
        res = a.to_fem(fem_res, "code_aster", overwrite=True, execute=True, scratch_dir=SCRATCH)
    else:
        res = ada.from_fem_res(res_file, fem_format="code_aster")

    res.show(ping_timeout=30)


if __name__ == "__main__":
    main()
