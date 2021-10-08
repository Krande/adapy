import logging

import pytest

from ada.fem import Bc, FemSet, Mass, StepEigen
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.io import FEATypes
from ada.fem.utils import get_beam_end_nodes

from .test_model_fixtures import beam_model_line

__all__ = [beam_model_line]


@pytest.fixture
def isotropic_mass():
    return Mass("IsotropicMass", None, 10)


@pytest.fixture
def anisotropic_mass():
    return Mass("AnIsotropicMass", None, [0, 0, 10], Mass.PTYPES.ANISOTROPIC)


@pytest.mark.parametrize("fem_format", FEATypes.all)
def test_beam_with_isotropic_mass(beam_model_line, isotropic_mass: Mass, fem_format):
    bm = beam_model_line.get_by_name("Bm")
    fix_nodes = get_beam_end_nodes(bm)
    fs_fix = bm.parent.fem.add_set(FemSet("FixSet", fix_nodes, FemSet.TYPES.NSET))
    bm.parent.fem.add_bc(Bc("FixBc", fs_fix, [1, 2, 3, 4, 5, 6]))

    end_nodes = get_beam_end_nodes(bm, 2)
    fs_mass = bm.parent.fem.add_set(FemSet("MassSet", end_nodes, FemSet.TYPES.NSET))
    isotropic_mass.fem_set = fs_mass
    bm.parent.fem.add_mass(isotropic_mass)

    a = bm.parent.get_assembly()
    a.fem.add_step(StepEigen("StepEig", num_eigen_modes=10))

    try:
        res = beam_model_line.to_fem(f"bm_wIsoMass_{fem_format}", fem_format, overwrite=True)  # , execute=True)
    except IncompatibleElements as e:
        logging.error(e)
        return

    if res is not None and res.eigen_mode_data is not None:
        for mode in res.eigen_mode_data.modes:
            print(mode)


if __name__ == "__main__":
    retcode = pytest.main([])
    print(retcode)
