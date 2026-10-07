"""Concept loads -> FE load cases of one static step on a meshed FEM.

Until this existed a part's concept loads (``Part.concept_fem.loads``, which the GeniE XML reader fills) reached no
FE model: meshing made no FE load of them and no step, the Sesam deck written from a GeniE model carried no load
record, and nothing said so. Each kind now becomes what GeniE meshes it into -- its own ``T1.FEM`` of the loads
fixture (``files/fem_files/sesam/genie_loads_all_kinds.xml`` and ``..._T1.FEM``) is the oracle, compared case by
case in ``tests/core/fem/test_concept_loads_to_fem.py``:

* a concept load case -> an FE :class:`~ada.fem.LoadCase` of the same name in one static step
  (:data:`CONCEPT_STEP_NAME`), in GeniE's order (``fem_loadcase_number``). The Sesam writer writes each as a TDLOAD,
  the Abaqus writer as a ``*Load Case`` of a linear perturbation step. A prescribed displacement stays with its
  support (:mod:`.to_fem`), which names its load case.
* :class:`LoadConceptPoint` -> a point force and moment on the mesh node at its position (GeniE: BNLOAD). Meshing
  makes that node (``embed_points``); GeniE instead wrote a point off its mesh nodes as a BELOAD1 LOTYP 2 over 5 mm.
* :class:`LoadConceptLine` -> a :class:`~ada.fem.LoadLine` on the beam elements under it, each a segment with the
  intensity at the start and end of its loaded stretch and the unloaded lengths L1, L2 at its ends (GeniE: BELOAD1
  per element, end values exactly linear, L1 = 0.3 and L2 = 0.1 on the end elements of a 1.3..2.9 m load). With no
  beam under it, on the shell element edges under it (GeniE: BELLO2). A line along a beam is the beam's, as GeniE
  resolves it.
* :class:`LoadConceptSurface` on a plate -> a :class:`~ada.fem.LoadPressure` on the plate's shell elements, each on
  the face that is the named side of the *plate*: ``front`` is the face on the plate's normal and a positive
  pressure pushes into it (GeniE: 1000 Pa front on a +z plate gave Fz = -4000 on 4 m2). An element wound against
  the plate's normal takes it on its own negative face.
* :class:`LoadConceptAccelerationField` with self-weight -> a gravity load of that acceleration (GeniE: BGRAV).
* a :class:`LoadConceptCaseCombination` -> one more FE load case holding each of its cases' loads (and settlements)
  times its factor and the global scale factor: exact for the linear analysis both formats run. GeniE writes no
  record for a combination; Sestra solves this one as a case of its own.

Whatever does not become an FE load -- a polygon pressure, a rotational field, an acceleration without self-weight,
a line partly over nothing, a point with no node -- is a named finding in :mod:`ada.fem.formats.conversion_report`
under :data:`STAGE`, as is a part whose concept loads reach a writer without having been converted at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.api.transforms import to_global_points, to_global_vectors
from ada.fem.concept.to_fem import STAGE, _element_nodes, report

if TYPE_CHECKING:
    from ada import FEM, Assembly, Part
    from ada.fem import Elem, Load
    from ada.fem.concept.loads import (
        LoadConceptCase,
        LoadConceptCaseCombination,
        LoadConceptLine,
        LoadConceptPoint,
        LoadConceptSurface,
    )
    from ada.fem.loads import LineLoadSegment

#: The static step the concept load cases become load cases of.
CONCEPT_STEP_NAME = "concept_loads"

#: ``Step.metadata`` key: ``{part name: [load case names]}`` the step was converted from.
CONVERTED_KEY = "concept_load_cases"


def concept_load_cases(part: Part) -> list[LoadConceptCase]:
    """The load cases of ``part`` and its sub-parts, in GeniE's order (``fem_loadcase_number``, then as read)."""
    cases = [lc for p in part.get_all_subparts(include_self=True) for lc in p.concept_fem.loads.load_cases.values()]
    return sorted(cases, key=lambda lc: lc.fem_loadcase_number)


def concept_load_points(part: Part) -> list[np.ndarray]:
    """The global position of every concept point load of ``part``, for a mesh node there."""
    from ada.fem.concept.loads import LoadConceptPoint

    out = []
    for lc in concept_load_cases(part):
        owner = _owner(lc, part)
        out += [to_global_points(owner, ld.position) for ld in lc.loads if isinstance(ld, LoadConceptPoint)]
    return out


def _owner(lc: LoadConceptCase, part: Part) -> Part:
    """The part a load case belongs to: its positions are in that part's local system."""
    loads = getattr(lc, "parent", None)
    concept = getattr(loads, "parent_fem", None)
    return getattr(concept, "parent_part", None) or part


