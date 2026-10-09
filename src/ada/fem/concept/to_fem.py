"""Concept supports -> FE boundary conditions, springs, couplings and settlements on a meshed FEM.

Each kind of support dof becomes what GeniE meshes it into. GeniE V8.13-02's own ``T1.FEM`` of the support
fixtures ``genie_supports_all_kinds.xml`` and ``genie_supports_frames.xml`` is the oracle, compared node by node
in ``tests/core/fem/test_concept_supports_to_fem.py``:

* ``fixed`` -> a :class:`~ada.fem.Bc` on those dofs (BNBCD 1).
* ``spring`` -> a spring to ground (:class:`~ada.fem.Spring`, ``SPRING1``) on each node, its stiffness on the
  diagonal at that dof (GeniE: BNBCD 0 there, and a GSPR element with MGSPRNG ``k``). A support curve's stiffness
  is per unit length, and each node takes ``k`` times its tributary length along the curve
  (:func:`tributary_lengths`; GeniE: 500000 N/m^2 gave 125000 at the end nodes and 250000 inside, at 0.5 m).
* ``prescribed`` -> held by the support's ``Bc``, and for every load case holding a
  :class:`~ada.fem.concept.loads.LoadConceptPrescribedDisplacement` on that support one more ``Bc`` on the same
  nodes, its magnitudes the case's values and ``metadata[BC_LOAD_CASE]`` the case's name (GeniE: BNBCD 2, and one
  BNDISPL per load case). The Sesam writer puts each in the load case of that name, the Abaqus writer in the step.
* ``super`` on all six dofs -> the nodes join the ``SESAM_SUPERNODES`` node set, which the Sesam writer retains on
  all six dofs (GeniE: BNBCD 4). On fewer than six it has no FEM form and is refused.
* ``dependent`` -> refused: GeniE writes BNBCD 3 with no BLDEP behind it, and an adapy FEM makes a dof dependent
  only through a constraint that says on what.

A rigid link's slaves are the mesh nodes inside its footprint, linked in translation (GeniE: a 9-term BLDEP) or,
``rotation_dependent``, in rotation too (12 terms). With ``include_all_edges=False`` GeniE V8.13-02 linked no node
and meshed no support at all -- measured on eight such links, over a whole plate, a whole plate edge, a whole
beam, part of a beam and a support point inside the footprint -- so nothing is made of one here either.

Whatever produces nothing in the FEM, or something other than GeniE's, is a named finding in
:mod:`ada.fem.formats.conversion_report` under :data:`STAGE`, never a log line only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.api.transforms import to_global_points, to_global_vectors, to_local_points
from ada.config import logger
from ada.fem.formats import conversion_report

if TYPE_CHECKING:
    from ada import FEM, Beam, Node
    from ada.fem.concept.constraints import (
        ConstraintConceptCurve,
        ConstraintConceptDofType,
        ConstraintConceptRigidLink,
        ConstraintConcepts,
    )
    from ada.fem.concept.loads import LoadConceptCase

#: The ``stage`` every finding of this conversion is filed under.
STAGE = "concept to fem"

_DOF_MAP = {"dx": 1, "dy": 2, "dz": 3, "rx": 4, "ry": 5, "rz": 6}


def report():
    return conversion_report.current()


def add_constraint_concepts_to_fem(
    concepts: ConstraintConcepts,
    fem: FEM,
    beams: Iterable[Beam],
    tol: float = 1e-4,
    load_cases: Iterable[LoadConceptCase] = (),
) -> None:
    """Convert constraint concepts into FEM boundary conditions on an already meshed FEM.

    A beam end constraint supports the cross-section of that beam end: a line mesh directly at its end node, and a
    shell/solid mesh through a reference node on the beam axis coupled to the section nodes (see
    :func:`_add_section_support`) -- or, with ``section_support="direct"``, on the section nodes themselves. A point
    constraint at the end of one of the given beams does the same, otherwise it restrains the mesh node at its
    position. A curve constraint restrains all mesh nodes on its segment, and a rigid link becomes a support at its
    master point coupled to the mesh nodes inside its influence region. Where a point or curve constraint shares
    nodes with a beam end constraint, the beam end constraint is kept on those nodes. Rotational dofs are dropped
    when all the constrained nodes are attached only to solid elements.

    ``load_cases`` are the concept load cases whose prescribed displacements give ``prescribed`` dofs their values,
    case by case. What each kind of dof becomes is in the module docstring.

    Concept positions are given in the local system of their parent part (or beam), and are transformed to the
    global system of the mesh.
    """
    beams = list(beams)
    claimed_by: dict[int, str] = {}
    settlements = _settlements_by_support(load_cases)
    held = _HeldNodes()

    for bec in concepts.beam_end_constraints.values():
        end_pos = to_global_points(bec.beam, bec.position)
        nodes = _nodes_in_section_plane(bec.beam, fem, end_pos, tol)
        if bec.section_support == "direct":
            added = _add_support(fem, bec.name, bec.dof_constraints, nodes, held)
        else:
            added = _add_section_support(fem, bec.name, bec.dof_constraints, nodes, end_pos, held)
        if added:
            claimed_by.update({n.id: bec.name for n in nodes})

    for pc in concepts.point_constraints.values():
        pos = to_global_points(pc.parent.parent_fem.parent_part, pc.position)
        found, at_beam_end = _get_point_constraint_nodes(pos, fem, beams, tol)
        nodes = _without_beam_end_nodes(pc.name, found, claimed_by)
        cases = settlements.pop(id(pc), [])
        if len(found) > 0 and len(nodes) == 0:
            continue

        if at_beam_end:
            _add_section_support(fem, pc.name, pc.dof_constraints, nodes, pos, held, cases)
        else:
            _add_support(fem, pc.name, pc.dof_constraints, nodes, held, cases)

    for cc in concepts.curve_constraints.values():
        found = _nodes_on_segment(fem, cc, tol)
        nodes = _without_beam_end_nodes(cc.name, found, claimed_by)
        if len(found) > 0 and len(nodes) == 0:
            continue
        _add_support(fem, cc.name, cc.dof_constraints, nodes, held, curve=cc)

    for rl in concepts.rigid_links.values():
        _add_rigid_link(fem, rl, tol, held)

    for cases in settlements.values():
        # A prescribed displacement on a support that is not among these concepts (another part's, say).
        for case, pd in cases:
            report().omitted(
                STAGE,
                "LoadConceptPrescribedDisplacement",
                f"{pd.name} in load case {case.name}",
                "its support point is not converted with this FEM, so the value has no node to act on",
                support=pd.support.name,
            )


def _settlements_by_support(load_cases: Iterable[LoadConceptCase]) -> dict[int, list]:
    """``{id(support point): [(load case, prescribed displacement)]}``, in load case order."""
    from ada.fem.concept.loads import LoadConceptPrescribedDisplacement

    out: dict[int, list] = {}
    for case in load_cases:
        for load in case.loads:
            if isinstance(load, LoadConceptPrescribedDisplacement):
                out.setdefault(id(load.support), []).append((case, load))
    return out


def _without_beam_end_nodes(name: str, nodes: list[Node], claimed_by: dict[int, str]) -> list[Node]:
    overlap = sorted({claimed_by[n.id] for n in nodes if n.id in claimed_by})
    if len(overlap) > 0:
        report().note(
            STAGE,
            "Support",
            name,
            "shares nodes with a beam end support; the beam end support is kept on the shared nodes",
            beam_end_supports=overlap,
        )
    return [n for n in nodes if n.id not in claimed_by]


class _HeldNodes:
    """What each support said about each node, so that two supports saying different things about one node are
    reported. adapy keeps both there (the union); GeniE V8.13-02 keeps one support's dofs and drops the other's
    (measured: a point on a curve, two curves meeting at a plate corner), and which one depends on the order it
    meets them in."""

    def __init__(self):
        self._by_node: dict[int, tuple[str, tuple]] = {}

    def claim(self, name: str, signature: tuple, nodes: list[Node]) -> None:
        clashes: dict[str, list[int]] = {}
        for n in nodes:
            first = self._by_node.setdefault(n.id, (name, signature))
            if first[0] != name and first[1] != signature:
                clashes.setdefault(first[0], []).append(n.id)
        for other, ids in sorted(clashes.items()):
            report().suspect(
                STAGE,
                "Support",
                name,
                "shares nodes with another support holding different dofs; both are kept there, where GeniE "
                "keeps one of the two",
                other=other,
                nodes=sorted(ids)[:10],
                n_nodes=len(ids),
            )


def _nodes_on_segment(fem: FEM, cc: ConstraintConceptCurve, tol: float) -> list[Node]:
    """All mesh nodes on the straight segment of a curve constraint"""
    part = cc.parent.parent_fem.parent_part
    p1, p2 = to_global_points(part, [cc.start_pos, cc.end_pos])
    axis = p2 - p1
    length = float(np.linalg.norm(axis))
    if length == 0.0:
        return fem.nodes.get_by_volume(p1, tol=tol)

    axis /= length
    all_nodes = _element_nodes(fem)
    rel = np.array([n.p for n in all_nodes], dtype=float) - p1
    along = rel @ axis
    dist = np.linalg.norm(rel - np.outer(along, axis), axis=1)
    mask = (dist <= tol) & (along >= -tol) & (along <= length + tol)
    return [all_nodes[i] for i in np.flatnonzero(mask)]


def tributary_lengths(nodes: list[Node], start, end) -> list[float]:
    """Each node's share of the segment ``start``..``end``, in the order given: half the distance to each neighbour
    along it, the first and last node reaching to the segment's ends.

    The shares add up to the segment's length, so a stiffness per length times them is the curve's whole
    stiffness. GeniE spread 500000 N/m^2 over a 4 m curve meshed at 0.5 m as 125000 on the two end nodes and
    250000 on the seven inside, which is this rule.
    """
    if len(nodes) == 0:
        return []
    p1, p2 = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    length = float(np.linalg.norm(p2 - p1))
    if length == 0.0:
        return [0.0] * len(nodes)
    axis = (p2 - p1) / length
    s = np.clip([float(np.dot(np.asarray(n.p, dtype=float) - p1, axis)) for n in nodes], 0.0, length)
    order = np.argsort(s, kind="stable")
    ss = s[order]
    bounds = np.concatenate([[0.0], (ss[1:] + ss[:-1]) / 2, [length]])
    shares = np.empty(len(ss))
    shares[order] = bounds[1:] - bounds[:-1]
    return [float(x) for x in shares]


def _add_rigid_link(fem: FEM, rl: ConstraintConceptRigidLink, tol: float, held: _HeldNodes) -> None:
    """A support at the master point, rigidly linked to all mesh nodes inside the influence region.

    The master point gets a node of its own (or reuses a mesh node at that position) carrying the support of the
    rigid link. The influence region is an axis-aligned box in the local system of the parent part. The dependent
    nodes are linked in all 6 dofs when the link is rotation dependent, otherwise in translation only.
    """
    from ada import Node
    from ada.fem import Constraint, FemSet

    if not rl.include_all_edges:
        report().omitted(
            STAGE,
            "ConstraintConceptRigidLink",
            rl.name,
            "include_all_edges is False: GeniE V8.13-02 links no node for such a link and meshes no support, so "
            "none is made here either",
        )
        return

    part = rl.parent.parent_fem.parent_part
    master_pos = to_global_points(part, rl.master_point)
    existing = fem.nodes.get_by_volume(master_pos, tol=tol)
    master_id = existing[0].id if len(existing) > 0 else None

    lower = np.asarray(rl.influence_region.lower_corner, dtype=float) - tol
    upper = np.asarray(rl.influence_region.upper_corner, dtype=float) + tol
    candidates = [n for n in _element_nodes(fem) if n.id != master_id]
    if len(candidates) > 0:
        local = to_local_points(part, [n.p for n in candidates]).reshape(-1, 3)
        inside = np.all((local >= lower) & (local <= upper), axis=1)
        dependents = [candidates[i] for i in np.flatnonzero(inside)]
    else:
        dependents = []

    if len(dependents) == 0:
        report().omitted(
            STAGE,
            "ConstraintConceptRigidLink",
            rl.name,
            "no mesh node inside its footprint, so it links nothing and no support is made",
        )
        return

    master = existing[0] if len(existing) > 0 else fem.nodes.add(Node(master_pos))
    m_set = FemSet(f"{rl.name}_master", [master], FemSet.TYPES.NSET)
    s_set = FemSet(f"{rl.name}_dependents", dependents, FemSet.TYPES.NSET)
    dofs = [1, 2, 3, 4, 5, 6] if rl.rotation_dependent else [1, 2, 3]
    fem.add_constraint(Constraint(rl.name, Constraint.TYPES.COUPLING, m_set, s_set, dofs=dofs))

    _add_support(fem, f"{rl.name}_support", rl.dof_constraints, [master], held)


def _add_section_support(
    fem: FEM,
    name: str,
    dof_constraints: list[ConstraintConceptDofType],
    nodes: list[Node],
    ref_pos: np.ndarray,
    held: _HeldNodes,
    settlements=(),
) -> bool:
    """A support of a beam end, given the nodes of its cross-section.

    A single node (a line mesh) is restrained directly. A shell/solid section is tied to a reference node on the
    beam axis by a kinematic coupling in all 6 dofs, and the support acts on the reference node. The section then
    moves as a rigid body about the beam end: a pinned end is free to rotate, and a fixed end is clamped without
    over-restraining the section (e.g. its Poisson contraction). Restraining every section node directly would
    clamp the end whatever the dofs, as a face whose nodes are all held in translation cannot rotate.
    """
    from ada import Node
    from ada.fem import Constraint, FemSet

    if len(nodes) <= 1:
        return _add_support(fem, name, dof_constraints, nodes, held, settlements)

    if not _acts(dof_constraints):
        logger.info(f'Constraint "{name}" holds no dof. Skipping')
        return False

    # Always a node of its own. A mesh node that happens to be on the beam axis (e.g. the web of a shell I-section)
    # belongs to the section, and a solid node has no rotational dofs for the support to act on.
    ref = fem.nodes.add(Node(ref_pos), allow_coincident=True)
    m_set = FemSet(f"{name}_ref", [ref], FemSet.TYPES.NSET)
    s_set = FemSet(f"{name}_sec", nodes, FemSet.TYPES.NSET)
    fem.add_constraint(Constraint(f"{name}_cpl", Constraint.TYPES.COUPLING, m_set, s_set, dofs=[1, 2, 3, 4, 5, 6]))

    return _add_support(fem, name, dof_constraints, [ref], held, settlements)


def _acts(dof_constraints: list[ConstraintConceptDofType]) -> bool:
    """Whether a support says anything about any dof: all free says nothing."""
    return any(dc.constraint_type != "free" for dc in dof_constraints)


def _add_support(
    fem: FEM,
    name: str,
    dof_constraints: list[ConstraintConceptDofType],
    nodes: list[Node],
    held: _HeldNodes,
    settlements=(),
    curve: ConstraintConceptCurve | None = None,
) -> bool:
    """One support's dofs on its nodes, kind by kind (see the module docstring). ``settlements`` are its
    ``(load case, prescribed displacement)`` pairs; a ``curve`` makes its spring stiffness per unit length."""
    from ada.fem import Bc, FemSet

    if not _acts(dof_constraints):
        logger.info(f'Constraint "{name}" holds no dof. Skipping')
        return False

    if len(nodes) == 0:
        report().omitted(STAGE, "Support", name, "the mesh has no node where the support is, so it acts on nothing")
        return False

    by_kind: dict[str, list[ConstraintConceptDofType]] = {}
    for dc in dof_constraints:
        by_kind.setdefault(dc.constraint_type, []).append(dc)

    held.claim(name, tuple((dc.dof, dc.constraint_type, dc.spring_stiffness) for dc in dof_constraints), nodes)

    solid_only = all(_is_solid_only(n) for n in nodes)
    if solid_only:
        dropped = sorted(_DOF_MAP[dc.dof] for dc in dof_constraints if dc.constraint_type != "free")
        dropped = [d for d in dropped if d > 3]
        if dropped:
            report().note(
                STAGE, "Support", name, "acts on nodes of solid elements only, which have no rotations", dofs=dropped
            )

    def dofs_of(kind: str) -> list[int]:
        return sorted(d for d in (_DOF_MAP[dc.dof] for dc in by_kind.get(kind, [])) if not (solid_only and d > 3))

    added = False
    prescribed = dofs_of("prescribed")
    held_dofs = sorted(dofs_of("fixed") + prescribed)
    if held_dofs:
        fem_set = fem.add_set(FemSet(f"{name}_set", nodes, FemSet.TYPES.NSET))
        fem.add_bc(Bc(name, fem_set, held_dofs))
        if prescribed:
            _add_settlements(fem, name, fem_set, prescribed, settlements)
        added = True

    springs = [dc for dc in by_kind.get("spring", []) if not (solid_only and _DOF_MAP[dc.dof] > 3)]
    if springs:
        added = _add_springs(fem, name, springs, nodes, curve) or added

    if "super" in by_kind:
        added = _add_super(fem, name, by_kind["super"], nodes) or added

    for dc in by_kind.get("dependent", []):
        report().omitted(
            STAGE,
            "ConstraintConceptDofType",
            f"{name}.{dc.dof}",
            "a dependent dof (GeniE: BNBCD 3) names nothing it depends on, and an adapy FEM makes a dof dependent "
            "only through a constraint that does; it is left free",
        )
    return added


def _add_settlements(fem: FEM, name: str, fem_set, dofs: list[int], settlements) -> None:
    """One ``Bc`` per load case, holding the case's values on the prescribed ``dofs`` and the case's name."""
    from ada.fem import Bc
    from ada.fem.constraints import BC_LOAD_CASE

    if not settlements:
        report().note(
            STAGE,
            "Support",
            name,
            "prescribed dofs with no prescribed displacement in any load case are held at zero",
            dofs=dofs,
        )
    for case, pd in settlements:
        values = [float(v) for v in (*pd.translation, *pd.rotation)]
        ignored = [d for d in range(1, 7) if values[d - 1] != 0.0 and d not in dofs]
        if ignored:
            # Sestra ignores a BNDISPL value on a dof without FIX code 2 (measured; see the Sesam reader).
            report().omitted(
                STAGE,
                "LoadConceptPrescribedDisplacement",
                f"{pd.name} in load case {case.name}",
                "a value on a dof its support does not prescribe, where it has nothing to act on",
                dofs=ignored,
                values=[values[d - 1] for d in ignored],
            )
        fem.add_bc(
            Bc(
                f"{name}_{case.name}",
                fem_set,
                list(dofs),
                magnitudes=[values[d - 1] for d in dofs],
                metadata={BC_LOAD_CASE: case.name},
            )
        )


def _add_springs(fem: FEM, name: str, springs: list[ConstraintConceptDofType], nodes: list[Node], curve) -> bool:
    """A spring to ground on each node, diagonal: each spring dof's stiffness, times the node's tributary length
    along the curve for a curve support."""
    from ada.fem import FemSet, Spring

    if curve is not None:
        part = curve.parent.parent_fem.parent_part
        p1, p2 = to_global_points(part, [curve.start_pos, curve.end_pos])
        factors = tributary_lengths(nodes, p1, p2)
    elif len(nodes) == 1:
        factors = [1.0]
    else:
        # A point's stiffness is the support's: on several coincident nodes it has no one node to go on.
        report().omitted(
            STAGE,
            "Support",
            name,
            "a point spring has more than one node at its position, and which one it acts on is not known",
            nodes=sorted(n.id for n in nodes),
        )
        return False

    next_id = (fem.elements.max_el_id if len(fem.elements) > 0 else 0) + 1
    for node, factor in sorted(zip(nodes, factors), key=lambda x: x[0].id):
        stiff = np.zeros((6, 6))
        for dc in springs:
            i = _DOF_MAP[dc.dof] - 1
            stiff[i, i] = float(dc.spring_stiffness) * factor
        sp_name = f"{name}_spring_{node.id}"
        fem_set = FemSet(f"{sp_name}_set", [node], FemSet.TYPES.NSET)
        fem.add_spring(Spring(sp_name, next_id, "SPRING1", fem_set=fem_set, stiff=stiff, parent=fem))
        next_id += 1
    return True


def _add_super(fem: FEM, name: str, supers: list[ConstraintConceptDofType], nodes: list[Node]) -> bool:
    """Super on all six dofs: the nodes join the node set the Sesam writer retains. On fewer, no FEM form."""
    from ada.fem import FemSet
    from ada.fem.formats.sesam.write.write_bcs import SUPERNODE_SET_NAME

    if len(supers) < 6:
        report().omitted(
            STAGE,
            "ConstraintConceptDofType",
            name,
            "super dofs on fewer than all six: the retained node set an adapy FEM carries retains all six",
            dofs=sorted(dc.dof for dc in supers),
        )
        return False

    try:
        fem_set = fem.sets.get_nset_from_name(SUPERNODE_SET_NAME)
    except ValueError:
        fem_set = fem.add_set(FemSet(SUPERNODE_SET_NAME, [], FemSet.TYPES.NSET))
    have = {m.id for m in fem_set.members}
    fem_set.add_members([n for n in sorted(nodes, key=lambda n: n.id) if n.id not in have])
    report().note(
        STAGE,
        "Support",
        name,
        f"super dofs are written as the {SUPERNODE_SET_NAME} node set, which the Sesam writer retains on all six "
        "dofs (BNBCD 4); other formats carry it as a plain node set",
    )
    return True


def _get_point_constraint_nodes(p: np.ndarray, fem: FEM, beams: list[Beam], tol: float) -> tuple[list[Node], bool]:
    """The nodes a point constraint at global position ``p`` acts on, and whether ``p`` is at a beam end.

    At a beam end, these are the nodes of the section there: a shell/solid mesh may have a node at the beam axis
    (e.g. the web of an I-section) that on its own would leave the section free.
    """
    nodes = []
    for bm in beams:
        if any(np.linalg.norm(end - p) <= tol for end in to_global_points(bm, [bm.n1.p, bm.n2.p])):
            nodes += _nodes_in_section_plane(bm, fem, p, tol)

    if len(nodes) == 0:
        return fem.nodes.get_by_volume(p, tol=tol), False

    return list({n.id: n for n in nodes}.values()), True


def _element_nodes(fem: FEM) -> list[Node]:
    """The mesh nodes, i.e. excluding e.g. the reference nodes of other supports, which belong to no element"""
    from ada.fem import Elem

    return [n for n in fem.nodes if any(isinstance(r, Elem) for r in n.refs)]


def _is_solid_only(node: Node) -> bool:
    from ada.fem import Elem

    elems = [r for r in node.refs if isinstance(r, Elem)]
    return len(elems) > 0 and all(isinstance(el.type, Elem.EL_TYPES.SOLID_SHAPES) for el in elems)


def _nodes_in_section_plane(bm: Beam, fem: FEM, p: np.ndarray, tol: float) -> list[Node]:
    half_h, half_w = _section_half_extents(bm)
    xvec, yvec, up = to_global_vectors(bm, [bm.xvec, bm.yvec, bm.up])

    all_nodes = _element_nodes(fem)
    coords = np.array([n.p for n in all_nodes], dtype=float) - p
    mask = (
        (np.abs(coords @ xvec) <= tol) & (np.abs(coords @ up) <= half_h + tol) & (np.abs(coords @ yvec) <= half_w + tol)
    )
    return [all_nodes[i] for i in np.flatnonzero(mask)]


def _section_half_extents(bm: Beam) -> tuple[float, float]:
    sec = bm.section
    if sec.r is not None:
        return sec.r, sec.r

    widths = [w for w in (sec.w_top, sec.w_btn) if w is not None]
    return sec.h / 2, max(widths) / 2
