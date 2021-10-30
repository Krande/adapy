import logging
import os
import pathlib

import pandas as pd

from ada import Assembly, Beam, Material, Part
from ada.config import Settings
from ada.fem import Bc, FemSet, Load, StepEigen, StepImplicit
from ada.fem.concepts.eigenvalue import EigenDataSummary
from ada.fem.exceptions import FEASolverNotInstalled, IncompatibleElements
from ada.fem.formats.utils import default_fem_res_path
from ada.fem.meshing.concepts import GmshOptions, GmshSession
from ada.fem.results import Results
from ada.fem.utils import get_beam_end_nodes, get_eldata
from ada.materials.metals import CarbonSteel


def static_cantilever():
    beam = Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (3, 0.5, 0.5),
        "IPE400",
        Material("S420", CarbonSteel("S420")),
    )

    p = Part("MyPart")
    a = Assembly("MyAssembly") / [p / beam]

    p.fem = beam.to_fem_obj(0.1, "shell", options=GmshOptions(Mesh_ElementOrder=2))

    fix_set = p.fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(beam), FemSet.TYPES.NSET))

    load_set = p.fem.add_set(FemSet("load_node", get_beam_end_nodes(beam, 2), FemSet.TYPES.NSET))
    a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))
    step = a.fem.add_step(StepImplicit("StaticStep"))
    step.add_load(Load("MyLoad", Load.TYPES.FORCE, -15e3, load_set, 3))
    return a


def make_eig_fem(
    beam: Beam,
    fem_format,
    geom_repr,
    elem_order=1,
    incomplete_2nd_order=True,
    overwrite=True,
    execute=True,
    eigen_modes=10,
):
    name = f"cantilever_EIG_{fem_format}_{geom_repr}_o{elem_order}"
    scratch_dir = Settings.scratch_dir / "eigen_fem"
    fem_res_files = default_fem_res_path(name, scratch_dir=scratch_dir)

    p = Part("MyPart")
    a = Assembly("MyAssembly") / [p / beam]
    os.makedirs("temp", exist_ok=True)
    if fem_res_files[fem_format].exists() is False:
        execute = True
        overwrite = True

    mesh_size = 0.05 if geom_repr != "line" else 0.3

    if overwrite is True:
        gmsh_opt = GmshOptions(Mesh_ElementOrder=elem_order, Mesh_MeshSizeMin=mesh_size)
        gmsh_opt.Mesh_SecondOrderIncomplete = 1 if incomplete_2nd_order is True else 0
        p.fem = beam.to_fem_obj(mesh_size, geom_repr, options=gmsh_opt)
        fs = p.fem.add_set(FemSet("bc_nodes", beam.bbox.sides.back(return_fem_nodes=True)))
        a.fem.add_bc(Bc("Fixed", fs, [1, 2, 3, 4, 5, 6]))

    a.fem.add_step(StepEigen("EigenStep", num_eigen_modes=eigen_modes))

    try:
        res = a.to_fem(
            name,
            fem_format,
            overwrite=overwrite,
            execute=execute,
            scratch_dir=scratch_dir,
        )
    except IncompatibleElements as e:
        logging.error(e)
        return None
    except FEASolverNotInstalled as e:
        logging.error(e)
        return None
    except BaseException as e:
        raise Exception(e)
    if res.output is not None:
        os.makedirs("temp/logs", exist_ok=True)
        with open(f"temp/logs/{name}.log", "w") as f:
            f.write(res.output.stdout)

    if res.eigen_mode_data is not None:
        for eig in res.eigen_mode_data.modes:
            print(eig)
    else:
        logging.error("Result file not created")

    assert pathlib.Path(res.results_file_path).exists()
    return res


def make_2nd_order_complete_elements():
    overwrite = False
    execute = False
    fem_format = "code_aster"
    elem_order = 2
    beam = Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (3, 0.5, 0.5),
        "IPE400",
        Material("S420", CarbonSteel("S420")),
    )
    geom_repr = "solid"
    gmsh_opt = GmshOptions(Mesh_ElementOrder=elem_order, Mesh_SecondOrderIncomplete=0)

    a = Assembly("MyAssembly") / [Part("MyPart") / beam]

    with GmshSession(silent=False, options=gmsh_opt) as gs:
        gs.add_obj(beam, geom_repr=geom_repr)
        gs.options.Mesh_Algorithm = 6
        gs.options.Mesh_Algorithm3D = 10
        # gs.open_gui()
        gs.mesh(0.05) if geom_repr != "line" else gs.mesh(1.0)
        a.get_part("MyPart").fem = gs.get_fem()
    print(get_eldata(a))
    fix_set = a.get_part("MyPart").fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(beam), FemSet.TYPES.NSET))
    a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))
    a.fem.add_step(StepEigen("Eigen", num_eigen_modes=10))

    name = f"cantilever_EIG_{fem_format}_{geom_repr}_o{elem_order}"
    res = a.to_fem(name, fem_format, overwrite=overwrite, execute=execute)

    if res.eigen_mode_data is not None:
        for eig in res.eigen_mode_data.modes:
            print(eig)

    assert pathlib.Path(res.results_file_path).exists()
    return res


def append_df(new_df, old_df):
    if old_df is None:
        updated_df = new_df
    else:
        updated_df = pd.concat([old_df, new_df], axis=1)
    return updated_df


def eig_data_to_df(eig_data: EigenDataSummary, columns):
    return pd.DataFrame([(e.no, e.f_hz) for e in eig_data.modes], columns=columns)


def eig_result_to_table(res: Results):
    eig_data = res.eigen_mode_data
    df = pd.DataFrame([(e.no, e.f_hz) for e in eig_data.modes], columns=["Mode", "Eigenvalue (real)"])
    return df.to_markdown(index=False, tablefmt="grid")


if __name__ == "__main__":
    a = static_cantilever()
    scratch = Settings.scratch_dir / "ada-testing"
    opts = dict(execute=True, overwrite=True, scratch_dir=scratch)
    a.to_fem("MyCantileverLoadTest_sesam", "sesam", **opts)
    a.to_fem("MyCantileverLoadTest_abaqus", "abaqus", **opts)
