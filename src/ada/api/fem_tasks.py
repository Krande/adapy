"""Decomposed FEM cantilever pipeline: design -> mesh -> run_*.

Split out of the monolithic `eigen_test` in `ada.fem.cases` so the
verification driver, the pytest suite, and the future `paradoc.tasks`
runner can share three independent callables that pass `Assembly`
objects between phases.

Each phase returns a picklable object (Assembly or FEAResult) so the
pipeline can cross process boundaries when run under a multi-env
worker pool.
"""

from __future__ import annotations

import logging
import os
import pathlib
from typing import TYPE_CHECKING

import ada
from ada.base.types import GeomRepr
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.general import FEATypes as FEA
from ada.fem.formats.utils import default_fem_res_path
from ada.fem.meshing.concepts import GmshOptions
from ada.materials.metals import CarbonSteel, DnvGl16Mat

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult

logger = logging.getLogger(__name__)

BEAM_NAME = "MyBeam"
PART_NAME = "MyPart"
ASSEMBLY_NAME = "MyAssembly"

SHORT_NAME_MAP = {
    "calculix": "ccx",
    "code_aster": "ca",
    "abaqus": "aba",
    "sesam": "ses",
}


def design_cantilever() -> ada.Assembly:
    """Build the canonical cantilever assembly. Pure geometry, no FEM."""
    bm = ada.Beam(
        BEAM_NAME,
        (0, 0.5, 0.5),
        (3, 0.5, 0.5),
        "IPE400",
        ada.Material("S420", CarbonSteel("S420", plasticity_model=DnvGl16Mat(15e-3, "S355"))),
    )
    p = ada.Part(PART_NAME)
    return ada.Assembly(ASSEMBLY_NAME) / [p / bm]


def mesh_cantilever(
    a: ada.Assembly,
    *,
    geom_repr: str | GeomRepr,
    elem_order: int,
    use_hex_quad: bool,
    reduced_integration: bool,
    mesh_size: float = 0.07,
) -> ada.Assembly:
    """Mesh the cantilever in place and apply the fixed-end BC.

    Returns the same Assembly with `.fem` populated on its Part. The
    caller can subsequently add a Step (Eigen / Static / ...) and
    invoke a solver via `run_eig` / `run_lin_static`.

    Reduced-integration flags are written into the per-solver options so
    downstream `to_fem(...)` honors them when the input deck is emitted.
    """
    if isinstance(geom_repr, str):
        geom_repr = GeomRepr.from_str(geom_repr)

    p = a.get_part(PART_NAME)
    bm = next(b for b in p.get_all_physical_objects() if b.name == BEAM_NAME)

    props: dict = dict(use_hex=use_hex_quad) if geom_repr == GeomRepr.SOLID else dict(use_quads=use_hex_quad)
    props["options"] = GmshOptions(Mesh_ElementOrder=elem_order)

    p.fem = bm.to_fem_obj(mesh_size, geom_repr, **props)

    fix_set = p.fem.add_set(ada.fem.FemSet("bc_nodes", bm.bbox().sides.back(return_fem_nodes=True, fem=p.fem)))
    a.fem.add_bc(ada.fem.Bc("Fixed", fix_set, [1, 2, 3, 4, 5, 6]))

    for part in a.get_all_parts_in_assembly():
        if part.fem.is_empty():
            continue
        part.fem.options.ABAQUS.default_elements.use_reduced_integration = reduced_integration
        part.fem.options.CALCULIX.default_elements.use_reduced_integration = reduced_integration
        part.fem.options.CODE_ASTER.use_reduced_integration = reduced_integration

    return a


def is_eig_skip(
    *,
    fem_format: str | FEA,
    geom_repr: str | GeomRepr,
    elem_order: int,
    use_hex_quad: bool,
    reduced_integration: bool,
) -> bool:
    """True when this cell is a known-invalid (geom, solver, ...) combination."""
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    geom_repr = GeomRepr.from_str(geom_repr) if isinstance(geom_repr, str) else geom_repr

    if geom_repr == GeomRepr.LINE and use_hex_quad is True:
        return True
    if reduced_integration is True:
        if use_hex_quad is False and geom_repr in (GeomRepr.SHELL, GeomRepr.SOLID):
            return True
        if fem_format in (FEA.CODE_ASTER, FEA.SESAM):
            return True
    if fem_format == FEA.CALCULIX and geom_repr == GeomRepr.LINE:
        return True
    if fem_format == FEA.CODE_ASTER and geom_repr == GeomRepr.LINE and elem_order == 2:
        return True
    if fem_format == FEA.SESAM and geom_repr == GeomRepr.SOLID:
        return True
    # Abaqus S3 and S3R are identical; skip S3 (shell + order 1 + no RI + no HQ).
    if (
        fem_format == FEA.ABAQUS
        and geom_repr == GeomRepr.SHELL
        and elem_order == 1
        and reduced_integration is False
        and use_hex_quad is False
    ):
        return True
    return False


