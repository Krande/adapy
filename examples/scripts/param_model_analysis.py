import pathlib

import ada
from ada.param_models.basic_module import SimpleStru

SCRATCH = pathlib.Path("temp")


def gravity_step():
    step = ada.fem.StepImplicit("gravity", nl_geom=False, init_incr=50.0, total_time=100.0)
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))
    return step


def eigen_step():
    return ada.fem.StepEigen("gravity", 20)


def main():
    fem_res = "ca_param_model_ca"
    res_file = (SCRATCH / fem_res / fem_res).with_suffix(".rmed")

    p = SimpleStru("MyStru")
    p.fem = p.to_fem_obj(0.1, use_quads=False)
    p.add_bcs()
    a = ada.Assembly("ParametricSite") / p
    # a.fem.add_step(gravity_step())
    a.fem.add_step(eigen_step())
    res = a.to_fem(fem_res, "code_aster", overwrite=True, execute=True, scratch_dir=SCRATCH)
    mesh = res.to_meshio_mesh(make_3xn_dofs=True)
    mesh.write(res_file.with_suffix('.vtu'))
    # res.to_vtu()

    # a.to_fem("ca_param_model_ses", "sesam", overwrite=True, execute=True)


def read_res():
    fem_res = "ca_param_model_ca"
    res_file = (SCRATCH / fem_res / fem_res).with_suffix(".rmed")
    res = ada.from_fem_res(res_file, "code_aster")
    mesh = res.to_meshio_mesh(make_3xn_dofs=True)
    mesh.write(res_file.with_suffix('.vtu'))
    # res.to_xdmf(res.name + ".xdmf")


if __name__ == "__main__":
    main()
    # read_res()
