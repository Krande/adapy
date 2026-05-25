from __future__ import annotations

import pathlib

import pytest

from ada.api.fem_tasks import (
    design_cantilever,
    is_static_skip,
    mesh_cantilever,
    run_lin_static,
    static_case_name,
)

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/static"


@pytest.mark.parametrize("use_hex_quad", [True, False])
@pytest.mark.parametrize("fem_format", ["code_aster", "calculix"])
@pytest.mark.parametrize("geom_repr", ["line", "shell", "solid"])
@pytest.mark.parametrize("elem_order", [1, 2])
@pytest.mark.parametrize("nl_geom", [True, False])
def test_fem_static(
    fem_format,
    geom_repr,
    elem_order,
    use_hex_quad,
    nl_geom,
    overwrite=True,
    execute=True,
):
    if is_static_skip(fem_format=fem_format, geom_repr=geom_repr, elem_order=elem_order, nl_geom=nl_geom):
        return None
    # Line elements have no HEX/QUAD alternative.
    if geom_repr == "line" and use_hex_quad is True:
        return None

    a = design_cantilever()
    # Static cantilever doesn't exercise reduced_integration in the
    # current test matrix; pass False to keep mesh_cantilever happy.
    a = mesh_cantilever(
        a,
        geom_repr=geom_repr,
        elem_order=elem_order,
        use_hex_quad=use_hex_quad,
        reduced_integration=False,
    )
    name = static_case_name(fem_format, geom_repr, elem_order, use_hex_quad, nl_geom)
    return run_lin_static(
        a,
        fem_format=fem_format,
        scratch_dir=SCRATCH_DIR,
        name=name,
        nl_geom=nl_geom,
        overwrite=overwrite,
        execute=execute,
    )
