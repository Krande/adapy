import logging
import pathlib

import pytest

import ada
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats import FEATypes as FEA
from ada.fem.meshing.concepts import GmshOptions
from ada.materials.metals import CarbonSteel

test_dir = ada.config.Settings.scratch_dir / "eigen_fem"
EL_TYPES = ada.fem.Elem.EL_TYPES


def beam() -> ada.Beam:
    return ada.Beam("MyBeam", (0, 0.5, 0.5), (3, 0.5, 0.5), "IPE400", ada.Material("S420", CarbonSteel("S420")))


@pytest.fixture
def beam_fixture() -> ada.Beam:
    return beam()


@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
def test_fem_eig(beam_fixture, fem_format, geom_repr, elem_order, overwrite=True, execute=True, eigen_modes=11):
    name = f"cantilever_EIG_{fem_format}_{geom_repr}_o{elem_order}"

    p = ada.Part("MyPart")
    a = ada.Assembly("MyAssembly") / [p / beam_fixture]
    p.fem = beam_fixture.to_fem_obj(0.05, geom_repr, options=GmshOptions(Mesh_ElementOrder=elem_order))
    fix_set = p.fem.add_set(ada.fem.FemSet("bc_nodes", beam_fixture.bbox.sides.back(return_fem_nodes=True, fem=p.fem)))
    a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))
    a.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=eigen_modes))
    geom_repr = geom_repr.upper()
    try:
        res = a.to_fem(name, fem_format, overwrite=overwrite, execute=execute, scratch_dir=test_dir)
    except IncompatibleElements as e:
        if fem_format == FEA.CALCULIX and geom_repr == EL_TYPES.LINE:
            logging.error(e)
            return None
        elif fem_format == FEA.CODE_ASTER and geom_repr == EL_TYPES.LINE and elem_order == 2:
            logging.error(e)
            return None
        elif fem_format == FEA.SESAM and geom_repr == EL_TYPES.SOLID:
            logging.error(e)
            return None
        raise e
    finally:
        with open(test_dir / name / "run.log", "w") as f:
            f.write(res.output.stdout)

    if pathlib.Path(res.results_file_path).exists() is False:
        raise FileNotFoundError(f'FEM analysis was not successful. Result file "{res.results_file_path}" not found.')

    return res
