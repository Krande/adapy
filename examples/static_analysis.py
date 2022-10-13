import ada
from ada.fem.formats.calculix.results.read_frd_file import read_from_frd_file
from ada.materials.metals import CarbonSteel, DnvGl16Mat


def gravity_step():
    step = ada.fem.StepImplicit("gravity", nl_geom=True, init_incr=50.0, total_time=100.0)
    step.add_load(ada.fem.LoadGravity("grav", -9.81 * 80))
    return step


def eigen_step():
    return ada.fem.StepEigen("gravity", 20)


def main():
    beam = ada.Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (3, 0.5, 0.5),
        "IPE400",
        ada.Material("S420", CarbonSteel("S420", plasticity_model=DnvGl16Mat(15e-3, "S355"))),
    )
    fem = beam.to_fem_obj(0.1, geom_repr="solid", use_hex=True)
    a = ada.Assembly() / (ada.Part("P1", fem=fem) / beam)

    fix_set = fem.add_set(ada.fem.FemSet("bc_nodes", beam.bbox.sides.back(return_fem_nodes=True, fem=fem)))
    a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))

    # a.fem.add_step(gravity_step())
    a.fem.add_step(eigen_step())

    # a.to_fem('static_cantilever_ses', 'sesam', overwrite=True, execute=True)
    # a.to_fem("static_cantilever_code_aster", "code_aster", overwrite=True, execute=True)
    rerun = True
    result_obj = a.to_fem("static_cantilever_calculix", "calculix", overwrite=rerun, execute=rerun)
    mesh = read_from_frd_file(result_obj.results_file_path)
    mesh.write(result_obj.results_file_path.with_suffix(".vtu"))

    # a.to_fem("static_cantilever_abaqus", "abaqus", overwrite=True, execute=True)


if __name__ == "__main__":
    main()