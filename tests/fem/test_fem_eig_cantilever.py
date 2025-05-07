from __future__ import annotations

import logging
import os
import pathlib

import pytest

import ada
from ada.base.types import GeomRepr
from ada.fem.cases import eigen_test
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.general import FEATypes as FEA
from ada.fem.formats.utils import default_fem_res_path
from ada.fem.meshing.concepts import GmshOptions
from ada.fem.results.common import FEAResult

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/eigen"

EL_TYPES = ada.fem.Elem.EL_TYPES



@pytest.mark.parametrize("use_hex_quad", [True, False])
@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
@pytest.mark.parametrize("reduced_integration", [True, False])
def test_fem_eig(
    beam_fixture,
    fem_format,
    geom_repr,
    elem_order,
    use_hex_quad,
    short_name_map,
    reduced_integration,
    overwrite=True,
    execute=True,
    eigen_modes=11,
    name=None,
    debug=False,
    **kwargs,
) -> FEAResult | None:

    return eigen_test(beam_fixture,
        fem_format,
        geom_repr,
        elem_order,
        use_hex_quad,
        short_name_map,
        reduced_integration,
        overwrite=overwrite,
        execute=execute,
        eigen_modes=eigen_modes,
        name=name,
        debug=debug,
        **kwargs
    )
