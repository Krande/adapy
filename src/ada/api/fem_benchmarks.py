"""The NAFEMS linear elastic benchmarks LE1 and LE10, and the closed form of a cantilever's bending modes.

LE1 and LE10 share one plan: a quarter of the region between two confocal ellipses. Because they are confocal, elliptic
coordinates map it exactly onto a rectangle, so the mesh here is written directly as structured 20-node hexahedra, with
straight, orthogonal element sides and every node on the true geometry. A mesher would add nothing to that and would
make the error depend on its choices. What is being verified is the route from an adapy FEM model through each solver's
deck writer, including the pressure records and the solid elements.

Published targets (The Standard NAFEMS Benchmarks, TNSB Rev. 3), at point D on the inner ellipse at ``(2, 0)``:

* LE1, elliptic membrane: tangential stress ``sigma_yy = 92.7 MPa`` under 10 MPa outward on the outer edge.
* LE10, thick plate: ``sigma_yy = -5.38 MPa`` at the top of D under 1 MPa on the upper face.

LE1 is defined in plane stress. It is modelled here as one layer of solid elements over half the thickness, with a
symmetry plane at mid-thickness and free faces, which puts no through-thickness stress in the model and needs nothing
from a solver beyond what LE10 does.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

import ada
from ada.api.fem_tasks import SHORT_NAME_MAP, _invoke_solver, design_cantilever
from ada.fem.formats.general import FEATypes as FEA

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult

NAFEMS_PART_NAME = "NafemsLE"
NAFEMS_ELSET_NAME = "NAFEMS_SOLIDS"
NAFEMS_SURFACE_NAME = "NAFEMS_LOADED"
NAFEMS_E = 210.0e9
NAFEMS_NU = 0.3

#: The ellipses' shared focal distance: ``2^2 - 1^2 = 3.25^2 - 2.75^2 = 3``.
_FOCAL = math.sqrt(3.0)
_MU_INNER = math.asinh(1.0 / _FOCAL)
_MU_OUTER = math.asinh(2.75 / _FOCAL)


@dataclass(frozen=True)
class NafemsCase:
    """``z_min`` and ``height`` are the modelled extent, ``pressure`` follows the writers' sign: positive pushes into
    the face. ``layers_per_division`` is how many element layers each radial division gets (0: always one layer)."""

    name: str
    title: str
    z_min: float
    height: float
    pressure: float
    target_pa: float
    layers_per_division: int


LE1 = NafemsCase("LE1", "Elliptic membrane", 0.0, 0.05, -10.0e6, 92.7e6, 0)
LE10 = NafemsCase("LE10", "Thick plate under pressure", -0.3, 0.6, 1.0e6, -5.38e6, 1)
NAFEMS_CASES = {case.name: case for case in (LE1, LE10)}


def _position(
    case: NafemsCase, i: int, j: int, k: int, radial: int, around: int, layers: int
) -> tuple[float, float, float]:
    """Where a node goes. Indices count half elements, so ``i`` runs ``0..2*radial``."""
    mu = _MU_INNER + (_MU_OUTER - _MU_INNER) * i / (2 * radial)
    nu = (math.pi / 2) * j / (2 * around)
    return (
        _FOCAL * math.cosh(mu) * math.cos(nu),
        _FOCAL * math.sinh(mu) * math.sin(nu),
        case.z_min + case.height * k / (2 * layers),
    )


def _dofs(case: NafemsCase, i: int, j: int, k: int, radial: int, around: int, layers: int) -> tuple[int, ...]:
    """The translations a node has held. j runs from y = 0 (j = 0) to x = 0 (j = 2 * around)."""
    held: set[int] = set()
    if j == 2 * around:
        held.add(1)
    if j == 0:
        held.add(2)
    if case is LE1:
        if k == 0:
            held.add(3)  # the symmetry plane at mid-thickness
    elif i == 2 * radial:
        held |= {1, 2}  # LE10: the outer face moves only through the thickness...
        if k == layers:
            held.add(3)  # ...and its mid-plane not even that
    return tuple(sorted(held))


def design_nafems_le(case: str | NafemsCase, segments: int = 48) -> ada.Assembly:
    """The benchmark's geometry as an ``ada.Plate``, at its full thickness, for showing what the model is.

    Each ellipse is drawn with ``segments`` straight sides. The analysis uses the exact curves (:func:`mesh_nafems_le`).
    """
    from ada.materials.metals import CarbonSteel

    case = NAFEMS_CASES[case] if isinstance(case, str) else case
    angles = [(math.pi / 2) * i / segments for i in range(segments + 1)]
    inner = [(2.0 * math.cos(a), math.sin(a)) for a in angles]
    outer = [(3.25 * math.cos(a), 2.75 * math.sin(a)) for a in angles]
    thickness = 2 * case.height if case is LE1 else case.height  # LE1 is modelled over half its thickness
    material = ada.Material("NafemsSteel", CarbonSteel("S355", E=NAFEMS_E, v=NAFEMS_NU))
    plate = ada.Plate(case.name, [*inner, *reversed(outer)], thickness, mat=material)
    return ada.Assembly("NafemsSite") / (ada.Part(NAFEMS_PART_NAME) / plate)


def mesh_nafems_le(case: str | NafemsCase, divisions: int) -> ada.Assembly:
    """The benchmark as a model: ``divisions`` elements radially, twice that around the quarter ellipse.

    Each node's restraints are collected first and written as one boundary condition per distinct set of dofs. One set
    per face would hold a dof twice along the edges where faces meet, which Code_Aster refuses (``<ASSEMBLA_26>``).
    """
    from ada.fem import Bc, Elem, FemSection, FemSet
    from ada.fem.shapes.definitions import SolidShapes
    from ada.fem.surfaces import create_surface_from_nodes
    from ada.materials.metals import CarbonSteel

    case = NAFEMS_CASES[case] if isinstance(case, str) else case
    radial, around = divisions, 2 * divisions
    layers = case.layers_per_division * divisions or 1
    material = ada.Material("NafemsSteel", CarbonSteel("S355", E=NAFEMS_E, v=NAFEMS_NU))
    part = ada.Part(NAFEMS_PART_NAME)
    fem = part.fem

    nodes: dict[tuple[int, int, int], ada.Node] = {}
    for k in range(2 * layers + 1):
        for j in range(2 * around + 1):
            for i in range(2 * radial + 1):
                if i % 2 + j % 2 + k % 2 < 2:  # a serendipity element has no face or body nodes
                    position = _position(case, i, j, k, radial, around, layers)
                    nodes[i, j, k] = fem.nodes.add(ada.Node(position, len(nodes) + 1))

    elements = []
    for k in range(0, 2 * layers, 2):
        for j in range(0, 2 * around, 2):
            for i in range(0, 2 * radial, 2):
                at = [
                    (0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0), (0, 0, 2), (2, 0, 2), (2, 2, 2), (0, 2, 2),
                    (1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0), (1, 0, 2), (2, 1, 2), (1, 2, 2), (0, 1, 2),
                    (0, 0, 1), (2, 0, 1), (2, 2, 1), (0, 2, 1),
                ]  # fmt: skip
                connectivity = [nodes[i + a, j + b, k + c] for a, b, c in at]
                elements.append(fem.add_elem(Elem(len(elements) + 1, connectivity, SolidShapes.HEX20)))

    elset = fem.add_set(FemSet(NAFEMS_ELSET_NAME, elements, "elset"))
    fem.add_section(FemSection("NafemsSection", "solid", elset, material))
    part.add_material(material)

    groups: dict[tuple[int, ...], list] = {}
    for (i, j, k), node in nodes.items():
        held = _dofs(case, i, j, k, radial, around, layers)
        if held:
            groups.setdefault(held, []).append(node)
    for held, members in groups.items():
        name = "BC_" + "".join(str(dof) for dof in held)
        fem_set = fem.add_set(FemSet(name, members, "nset", parent=fem))
        fem.add_bc(Bc(name, fem_set, list(held)))

    loaded = [node for (i, _, k), node in nodes.items() if (i == 2 * radial if case is LE1 else k == 2 * layers)]
    fem.add_surface(create_surface_from_nodes(NAFEMS_SURFACE_NAME, loaded, fem))

    assembly = ada.Assembly("NafemsSite") / part
    point = nodes[0, 0, 0 if case is LE1 else 2 * layers]
    assembly.metadata["nafems"] = {
        "case": case.name,
        "divisions": divisions,
        "n_elements": len(elements),
        "point_d": (float(point.x), float(point.y), float(point.z)),
    }
    return assembly


def add_nafems_load(assembly: ada.Assembly) -> None:
    """The static step, carrying the benchmark's pressure."""
    from ada.fem.loads import LoadPressure

    case = NAFEMS_CASES[assembly.metadata["nafems"]["case"]]
    surface = assembly.get_part(NAFEMS_PART_NAME).fem.surfaces[NAFEMS_SURFACE_NAME]
    step = assembly.fem.add_step(
        ada.fem.StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1)
    )
    step.add_load(LoadPressure("q", case.pressure, surface))
    # The stress at the element nodes as well: Abaqus writes none at the nodes themselves, so its value at D is the
    # average of the elements' own extrapolations there. Formats without such an output ignore it.
    step.field_outputs[0].element_nodal.append("S")


