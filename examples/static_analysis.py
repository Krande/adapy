import logging

import ada
from ada.fem.formats.calculix.results.read_frd_file import ReadFrdFailedException
from ada.fem.exceptions.element_support import IncompatibleElements
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
    for is_static in [True, False]:
        for geom_repr in ["line", "shell", "solid"]:
            fem = beam.to_fem_obj(0.1, geom_repr=geom_repr, use_hex=True, use_quads=True)
            a = ada.Assembly("static_cantilever") / (ada.Part("P1", fem=fem) / beam)

            fix_set = fem.add_set(ada.fem.FemSet("bc_nodes", beam.bbox.sides.back(return_fem_nodes=True, fem=fem)))
            a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))

            if is_static:
                prefix = f"static_{geom_repr}"
                a.fem.add_step(gravity_step())
            else:
                prefix = f"eigen_{geom_repr}"
                a.fem.add_step(eigen_step())

            rerun = True
            res_files = []
            for fea_software in ["abaqus", "sesam", "code_aster", "calculix"]:
                try:
                    res = a.to_fem(f"{prefix}_cantilever_{fea_software}", fea_software, overwrite=rerun, execute=rerun)
                except (ReadFrdFailedException, IncompatibleElements) as e:
                    logging.error(e)
                    continue
                res_files.append(res.results_file_path)

    # for resf in res_files:
    #    mesh = read_from_frd_file(resf)
    #    mesh.write(resf.with_suffix(".vtu"))


if __name__ == "__main__":
    main()
