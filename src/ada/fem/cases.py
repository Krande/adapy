"""Legacy monolithic eigen helper.

Kept as a thin wrapper for backward compatibility; new code should
compose `ada.api.fem_tasks.{design_cantilever, mesh_cantilever, run_eig}`
directly. The wrapper is what the verification driver and the
pytest suite used to share before the design->mesh->analyze split.
"""

from __future__ import annotations

import pathlib

from ada.api.fem_tasks import (
    design_cantilever,
    eig_case_name,
    is_eig_skip,
    mesh_cantilever,
    run_eig,
)
from ada.fem.results.common import FEAResult

SCRATCH_DIR = pathlib.Path(__file__).parent / "temp/eigen"

# Re-export for the few external scripts that import this name directly.
is_conditions_unsupported = is_eig_skip


def eigen_test(
    beam_fixture,  # noqa: ARG001 — kept for signature compatibility; design is canonical now
    fem_format,
    geom_repr,
    elem_order,
    use_hex_quad,
    short_name_map,  # noqa: ARG001
    reduced_integration,
    overwrite=True,
    execute=True,
    eigen_modes=11,
    name=None,
    debug=False,  # noqa: ARG001
    scratch_dir: pathlib.Path = SCRATCH_DIR,
    **kwargs,  # noqa: ARG001
) -> FEAResult | None:
    """Compose design/mesh/run_eig in one call. Use the underlying
    callables directly in new code.

    `beam_fixture`, `short_name_map`, `debug` and `**kwargs` are accepted
    but ignored: the new pipeline uses the canonical `design_cantilever`
    geometry, drops the short-name lookup (built into `eig_case_name`),
    and no longer threads gmsh debug knobs through this helper.
    """
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
    if name is None:
        name = eig_case_name(fem_format, geom_repr, elem_order, use_hex_quad, reduced_integration)

    return run_eig(
        a,
        fem_format=fem_format,
        scratch_dir=scratch_dir,
        name=name,
        eigen_modes=eigen_modes,
        overwrite=overwrite,
        execute=execute,
    )