def add_load_concepts_to_fem(part: Part, fem: FEM, tol: float = 1e-4) -> None:
    """Convert the concept load cases and combinations of ``part`` (and its sub-parts) into FE load cases of one
    static step on ``fem``, which ``part``'s geometry was meshed into. See the module docstring."""
    from ada.fem import LoadCase
    from ada.fem.steps import StepImplicitStatic

    cases = concept_load_cases(part)
    combinations = [
        lcc
        for p in part.get_all_subparts(include_self=True)
        for lcc in p.concept_fem.loads.load_case_combinations.values()
    ]
    if not cases and not combinations:
        return

    conv = _Converter(fem, tol)
    step = fem.add_step(StepImplicitStatic(CONCEPT_STEP_NAME))
    converted: dict[str, list[str]] = {}
    fe_loads: dict[int, list[Load]] = {}
    for lc in cases:
        owner = _owner(lc, part)
        loads = [fe for load in lc.loads for fe in conv.load(load, lc, owner)]
        fe_loads[id(lc)] = loads
        if not loads and not conv.settles(lc):
            report().note(STAGE, "LoadConceptCase", lc.name, "a load case with no load in it is written empty")
        step.add_loadcase(LoadCase(lc.name, None, loads=loads))
        converted.setdefault(owner.name, []).append(lc.name)

    for lcc in combinations:
        loads = conv.combination(lcc, fe_loads)
        if loads is None:
            continue
        step.add_loadcase(LoadCase(lcc.name, None, loads=loads))
        converted.setdefault(part.name, []).append(lcc.name)
        report().note(
            STAGE,
            "LoadConceptCaseCombination",
            lcc.name,
            "written as a load case of its own holding its cases' loads times their factors, which is the "
            "combination for a linear analysis; GeniE writes no record for a combination",
            factors={t.load_case.name: t.factor * lcc.global_scale_factor for t in lcc.load_cases},
        )
    step.metadata[CONVERTED_KEY] = converted


def report_unconverted_concept_loads(assembly: Assembly) -> None:
    """Name every concept load case that reaches a writer without having become an FE load case: a part whose FEM
    was not made by ``Part.to_fem_obj`` (or was made before its loads were added) carries none of them."""
    converted: set[tuple[str, str]] = set()
    for p in assembly.get_all_parts_in_assembly(include_self=True):
        for step in p.fem.steps:
            for owner, names in (step.metadata or {}).get(CONVERTED_KEY, {}).items():
                converted.update((owner, n) for n in names)
    for p in assembly.get_all_parts_in_assembly(include_self=True):
        concept = p.concept_fem.loads
        for name in list(concept.load_cases) + list(concept.load_case_combinations):
            if (p.name, name) not in converted:
                report().omitted(
                    STAGE,
                    "LoadConceptCase",
                    f"{name} of part {p.name}",
                    "a concept load case that no FE step carries -- the part's FEM was not meshed from it by "
                    "Part.to_fem_obj -- so none of its loads is written",
                )


