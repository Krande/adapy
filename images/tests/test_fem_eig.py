import logging
import pathlib

import pytest

from ada import Assembly, Beam, Material, Part
from ada.fem import Bc, FemSet, Step
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.meshing.gmshapiv2 import GmshOptions, GmshSession
from ada.fem.utils import get_beam_end_nodes
from ada.materials.metals import CarbonSteel


@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
def test_fem_eig(fem_format, geom_repr, elem_order):
    name = f"cantilever_EIG_{fem_format}_{geom_repr}_o{elem_order}"

    beam = Beam("MyBeam", (0, 0.5, 0.5), (3, 0.5, 0.5), "IPE400", Material("S420", CarbonSteel("S420")))
    a = Assembly("MyAssembly") / [Part("MyPart") / beam]

    with GmshSession(silent=True, options=GmshOptions(Mesh_ElementOrder=elem_order)) as gs:
        gs.add_obj(beam, geom_repr=geom_repr)
        gs.mesh(0.05)
        a.get_part("MyPart").fem = gs.get_fem()

    fix_set = a.get_part("MyPart").fem.add_set(FemSet("bc_nodes", get_beam_end_nodes(beam), FemSet.TYPES.NSET))
    a.fem.add_bc(Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))
    a.fem.add_step(Step("Eigen", Step.TYPES.EIGEN, eigenmodes=11))

    try:
        res = a.to_fem(name, fem_format, overwrite=True, execute=True)
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