def run_nafems_le(
    assembly: ada.Assembly,
    *,
    fem_format: str | FEA,
    scratch_dir: pathlib.Path,
    name: str,
    overwrite: bool = True,
    execute: bool = True,
) -> "FEAResult | None":
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    add_nafems_load(assembly)
    return _invoke_solver(
        assembly, name=name, fem_format=fem_format, scratch_dir=scratch_dir, overwrite=overwrite, execute=execute
    )


def nafems_case_name(fem_format: str | FEA, case: str, divisions: int) -> str:
    fem_format = FEA.from_str(fem_format) if isinstance(fem_format, str) else fem_format
    return f"nafems_{case}_{SHORT_NAME_MAP.get(fem_format.value, fem_format.value)}_n{divisions}"


#: What the solvers' readers call the stress on planes normal to global y.
_SYY_COMPONENTS = ("SYY", "SIYY", "S22", "SIGYY")


def stress_yy_nodes(result):
    """``(coords, sigma_yy)`` for every node, from nodal stresses or from element-node stresses, in pascals.

    Element-node stresses are averaged over the elements sharing a node, which is what a solver's own nodal stress is. A
    node with no value is NaN. Integration-point fields are not accepted, because they are not at the nodes.
    """
    import numpy as np

    from ada.fem.results.field_data import ElementFieldData, FieldPosition, NodalFieldData

    if hasattr(result, "to_fea_result"):
        result = result.to_fea_result()
    coords = np.asarray(result.mesh.nodes.coords, dtype=float)

    def column(field) -> int | None:
        return next((i for i, c in enumerate(field.components) if c.upper() in _SYY_COMPONENTS), None)

    fields = [f for f in result.results if column(f) is not None]
    nodal = [f for f in fields if isinstance(f, NodalFieldData)]
    if nodal:
        # The last one: a reader that keeps the step's base state lists it first.
        return coords, np.asarray(nodal[-1].values, dtype=float)[:, column(nodal[-1]) + 1]

    index_of = {int(node): i for i, node in enumerate(result.mesh.nodes.identifiers)}
    for field in sorted((f for f in fields if isinstance(f, ElementFieldData)), key=lambda f: "SIGM" not in f.name):
        values = np.asarray(field.values, dtype=float)
        total, count = np.zeros(len(coords)), np.zeros(len(coords), dtype=int)
        if values.ndim == 2 and field.field_pos == FieldPosition.ELEMENT_NODAL:
            # Rows of (element, its node's place in the connectivity from 1, components...).
            refs_by_element = {
                int(label): (group, row)
                for group in result.mesh.elements
                for label, row in zip(group.identifiers, np.asarray(group.node_refs))
            }
            for element, place, value in zip(
                values[:, 0].astype(int), values[:, 1].astype(int), values[:, 2 + column(field)]
            ):
                group, row = refs_by_element[element]
                ref = row[place - 1]
                node = int(ref) if group.node_refs_are_indices else index_of[int(ref)]
                total[node] += value
                count[node] += 1
        offset = 0
        for group in result.mesh.elements:
            refs = np.asarray(group.node_refs)
            if values.ndim == 3 and refs.shape[1] == values.shape[1]:
                for element, row in enumerate(refs):
                    for position, ref in enumerate(row):
                        node = int(ref) if group.node_refs_are_indices else index_of[int(ref)]
                        total[node] += values[offset + element, position, column(field)]
                        count[node] += 1
            offset += len(refs)
        if count.any():
            with np.errstate(invalid="ignore"):
                return coords, np.where(count > 0, total / count, np.nan)
    raise ValueError(f"no nodal or element-node sigma_yy among {[(f.name, f.components) for f in result.results]}")


