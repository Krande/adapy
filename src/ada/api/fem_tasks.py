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


# ---------------------------------------------------------------------------------------------
# The plate strip: a shell case with a closed form, meshed by gmsh and written as a deck
# ---------------------------------------------------------------------------------------------
#
# The cantilever above covers line, shell and solid representations of one *beam*. This is the
# plate half: an ``ada.Plate`` meshed as shells, loaded by a surface pressure, and checked against
# a closed form rather than only against another solver.
#
# It is deliberately the same strip the Sesam pressure writer was verified on -- 4.0 x 0.5 m, 10 mm
# S355, simply supported on the short edges and held in cylindrical bending on the long ones -- so
# the verification report and that test are measuring one model, not two that look alike.

PLATE_PART_NAME = "Strip"
PLATE_NAME = "strip"
PLATE_BAR_NAME = "stiffener"

#: The element set every shell of the plate lands in, and the surface built over it. The set is what
#: the decks name: ``*DLOAD``, ``FORCE_COQUE`` and BEUSLO all address elements.
PLATE_ELSET_NAME = "PLATE_SHELLS"
PLATE_SURFACE_NAME = "PLATE_FACE"

#: Span, width and thickness, metres. ``L / t = 400``: thin, so transverse shear sits four orders
#: below the discretisation error and the thin-plate closed form is the right reference.
PLATE_STRIP_LENGTH = 4.0
PLATE_STRIP_WIDTH = 0.5
PLATE_STRIP_THICKNESS = 0.010

#: Uniform pressure, pascals, on the face on the elements' positive normal.
PLATE_STRIP_PRESSURE = 1000.0

#: S355 as ``CarbonSteel`` gives it.
PLATE_E = 210.0e9
PLATE_NU = 0.3
PLATE_RHO = 7850.0

_TOL = 1e-09


def _plate_support_groups(fem) -> tuple:
    """The support sets, chosen so that no dof is constrained twice.

    Simple support on the two short edges plus cylindrical bending on the two long ones is four
    overlapping regions at the corners, and the obvious spelling -- one set per edge -- constrains
    ``u2`` twice there. Abaqus and Sestra accept that; Code_Aster refuses it outright with
    ``<EXCEPTION> <ASSEMBLA_26> le noeud: 1 composante: DY est bloqué plusieurs fois``, so a model
    written that way is not a cross-solver case at all. The corner nodes are therefore split out and
    carry only the rotation the long edges add.

    Returns ``(name, nodes, dofs, what it means)`` tuples.
    """
    on_x0 = lambda n: abs(n.x) < _TOL  # noqa: E731
    on_x1 = lambda n: abs(n.x - PLATE_STRIP_LENGTH) < _TOL  # noqa: E731
    from ada.fem.shapes.definitions import LineShapes

    on_long = lambda n: abs(n.y) < _TOL or abs(n.y - PLATE_STRIP_WIDTH) < _TOL  # noqa: E731
    beam_node_ids = {n.id for el in fem.elements if isinstance(el.type, LineShapes) for n in el.nodes}

    return (
        ("EDGE_X0", [n for n in fem.nodes if on_x0(n)], (1, 2, 3), "pinned: the support that locates the strip"),
        ("EDGE_X1", [n for n in fem.nodes if on_x1(n)], (2, 3), "simply supported, free to slide along the span"),
        (
            "EDGE_LONG",
            [n for n in fem.nodes if on_long(n) and not (on_x0(n) or on_x1(n))],
            (2, 4),
            "cylindrical bending: no transverse displacement, no roll",
        ),
        (
            "CORNERS",
            [n for n in fem.nodes if on_long(n) and (on_x0(n) or on_x1(n))],
            (4,),
            "the roll restraint only; u2 and u3 are already held by the edge sets",
        ),
        (
            "DRILLING",
            [n for n in fem.nodes if n.id not in beam_node_ids],
            (6,),
            "no rotation about the plate normal: it carries no load in the bare plate, and Sestra's "
            "FQUS gives it next to no stiffness, so left free it is a 0.01 Hz mechanism mode. Not on "
            "a stiffener's nodes: there it is the bar's own lateral bending, which is real",
        ),
    )


