from __future__ import annotations

import pathlib

import pytest

from ada.api.fem_tasks import (
    design_cantilever,
    eig_case_name,
    is_eig_skip,
    mesh_cantilever,
    run_eig,
)
from ada.fem.results.common import FEAResult

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/eigen"


@pytest.mark.parametrize("use_hex_quad", [True, False])
@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
@pytest.mark.parametrize("reduced_integration", [True, False])
def test_fem_eig(
    fem_format,
    geom_repr,
    elem_order,
    use_hex_quad,
    reduced_integration,
    overwrite=True,
    execute=True,
    eigen_modes=11,
) -> FEAResult | None:
    if is_eig_skip(
        fem_format=fem_format,
        geom_repr=geom_repr,
        elem_order=elem_order,
        use_hex_quad=use_hex_quad,
        reduced_integration=reduced_integration,
    ):
        return None

    a = design_cantilever()
    a = mesh_cantilever(
        a,
        geom_repr=geom_repr,
        elem_order=elem_order,
        use_hex_quad=use_hex_quad,
        reduced_integration=reduced_integration,
    )
    name = eig_case_name(fem_format, geom_repr, elem_order, use_hex_quad, reduced_integration)
    return run_eig(
        a,
        fem_format=fem_format,
        scratch_dir=SCRATCH_DIR,
        name=name,
        eigen_modes=eigen_modes,
        overwrite=overwrite,
        execute=execute,
    )
