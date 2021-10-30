import logging
import pathlib

import pytest

import ada
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.meshing.concepts import GmshOptions
from ada.materials.metals import CarbonSteel

test_dir = ada.config.Settings.scratch_dir / "eigen_fem"


@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
def test_fem_eig(fem_format, geom_repr, elem_order):
    name = f"cantilever_EIG_{fem_format}_{geom_repr}_o{elem_order}"

    beam = ada.Beam("MyBeam", (0, 0.5, 0.5), (3, 0.5, 0.5), "IPE400", ada.Material("S420", CarbonSteel("S420")))
    p = ada.Part("MyPart")
    a = ada.Assembly("MyAssembly") / [p / beam]
    p.fem = beam.to_fem_obj(0.05, geom_repr, options=GmshOptions(Mesh_ElementOrder=elem_order))
    fix_set = p.fem.add_set(ada.fem.FemSet("bc_nodes", beam.bbox.sides.back(return_fem_nodes=True, fem=p.fem)))
    a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))
    a.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=11))

    try:
        res = a.to_fem(name, fem_format, overwrite=True, execute=True, scratch_dir=test_dir)
    except IncompatibleElements as e:
        if fem_format == "calculix" and geom_repr == "line":
            logging.error(e)
            return None
        elif fem_format == "code_aster" and geom_repr == "line" and elem_order == 2:
            logging.error(e)
            return None
        raise e

    if pathlib.Path(res.results_file_path).exists() is False:
        raise FileNotFoundError(f'FEM analysis was not successful. Result file "{res.results_file_path}" not found.')
