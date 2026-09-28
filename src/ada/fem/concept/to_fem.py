from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.api.transforms import to_global_points, to_global_vectors, to_local_points
from ada.config import logger

if TYPE_CHECKING:
    from ada import FEM, Beam, Node
    from ada.fem.concept.constraints import (
        ConstraintConceptCurve,
        ConstraintConceptDofType,
        ConstraintConceptPoint,
        ConstraintConceptRigidLink,
        ConstraintConcepts,
    )

_DOF_MAP = {"dx": 1, "dy": 2, "dz": 3, "rx": 4, "ry": 5, "rz": 6}


def add_constraint_concepts_to_fem(
    concepts: ConstraintConcepts, fem: FEM, beams: Iterable[Beam], tol: float = 1e-4
) -> None:
    """Convert constraint concepts into FEM boundary conditions on an already meshed FEM.

    A beam end constraint restrains all nodes on the cross-section face of that beam end (a single node for line
    elements). A point constraint at the end of one of the given beams does the same, otherwise it restrains the
    mesh node at its position. A curve constraint restrains all mesh nodes on its segment, and a rigid link becomes
    a support at its master point coupled to the mesh nodes inside its influence region. Where a point or curve
    constraint shares nodes with a beam end constraint, the beam end constraint is kept on those nodes. Rotational
    dofs are dropped when all the constrained nodes are attached only to solid elements.

    Concept positions are given in the local system of their parent part (or beam), and are transformed to the
    global system of the mesh.
    """
    beams = list(beams)
    claimed_by: dict[int, str] = {}

    for bec in concepts.beam_end_constraints.values():
        nodes = _nodes_in_section_plane(bec.beam, fem, to_global_points(bec.beam, bec.position), tol)
        if _add_bc(fem, bec.name, bec.dof_constraints, nodes):
            claimed_by.update({n.id: bec.name for n in nodes})

    for pc in concepts.point_constraints.values():
        nodes = _get_point_constraint_nodes(pc, fem, beams, tol)
        overlap = {claimed_by[n.id] for n in nodes if n.id in claimed_by}
        if len(overlap) > 0:
            logger.warning(
                f'Point constraint "{pc.name}" overlaps beam end constraint(s) {sorted(overlap)}. '
                "The beam end constraint is kept on the shared nodes"
            )
            nodes = [n for n in nodes if n.id not in claimed_by]
            if len(nodes) == 0:
                continue

        _add_bc(fem, pc.name, pc.dof_constraints, nodes)

    for cc in concepts.curve_constraints.values():
        nodes = _nodes_on_segment(fem, cc, tol)
        overlap = {claimed_by[n.id] for n in nodes if n.id in claimed_by}
        if len(overlap) > 0:
            logger.warning(
                f'Curve constraint "{cc.name}" overlaps beam end constraint(s) {sorted(overlap)}. '
                "The beam end constraint is kept on the shared nodes"
            )
            nodes = [n for n in nodes if n.id not in claimed_by]

        _add_bc(fem, cc.name, cc.dof_constraints, nodes)

    for rl in concepts.rigid_links.values():
        _add_rigid_link(fem, rl, tol)


def _nodes_on_segment(fem: FEM, cc: ConstraintConceptCurve, tol: float) -> list[Node]:
    """All mesh nodes on the straight segment of a curve constraint"""
    part = cc.parent.parent_fem.parent_part
    p1, p2 = to_global_points(part, [cc.start_pos, cc.end_pos])
    axis = p2 - p1
    length = float(np.linalg.norm(axis))
    if length == 0.0:
        return fem.nodes.get_by_volume(p1, tol=tol)

    axis /= length
    all_nodes = list(fem.nodes)
    rel = np.array([n.p for n in all_nodes], dtype=float) - p1
    along = rel @ axis
    dist = np.linalg.norm(rel - np.outer(along, axis), axis=1)
    mask = (dist <= tol) & (along >= -tol) & (along <= length + tol)
    return [all_nodes[i] for i in np.flatnonzero(mask)]