def stress_yy_at(result, point: Sequence[float], tolerance: float = 1e-6) -> float:
    """``sigma_yy`` in pascals at the node at ``point``, found by position because each solver renumbers."""
    import numpy as np

    coords, values = stress_yy_nodes(result)
    distance = np.linalg.norm(coords - np.asarray(point, dtype=float), axis=1)
    index = int(np.argmin(distance))
    if distance[index] > tolerance:
        raise ValueError(f"no node at {tuple(point)}; the closest is {distance[index]!r} away")
    return float(values[index])


def stress_yy_on_plane(result, z: float, tolerance: float = 1e-6) -> dict[str, list[float]]:
    """``{"x", "y", "syy"}`` for the nodes in the plane at height ``z``, small enough to keep with a cached result."""
    import numpy as np

    coords, values = stress_yy_nodes(result)
    keep = (np.abs(coords[:, 2] - z) < tolerance) & np.isfinite(values)
    return {"x": coords[keep, 0].tolist(), "y": coords[keep, 1].tolist(), "syy": values[keep].tolist()}


#: ``beta_n L`` of a clamped-free Euler-Bernoulli beam, the first roots of ``cos x cosh x = -1``.
CANTILEVER_BETA_L = (1.875104, 4.694091, 7.854757, 10.995541, 14.137168)


