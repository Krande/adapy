import ada
from ada.param_models.basic_module import SimpleStru


def gravity_step():
    step = ada.fem.StepImplicit("gravity", nl_geom=False, init_incr=50.0, total_time=100.0)
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))
    return step


def eigen_step():
    return ada.fem.StepEigen("gravity", 20)


def main():
    p = SimpleStru("MyStru")
    p.fem = p.to_fem_obj(0.1, use_quads=True)
    p.add_bcs()
    a = ada.Assembly("ParametricSite") / p
    # a.fem.add_step(gravity_step())
    a.fem.add_step(eigen_step())
    res = a.to_fem("ca_param_model_ca", "code_aster", overwrite=True, execute=True, scratch_dir="temp")
    res.to_xdmf(res.name + ".xdmf")
    # res.to_vtu()

    # a.to_fem("ca_param_model_ses", "sesam", overwrite=True, execute=True)


if __name__ == "__main__":
    main()
