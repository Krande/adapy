"""Shell-to-solid coupling (``Constraint.TYPES.SHELL2SOLID``): a shell edge joined to a solid face.

The constraint is the one the Abaqus reader makes from ``*Shell to Solid Coupling`` and the Abaqus writer writes back:
``m_set`` the shell edge (a node set or surface on the shell's edge nodes), ``s_set`` the solid surface it meets. The
other writers take the same object:

* **Code_Aster** -- ``LIAISON_MAIL`` with ``TYPE_RACCORD='COQUE_MASSIF'`` (U4.44.01 5.13.3): the edge nodes' six dofs
  eliminated, their translations those of the solid at the node, their rotations those of the shell's normal fibre
  through the solid, from the solid's translations at the fibre's ends ``A +- (h / 2) n``; the thickness ``h`` and the
  shell's normals (``CREA_CHAMP NORMALE``) given.
* **Sesam** -- the same kinematics as BLDEP linear dependencies (:func:`edge_relations`), over the solid face's nodes:
  translations ``u_s = sum_k N_k(A) u_k``, rotations ``theta_s = n x (u(A+) - u(A-)) / h``, each point's solid motion
  interpolated on the face facet it projects onto. A rigid motion of the solid passes through exactly (``theta = w -
  (w . n) n`` for a rotation ``w``: the fibre turns with the solid, and the drilling rotation about ``n``, which a fibre
  cannot carry, is held).

Abaqus couples the other way round -- the edge independent, its motion distributed over the face within the influence
distance -- so the two agree on a joint's kinematics, not on how a load spreads into the solid right at the joint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ada import Node
    from ada.fem import Constraint


@dataclass
class EdgeJoint:
    """The shell side of a shell-to-solid coupling, as the writers need it."""

    #: the shell edge's nodes
    nodes: list
    #: ``{node id: unit normal}`` of the shell at each edge node (mean of its shell elements')
    normals: dict = field(default_factory=dict)
    #: the shell's thickness at the joint (one value: Code_Aster's EPAIS is constant)
    thickness: float | None = None
    #: the shell elements at the edge (for Code_Aster's normal field)
    shells: list = field(default_factory=list)
    #: why the joint cannot be made, or None
    why: str | None = None


def _shell_elements(constraint: Constraint) -> list:
    fem = constraint.parent
    fems = [fem]
    owner = getattr(fem, "parent", None)
    assembly = owner.get_assembly() if owner is not None and hasattr(owner, "get_assembly") else None
    if assembly is not None:
        fems += [p.fem for p in assembly.get_all_parts_in_assembly(include_self=True) if p.fem is not fem]
    return [el for f in fems if f is not None for el in f.elements.shell]


def edge_joint(constraint: Constraint) -> EdgeJoint:
    """The shell edge of ``constraint``: its nodes, the shell's normal at each and its thickness."""
    from ada.fem.surfaces import surface_nodes

    nodes = list(surface_nodes(constraint.m_set))
    joint = EdgeJoint(nodes)
    if not nodes:
        joint.why = "its shell edge has no nodes"
        return joint
    ids = {int(n.id) for n in nodes}
    by_node: dict[int, list] = {}
    for el in _shell_elements(constraint):
        for n in el.nodes:
            if int(n.id) in ids:
                by_node.setdefault(int(n.id), []).append(el)
    thicknesses = set()
    shells = {}
    for nid in ids:
        els = by_node.get(nid, [])
        if not els:
            joint.why = f"shell edge node {nid} belongs to no shell element"
            return joint
        normals = []
        for el in els:
            c = np.array([n.p for n in el.nodes[:3]], dtype=float)
            v = np.cross(c[1] - c[0], c[2] - c[0])
            normals.append(v / np.linalg.norm(v))
            shells[id(el)] = el
            sec = getattr(el, "fem_sec", None)
            if sec is not None and getattr(sec, "thickness", None) is not None:
                thicknesses.add(round(float(sec.thickness), 12))
        # a node shared by elements wound either way: the normal's sense does not matter (the fibre is the same)
        ref = normals[0]
        mean = sum((nv if np.dot(nv, ref) >= 0 else -nv) for nv in normals)
        joint.normals[nid] = mean / np.linalg.norm(mean)
    joint.shells = list(shells.values())
    if len(thicknesses) != 1:
        joint.why = (
            "the shell at the edge has no single thickness"
            if thicknesses
            else "the shell at the edge has no thickness (no shell section)"
        )
        return joint
    joint.thickness = thicknesses.pop()
    return joint


@dataclass
class EdgeRelation:
    """One shell edge node's six dofs as a linear function of the solid face nodes' translations:
    ``u_s[d] = sum beta * u_m[e]`` over ``terms`` ``{(master id, d, e): beta}`` (dofs 1..6 and 1..3)."""

    node: Node
    terms: dict = field(default_factory=dict)
    masters: dict = field(default_factory=dict)  # master id -> Node
    gap: float = 0.0


def edge_relations(constraint: Constraint, joint: EdgeJoint | None = None) -> tuple[list[EdgeRelation], str | None]:
    """The relation of each shell edge node to the solid face (see the module docstring), or ``([], why)``."""
    from ada.fem.formats.sesam.write.facet_interpolation import nearest_facet
    from ada.fem.surfaces import surface_facets

    joint = joint or edge_joint(constraint)
    if joint.why:
        return [], joint.why
    facets = [f for f in surface_facets(constraint.s_set) if f.shape is not None]
    if not facets:
        return [], "its solid side has no faces to project onto (a node set or node surface)"
    h = float(joint.thickness)
    nodes = joint.nodes
    a = np.array([n.p for n in nodes], dtype=float)
    nrm = np.array([joint.normals[int(n.id)] for n in nodes])
    points = np.vstack([a, a + 0.5 * h * nrm, a - 0.5 * h * nrm])
    match = nearest_facet(points, facets, max_distance=np.inf)
    m = len(nodes)
    out = []
    for i, node in enumerate(nodes):
        rel = EdgeRelation(node, gap=float(match.distance[i]))

        def add(point_index, coef_of):
            facet = facets[int(match.facet_index[point_index])]
            for k, master in enumerate(facet.nodes):
                w = float(match.weights[point_index, k])
                if abs(w) < 1e-14:
                    continue
                rel.masters[int(master.id)] = master
                for (d, e), c in coef_of(w).items():
                    key = (int(master.id), d, e)
                    rel.terms[key] = rel.terms.get(key, 0.0) + c

        # translations at A
        add(i, lambda w: {(d, d): w for d in (1, 2, 3)})
        # rotations: theta = n x (u+ - u-) / h, (n x d)_j = sum_ab eps_jab n_a d_b
        n = nrm[i]
        cross = np.array([[0.0, -n[2], n[1]], [n[2], 0.0, -n[0]], [-n[1], n[0], 0.0]])  # cross @ d = n x d

        def rot(sign):
            return lambda w: {
                (3 + j + 1, b + 1): sign * w * cross[j, b] / h
                for j in range(3)
                for b in range(3)
                if abs(cross[j, b]) > 0.0
            }

        add(m + i, rot(1.0))
        add(2 * m + i, rot(-1.0))
        rel.terms = {k: v for k, v in rel.terms.items() if abs(v) > 1e-14}
        out.append(rel)
    return out, None