def design_plate_strip(*, stiffened: bool = False) -> ada.Assembly:
    """The strip as geometry. Pure design, no FEM.

    ``stiffened`` adds a T-profile bar along the centreline. That variant has no closed form -- a
    parallel-spring estimate is not one -- so it is a cross-solver agreement case, and it is also
    what exercises the T section through the deck writers.
    """
    from ada.materials.metals import CarbonSteel

    mat = ada.Material("S355", CarbonSteel("S355"))
    outline = [
        (0.0, 0.0),
        (PLATE_STRIP_LENGTH, 0.0),
        (PLATE_STRIP_LENGTH, PLATE_STRIP_WIDTH),
        (0.0, PLATE_STRIP_WIDTH),
    ]
    objects: list = [ada.Plate(PLATE_NAME, outline, PLATE_STRIP_THICKNESS, mat=mat)]
    if stiffened:
        y = PLATE_STRIP_WIDTH / 2
        objects.append(
            ada.Beam(
                PLATE_BAR_NAME,
                (0.0, y, 0.0),
                (PLATE_STRIP_LENGTH, y, 0.0),
                ada.Section("TBar", from_str="TG200x100x8x12"),
                mat,
            )
        )
    part = ada.Part(PLATE_PART_NAME) / objects
    return ada.Assembly("StripSite") / part


def mesh_plate_strip(
    a: ada.Assembly,
    *,
    elem_order: int = 1,
    use_quads: bool = True,
    mesh_size: float = 0.125,
) -> ada.Assembly:
    """Mesh the strip as shells and apply its supports. Returns the same Assembly.

    ``use_quads`` because the closed form is a plate's: a triangulated mesh is a different
    discretisation of a different element family, which is a thing to compare rather than to
    substitute. ``"line"`` is the *beam* representation -- the plate is a shell either way -- so a
    stiffener stays a line element sharing the shells' nodes.
    """
    p = a.get_part(PLATE_PART_NAME)
    p.fem = p.to_fem_obj(
        mesh_size,
        "line",
        use_quads=use_quads,
        options=GmshOptions(Mesh_ElementOrder=elem_order),
        interactive=False,
    )
    fem = p.fem

    for name, nodes, dofs, why in _plate_support_groups(fem):
        if not nodes:
            raise ValueError(f"support set {name} ({why}) matched no node at seed {mesh_size}")
        fem_set = fem.add_set(ada.fem.FemSet(name, sorted(nodes, key=lambda n: n.id), "nset", parent=fem))
        fem.add_bc(ada.fem.Bc(name, fem_set, list(dofs)))

    return a


def plate_shell_surface(a: ada.Assembly, *, negative_face: bool = False):
    """The plate's shells as an element set, and a ``Surface`` naming one of their two faces."""
    from ada.fem import FemSet, Surface
    from ada.fem.shapes.definitions import ShellShapes

    fem = a.get_part(PLATE_PART_NAME).fem
    shells = sorted((el for el in fem.elements if isinstance(el.type, ShellShapes)), key=lambda el: el.id)
    if not shells:
        raise ValueError("the strip meshed with no shell elements, so there is no surface to load")
    elset = fem.add_set(FemSet(PLATE_ELSET_NAME, shells, FemSet.TYPES.ELSET, parent=fem))
    return fem.add_surface(
        Surface(
            PLATE_SURFACE_NAME,
            Surface.TYPES.ELEMENT,
            elset,
            el_face_index=-1 if negative_face else None,
            parent=fem,
        )
    )