def cantilever_bending_frequencies(modes: int = 2) -> dict[str, list[float]]:
    """``f_n = (beta_n L)^2 / (2 pi L^2) sqrt(E I / (rho A))``, hertz, keyed by the global direction the tip moves in.

    The beam runs along global x with its web along z, so ``"Z"`` bends about the strong axis and ``"Y"`` about the weak
    one. Euler-Bernoulli ignores shear deformation and rotary inertia. At this beam's length to depth of 7.5 both matter,
    so a correct solver with those effects comes out below this, by more in the higher modes. It is a reference for the
    trend and the size of the gap, not a target to reach.

    Two modes per direction by default. Past that, shell and solid models have local modes of the web that also move
    mainly in Y, so taking "the third Y mode" stops meaning the third bending mode of the beam.
    """
    beam = next(b for b in design_cantilever().get_part("MyPart").get_all_physical_objects() if b.name == "MyBeam")
    properties = beam.section.properties
    length = float(beam.length)
    steel = beam.material.model
    return {
        direction: [
            beta**2 / (2.0 * math.pi * length**2) * math.sqrt(steel.E * inertia / (steel.rho * properties.Ax))
            for beta in CANTILEVER_BETA_L[:modes]
        ]
        for direction, inertia in (("Y", properties.Iz), ("Z", properties.Iy))
    }