def is_static_skip(
    *,
    fem_format: str | FEA,
    geom_repr: str | GeomRepr,
    elem_order: int,
    nl_geom: bool,
) -> bool:
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    geom_repr = GeomRepr.from_str(geom_repr) if isinstance(geom_repr, str) else geom_repr

    if fem_format == FEA.CALCULIX and geom_repr == GeomRepr.LINE:
        return True
    if fem_format == FEA.CODE_ASTER:
        if geom_repr == GeomRepr.LINE and (nl_geom is True or elem_order == 2):
            return True
        if geom_repr == GeomRepr.SHELL and elem_order == 2 and nl_geom is True:
            return True
    return False


def eig_case_name(
    fem_format: str | FEA,
    geom_repr: str | GeomRepr,
    elem_order: int,
    use_hex_quad: bool,
    reduced_integration: bool,
) -> str:
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    geom_repr = GeomRepr.from_str(geom_repr) if isinstance(geom_repr, str) else geom_repr
    short = SHORT_NAME_MAP.get(fem_format.value, fem_format.value)
    return f"cantilever_EIG_{short}_{geom_repr.value}_o{elem_order}_hq{use_hex_quad}_ri{reduced_integration}"


def static_case_name(
    fem_format: str | FEA,
    geom_repr: str | GeomRepr,
    elem_order: int,
    use_hex_quad: bool,
    nl_geom: bool,
) -> str:
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    geom_repr = GeomRepr.from_str(geom_repr) if isinstance(geom_repr, str) else geom_repr
    short = SHORT_NAME_MAP.get(fem_format.value, fem_format.value)
    return f"cantilever_static_{short}_{geom_repr.value}_o{elem_order}_hq{use_hex_quad}_nl{nl_geom}"


def run_eig(
    a: ada.Assembly,
    *,
    fem_format: str | FEA,
    scratch_dir: pathlib.Path,
    name: str,
    eigen_modes: int = 11,
    overwrite: bool = True,
    execute: bool = True,
) -> "FEAResult | None":
    """Add an eigen step to a meshed Assembly and invoke the solver.

    Returns FEAResult, or None when running under pytest (the test only
    cares that no exception fired) or when the deck-only / replay path
    finds no cached results on disk.
    """
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    a.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=eigen_modes))
    return _invoke_solver(
        a, name=name, fem_format=fem_format, scratch_dir=scratch_dir, overwrite=overwrite, execute=execute
    )


def run_lin_static(
    a: ada.Assembly,
    *,
    fem_format: str | FEA,
    scratch_dir: pathlib.Path,
    name: str,
    nl_geom: bool = False,
    gravity_factor: float = -9.81 * 80,
    init_incr: float = 100.0,
    total_time: float = 100.0,
    overwrite: bool = True,
    execute: bool = True,
) -> "FEAResult | None":
    """Add an implicit static gravity step and invoke the solver."""
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    step = a.fem.add_step(
        ada.fem.StepImplicitStatic("gravity", nl_geom=nl_geom, init_incr=init_incr, total_time=total_time)
    )
    step.add_load(ada.fem.LoadGravity("grav", gravity_factor))
    return _invoke_solver(
        a, name=name, fem_format=fem_format, scratch_dir=scratch_dir, overwrite=overwrite, execute=execute
    )


def _invoke_solver(
    a: ada.Assembly,
    *,
    name: str,
    fem_format: FEA,
    scratch_dir: pathlib.Path,
    overwrite: bool,
    execute: bool,
) -> "FEAResult | None":
    """Common solver-invocation + replay path shared by `run_eig` / `run_lin_static`."""
    if overwrite is False:
        if "PYTEST_CURRENT_TEST" in os.environ:
            return None
        res_path = default_fem_res_path(name, scratch_dir=scratch_dir, fem_format=fem_format)
        if isinstance(res_path, pathlib.Path) and not res_path.exists():
            logger.info(f"Result file {res_path} not found.")
            return None
        return ada.from_fem_res(res_path, fem_format=fem_format)

    try:
        res = a.to_fem(
            name,
            fem_format,
            overwrite=overwrite,
            execute=execute,
            scratch_dir=scratch_dir,
            exit_on_complete=False,
        )
    except IncompatibleElements as e:
        logger.error(e)
        return None

    if res is None or pathlib.Path(res.results_file_path).exists() is False:
        raise FileNotFoundError(f'FEM analysis was not successful. Result file "{res}" not found.')

    if "PYTEST_CURRENT_TEST" in os.environ:
        return None

    return res