def run_plate_pressure(
    a: ada.Assembly,
    *,
    fem_format: str | FEA,
    scratch_dir: pathlib.Path,
    name: str,
    pressure: float = PLATE_STRIP_PRESSURE,
    negative_face: bool = False,
    overwrite: bool = True,
    execute: bool = True,
) -> "FEAResult | None":
    """Add a static step carrying a uniform pressure on the plate, and invoke the solver."""
    from ada.fem.loads import LoadPressure

    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    surface = plate_shell_surface(a, negative_face=negative_face)
    step = a.fem.add_step(ada.fem.StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    step.add_load(LoadPressure("q", pressure, surface))
    return _invoke_solver(
        a, name=name, fem_format=fem_format, scratch_dir=scratch_dir, overwrite=overwrite, execute=execute
    )


def run_plate_eig(
    a: ada.Assembly,
    *,
    fem_format: str | FEA,
    scratch_dir: pathlib.Path,
    name: str,
    eigen_modes: int = 6,
    overwrite: bool = True,
    execute: bool = True,
) -> "FEAResult | None":
    """Add an eigen step to the meshed strip and invoke the solver."""
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    a.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=eigen_modes))
    return _invoke_solver(
        a, name=name, fem_format=fem_format, scratch_dir=scratch_dir, overwrite=overwrite, execute=execute
    )


def plate_flexural_rigidity() -> float:
    """``D = E t^3 / (12 (1 - nu^2))``, the strip's plate rigidity per unit width."""
    return PLATE_E * PLATE_STRIP_THICKNESS**3 / (12.0 * (1.0 - PLATE_NU**2))


def plate_closed_form_deflection(pressure: float = PLATE_STRIP_PRESSURE) -> float:
    """``5 q L^4 / (384 D)``: mid-span deflection of a simply supported strip in cylindrical
    bending, as a positive magnitude. The long-edge constraint is what makes the strip cylindrical,
    so this is the reference and not an approximation of a two-way plate."""
    return 5.0 * pressure * PLATE_STRIP_LENGTH**4 / (384.0 * plate_flexural_rigidity())


def plate_closed_form_frequencies(n_modes: int = 6) -> list[float]:
    """``f_n = n^2 pi / (2 L^2) sqrt(D / (rho t))``, hertz.

    The simply supported beam frequencies with the beam's ``EI / (rho A)`` replaced by the strip's
    ``D / (rho t)`` -- the same substitution that turns the beam deflection into the one above, and
    valid for the same reason: the long-edge constraint admits only cylindrical modes.
    """
    import math

    base = (
        math.pi
        / (2.0 * PLATE_STRIP_LENGTH**2)
        * math.sqrt(plate_flexural_rigidity() / (PLATE_RHO * PLATE_STRIP_THICKNESS))
    )
    return [n**2 * base for n in range(1, n_modes + 1)]


def plate_case_name(fem_format: str | FEA, analysis: str, elem_order: int, stiffened: bool) -> str:
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    short = SHORT_NAME_MAP.get(fem_format.value, fem_format.value)
    return f"plate_{analysis}_{short}_shell_o{elem_order}_st{stiffened}"


def is_plate_skip(*, fem_format: str | FEA, analysis: str, elem_order: int, stiffened: bool) -> bool:
    """True for a (solver, case) combination that cannot run, with the reason in a comment."""
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format

    # Code_Aster's shell modelisation here is DKT/DKQ, which is first order only.
    if fem_format == FEA.CODE_ASTER and elem_order == 2:
        return True
    # A T stiffener reaches Calculix as a general section on a `U1` user element (CalculiX' beam
    # library has no I or T keyword), and a U1 sharing its nodes with shells is more than that
    # element can do: measured, ccx 2.23 stops at `*ERROR in gen3delem: first thickness in node 1 of
    # element 1 is zero` while expanding the mesh to 3D, for the static case as well as the eigen
    # one. adapy already gates U1 against gravity loads for a related reason
    # (`calculix.compatibility.check_compatibility`). So the stiffened plate is a case for the other
    # three solvers, and its table says so rather than showing an empty Calculix column.
    if fem_format == FEA.CALCULIX and stiffened:
        return True
    return False