class _Converter:
    def __init__(self, fem: FEM, tol: float):
        self.fem = fem
        self.tol = tol
        self.nodes = _element_nodes(fem)
        self.coords = np.array([n.p for n in self.nodes], dtype=float).reshape(-1, 3)
        self.names: set[str] = set()

    def _name(self, base: str) -> str:
        name, i = base, 2
        while name in self.names or name in self.fem.sets.nodes or name in self.fem.sets.elements:
            name, i = f"{base}_{i}", i + 1
        self.names.add(name)
        return name

    def settles(self, lc: LoadConceptCase) -> bool:
        from ada.fem.constraints import BC_LOAD_CASE

        return any((bc.metadata or {}).get(BC_LOAD_CASE) == lc.name for bc in self.fem.bcs)

    def load(self, load, lc: LoadConceptCase, owner: Part) -> list[Load]:
        from ada.fem.concept.loads import (
            LoadConceptAccelerationField,
            LoadConceptLine,
            LoadConceptPoint,
            LoadConceptPrescribedDisplacement,
            LoadConceptSurface,
        )

        subject = f"{load.name} in load case {lc.name}"
        if isinstance(load, LoadConceptPrescribedDisplacement):
            return []  # the support's, per load case (to_fem)
        if isinstance(load, LoadConceptPoint):
            return self.point(load, lc, owner, subject)
        if isinstance(load, LoadConceptLine):
            return self.line(load, lc, owner, subject)
        if isinstance(load, LoadConceptSurface):
            return self.surface(load, lc, subject)
        if isinstance(load, LoadConceptAccelerationField):
            return self.acceleration(load, lc, subject)
        report().omitted(STAGE, type(load).__name__, subject, "no FE load is made of this kind of concept load")
        return []

    # --- point --------------------------------------------------------------------------------------------------

    def point(self, load: LoadConceptPoint, lc: LoadConceptCase, owner: Part, subject: str) -> list[Load]:
        from ada.fem import FemSet, Load
        from ada.fem.concept.to_fem import _is_solid_only

        pos = to_global_points(owner, load.position)
        found = self._nodes_at(pos)
        if not found:
            report().omitted(
                STAGE, "LoadConceptPoint", subject, "the mesh has no node at its position, so it acts on nothing"
            )
            return []
        if len(found) > 1:
            report().suspect(
                STAGE,
                "LoadConceptPoint",
                subject,
                "the mesh has several unmerged nodes at its position; the load acts on the first",
                nodes=[n.id for n in found],
            )
        node = found[0]
        values = [float(v) for v in (*_vec(owner, load.force), *_vec(owner, load.moment))]
        if _is_solid_only(node) and any(values[3:]):
            report().omitted(
                STAGE,
                "LoadConceptPoint",
                subject,
                "a moment on a node of solid elements only, which has no rotation for it to act on; the force is kept",
                moment=values[3:],
            )
            values[3:] = [0.0, 0.0, 0.0]
        if not any(values):
            return []
        fem_set = self.fem.add_set(FemSet(self._name(f"{lc.name}_{load.name}"), [node], FemSet.TYPES.NSET))
        return [Load(f"{lc.name}_{load.name}", Load.TYPES.FORCE, 1.0, fem_set=fem_set, dof=values)]

    def _nodes_at(self, pos) -> list:
        if len(self.coords) == 0:
            return []
        dist = np.linalg.norm(self.coords - np.asarray(pos, dtype=float), axis=1)
        return sorted((self.nodes[i] for i in np.flatnonzero(dist <= self.tol)), key=lambda n: n.id)

    # --- line ---------------------------------------------------------------------------------------------------

    def line(self, load: LoadConceptLine, lc: LoadConceptCase, owner: Part, subject: str) -> list[Load]:
        from ada.fem import LoadLine

        start, end = to_global_points(owner, [load.start_point, load.end_point])
        q1, q2 = _vec(owner, load.intensity_start), _vec(owner, load.intensity_end)
        length = float(np.linalg.norm(end - start))
        if length <= self.tol:
            report().omitted(STAGE, "LoadConceptLine", subject, "a line load of zero length")
            return []

        segments, covered = self._beam_segments(start, end, q1, q2)
        on = "beam elements"
        if not segments:
            segments, covered, partial = self._edge_segments(start, end, q1, q2)
            on = "shell element edges"
            if partial:
                report().omitted(
                    STAGE,
                    "LoadConceptLine",
                    subject,
                    "it ends inside a shell element edge, and a shell edge load (BELLO2) loads a whole edge; the "
                    "part of it on those edges is not written",
                    edges=partial,
                )
        if not segments:
            report().omitted(
                STAGE, "LoadConceptLine", subject, "no beam element or shell element edge lies along it in the mesh"
            )
            return []
        missing = length - covered
        if missing > self.tol:
            report().omitted(
                STAGE,
                "LoadConceptLine",
                subject,
                f"part of its length has no {on} under it, and that part of the load is not written",
                length=length,
                not_covered=missing,
            )
        return [LoadLine(f"{lc.name}_{load.name}", segments)]

    def _along(self, start, end, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Each point's position along ``start``..``end`` and whether it lies on that line."""
        axis = (end - start) / np.linalg.norm(end - start)
        rel = points - start
        s = rel @ axis
        dist = np.linalg.norm(rel - np.outer(s, axis), axis=1)
        return s, dist <= self.tol

    def _beam_segments(self, start, end, q1, q2):
        from ada.fem.shapes.definitions import LineShapes

        elems = [el for el in self.fem.elements if el.type == LineShapes.LINE]
        return self._segments(start, end, q1, q2, [(el, None, el.nodes[0], el.nodes[1]) for el in elems])

    def _edge_segments(self, start, end, q1, q2):
        from ada.fem.loads.fe_loads import _corner_count
        from ada.fem.shapes.definitions import ShellShapes

        edges = []
        for el in self.fem.elements:
            if el.type not in (ShellShapes.TRI, ShellShapes.QUAD):
                continue
            n = _corner_count(el)
            edges += [(el, i, el.nodes[i - 1], el.nodes[i % n]) for i in range(1, n + 1)]
        segments, covered = self._segments(start, end, q1, q2, edges)
        whole = [s for s in segments if s.l1 == 0.0 and s.l2 == 0.0]
        partial = sorted(f"{s.elem.id}:{s.edge}" for s in segments if s.l1 != 0.0 or s.l2 != 0.0)
        covered -= sum(float(np.linalg.norm(np.subtract(*s.loaded_ends()))) for s in segments if s not in whole)
        return whole, covered, partial

    def _segments(self, start, end, q1, q2, candidates) -> tuple[list[LineLoadSegment], float]:
        """The segments of the candidates ``(element, edge, node a, node b)`` lying along the line, one per node pair
        (the lowest element id where two share one: an interior plate edge), and the length they cover."""
        from ada.fem.loads import LineLoadSegment

        if not candidates:
            return [], 0.0
        length = float(np.linalg.norm(end - start))
        pa = np.array([c[2].p for c in candidates], dtype=float)
        pb = np.array([c[3].p for c in candidates], dtype=float)
        sa, on_a = self._along(start, end, pa)
        sb, on_b = self._along(start, end, pb)

        def q_at(s: float) -> tuple[float, float, float]:
            t = s / length
            return tuple(float(v) for v in (1 - t) * q1 + t * q2)

        by_pair: dict[tuple[int, int], LineLoadSegment] = {}
        intervals = []
        for i in sorted(np.flatnonzero(on_a & on_b), key=lambda k: candidates[k][0].id):
            el, edge, na, nb = candidates[i]
            lo, hi = max(min(sa[i], sb[i]), 0.0), min(max(sa[i], sb[i]), length)
            pair = tuple(sorted((na.id, nb.id)))
            if hi - lo <= self.tol or pair in by_pair:
                continue
            if sa[i] <= sb[i]:
                l1, l2, qa, qb = lo - sa[i], sb[i] - hi, q_at(lo), q_at(hi)
            else:
                l1, l2, qa, qb = sa[i] - hi, lo - sb[i], q_at(hi), q_at(lo)
            l1 = 0.0 if l1 <= self.tol else float(l1)
            l2 = 0.0 if l2 <= self.tol else float(l2)
            by_pair[pair] = LineLoadSegment(el, qa, qb, l1, l2, edge)
            intervals.append((lo, hi))
        return sorted(by_pair.values(), key=lambda s: (s.elem.id, s.edge or 0)), _union_length(intervals)

    # --- surface ------------------------------------------------------------------------------------------------

    def surface(self, load: LoadConceptSurface, lc: LoadConceptCase, subject: str) -> list[Load]:
        from ada.fem import FemSet, LoadPressure, Surface
        from ada.fem.shapes.definitions import ShellShapes

        plate = load.plate_ref
        if plate is None:
            report().omitted(
                STAGE,
                "LoadConceptSurface",
                subject,
                "a pressure over a polygon, which loads whatever elements it covers; GeniE smears it over whole "
                "elements, so its own result depends on the mesh; not written",
            )
            return []
        if load.side not in ("front", "back"):
            report().omitted(STAGE, "LoadConceptSurface", subject, f"side {load.side!r} is neither front nor back")
            return []
        elems = [el for el in self.fem.elements if plate in el.refs and isinstance(el.type, ShellShapes)]
        if not elems:
            report().omitted(
                STAGE,
                "LoadConceptSurface",
                subject,
                "its plate has no shell element in the mesh, so the pressure acts on nothing",
                plate=plate.name,
            )
            return []

        normal = to_global_vectors(plate, plate.poly.normal)
        normal = normal / np.linalg.norm(normal)
        # The face on the plate's normal is its front; a positive pressure pushes into the face it names.
        want = 1.0 if load.side == "front" else -1.0
        groups: dict[int, list[Elem]] = {1: [], -1: []}
        crossways = []
        for el in sorted(elems, key=lambda e: e.id):
            d = float(np.dot(_shell_normal(el), normal))
            if abs(d) < 1e-6:
                crossways.append(el.id)
                continue
            groups[1 if d * want > 0 else -1].append(el)
        if crossways:
            report().omitted(
                STAGE,
                "LoadConceptSurface",
                subject,
                "elements of the plate whose own normal is across the plate's; which face is which is not known",
                elements=crossways[:10],
                n_elements=len(crossways),
            )
        base = f"{lc.name}_{load.name}"
        sets, sides = [], []
        for side, members in groups.items():
            if members:
                tag = "pos" if side == 1 else "neg"
                sets.append(self.fem.add_set(FemSet(self._name(f"{base}_{tag}"), members, FemSet.TYPES.ELSET)))
                sides.append(side)
        if not sets:
            return []
        surface = self.fem.add_surface(
            Surface(self._name(f"{base}_surf"), Surface.TYPES.ELEMENT, sets, el_face_index=sides, parent=self.fem)
        )
        return [LoadPressure(base, float(load.pressure), surface)]

    # --- acceleration -------------------------------------------------------------------------------------------

    def acceleration(self, load, lc: LoadConceptCase, subject: str) -> list[Load]:
        from ada.fem import Load

        out = []
        if load.rotational_field is not None:
            report().omitted(
                STAGE,
                "RotationalAccelerationField",
                subject,
                "a rotational acceleration field has no FE load here; only the translational field is written",
            )
        if not load.include_self_weight:
            report().omitted(
                STAGE,
                "LoadConceptAccelerationField",
                subject,
                "an acceleration that does not act on the structure's own mass; an FE gravity load acts on all mass, "
                "so it is not written",
            )
            return out
        acc = np.asarray(load.acceleration, dtype=float)
        magnitude = float(np.linalg.norm(acc))
        if magnitude == 0.0:
            return out
        out.append(
            Load(f"{lc.name}_{load.name}", Load.TYPES.GRAVITY, magnitude, dof=[float(a) for a in acc / magnitude])
        )
        return out

    # --- combinations -------------------------------------------------------------------------------------------

    def combination(self, lcc: LoadConceptCaseCombination, fe_loads: dict[int, list[Load]]) -> list[Load] | None:
        from ada.fem import Bc
        from ada.fem.constraints import BC_LOAD_CASE

        for term in lcc.load_cases:
            if term.phase not in (0, 0.0):
                report().omitted(
                    STAGE,
                    "LoadConceptCaseCombination",
                    lcc.name,
                    "a term with a phase angle combines complex load cases; a static FE load case has no phase",
                    load_case=term.load_case.name,
                    phase=term.phase,
                )
                return None
            if id(term.load_case) not in fe_loads:
                report().omitted(
                    STAGE,
                    "LoadConceptCaseCombination",
                    lcc.name,
                    "it combines a load case of another part, which is not converted with this FEM",
                    load_case=term.load_case.name,
                )
                return None
        loads = []
        for term in lcc.load_cases:
            factor = float(term.factor) * float(lcc.global_scale_factor)
            for fe in fe_loads[id(term.load_case)]:
                loads.append(_scaled(fe, factor, f"{lcc.name}_{fe.name}"))
            for bc in [b for b in self.fem.bcs if (b.metadata or {}).get(BC_LOAD_CASE) == term.load_case.name]:
                magnitudes = [None if m is None else factor * float(m) for m in bc.magnitudes]
                self.fem.add_bc(
                    Bc(
                        f"{bc.name}_{lcc.name}",
                        bc.fem_set,
                        list(bc.dofs),
                        magnitudes=magnitudes,
                        metadata={BC_LOAD_CASE: lcc.name},
                    )
                )
        return loads


def _scaled(fe: Load, factor: float, name: str) -> Load:
    from ada.fem import Load, LoadLine, LoadPressure

    if isinstance(fe, LoadLine):
        return fe.scaled(factor, name)
    if isinstance(fe, LoadPressure):
        return LoadPressure(name, factor * float(fe.magnitude), fe.surface, fe.distribution)
    return Load(name, fe.type, factor * float(fe.magnitude), fem_set=fe.fem_set, dof=list(fe.dof))


def _vec(owner: Part, v) -> np.ndarray:
    """A concept load's components (GeniE: in its own system, the identity when it has none) in global axes."""
    return np.asarray(to_global_vectors(owner, np.asarray(v, dtype=float)), dtype=float)


def _shell_normal(el: Elem) -> np.ndarray:
    """The element's own normal, by the right-hand rule on its node order."""
    p = [np.asarray(n.p, dtype=float) for n in el.nodes]
    if len(p) >= 4 and el.type.name.startswith("QUAD"):
        return np.cross(p[2] - p[0], p[3] - p[1])
    return np.cross(p[1] - p[0], p[2] - p[0])


def _union_length(intervals: list[tuple[float, float]]) -> float:
    total, cur = 0.0, None
    for lo, hi in sorted(intervals):
        if cur is None or lo > cur[1]:
            if cur is not None:
                total += cur[1] - cur[0]
            cur = [lo, hi]
        else:
            cur[1] = max(cur[1], hi)
    return total + (cur[1] - cur[0] if cur is not None else 0.0)