def _add_rigid_link(fem: FEM, rl: ConstraintConceptRigidLink, tol: float) -> None:
    """A support at the master point, rigidly linked to all mesh nodes inside the influence region.

    The master point gets a node of its own (or reuses a mesh node at that position) carrying the fixed dofs of
    the rigid link. The influence region is an axis-aligned box in the local system of the parent part. The
    dependent nodes are linked in all 6 dofs when the link is rotation dependent, otherwise in translation only.
    """
    from ada import Node
    from ada.fem import Constraint, FemSet

    part = rl.parent.parent_fem.parent_part
    master_pos = to_global_points(part, rl.master_point)
    existing = fem.nodes.get_by_volume(master_pos, tol=tol)
    master = existing[0] if len(existing) > 0 else fem.nodes.add(Node(master_pos))

    lower = np.asarray(rl.influence_region.lower_corner, dtype=float) - tol
    upper = np.asarray(rl.influence_region.upper_corner, dtype=float) + tol
    candidates = [n for n in fem.nodes if n.id != master.id]
    if len(candidates) > 0:
        local = to_local_points(part, [n.p for n in candidates]).reshape(-1, 3)
        inside = np.all((local >= lower) & (local <= upper), axis=1)
        dependents = [candidates[i] for i in np.flatnonzero(inside)]
    else:
        dependents = []

    if len(dependents) == 0:
        logger.warning(f'No FEM nodes found inside the influence region of rigid link "{rl.name}". Skipping')
        return

    m_set = FemSet(f"{rl.name}_master", [master], FemSet.TYPES.NSET)
    s_set = FemSet(f"{rl.name}_dependents", dependents, FemSet.TYPES.NSET)
    dofs = [1, 2, 3, 4, 5, 6] if rl.rotation_dependent else [1, 2, 3]
    fem.add_constraint(Constraint(rl.name, Constraint.TYPES.COUPLING, m_set, s_set, dofs=dofs))

    _add_bc(fem, f"{rl.name}_support", rl.dof_constraints, [master])


def _add_bc(fem: FEM, name: str, dof_constraints: list[ConstraintConceptDofType], nodes: list[Node]) -> bool:
    from ada.fem import Bc, FemSet

    dofs = _fixed_dofs(name, dof_constraints)
    if len(dofs) == 0:
        logger.info(f'Constraint "{name}" has no fixed dofs. Skipping')
        return False

    if len(nodes) == 0:
        logger.warning(f'No FEM nodes found for constraint "{name}". Skipping')
        return False

    if all(_is_solid_only(n) for n in nodes):
        dofs = [d for d in dofs if d <= 3]

    fem_set = fem.add_set(FemSet(f"{name}_set", nodes, FemSet.TYPES.NSET))
    fem.add_bc(Bc(name, fem_set, dofs))
    return True


def _get_point_constraint_nodes(pc: ConstraintConceptPoint, fem: FEM, beams: list[Beam], tol: float) -> list[Node]:
    p = to_global_points(pc.parent.parent_fem.parent_part, pc.position)

    # A beam end must be restrained over its whole section face, as a shell/solid mesh may still have a node at the
    # beam axis (e.g. the web of an I-section) that on its own would leave the section free
    nodes = []
    for bm in beams:
        if any(np.linalg.norm(end - p) <= tol for end in to_global_points(bm, [bm.n1.p, bm.n2.p])):
            nodes += _nodes_in_section_plane(bm, fem, p, tol)

    if len(nodes) == 0:
        return fem.nodes.get_by_volume(p, tol=tol)

    return list({n.id: n for n in nodes}.values())


def _fixed_dofs(name: str, dof_constraints: list[ConstraintConceptDofType]) -> list[int]:
    dofs = []
    for dc in dof_constraints:
        if dc.constraint_type == "fixed":
            dofs.append(_DOF_MAP[dc.dof])
        elif dc.constraint_type != "free":
            logger.warning(f'Constraint type "{dc.constraint_type}" on "{name}.{dc.dof}" is not yet supported')
    return sorted(dofs)


def _is_solid_only(node: Node) -> bool:
    from ada.fem import Elem

    elems = [r for r in node.refs if isinstance(r, Elem)]
    return len(elems) > 0 and all(isinstance(el.type, Elem.EL_TYPES.SOLID_SHAPES) for el in elems)


def _nodes_in_section_plane(bm: Beam, fem: FEM, p: np.ndarray, tol: float) -> list[Node]:
    half_h, half_w = _section_half_extents(bm)
    xvec, yvec, up = to_global_vectors(bm, [bm.xvec, bm.yvec, bm.up])

    all_nodes = list(fem.nodes)
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
