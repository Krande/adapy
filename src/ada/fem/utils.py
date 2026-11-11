from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.fem import Elem

from .shapes import ElemShape
from .shapes import definitions as shape_def

if TYPE_CHECKING:
    from ada import FEM, Assembly, Beam, Node, Part, Plate
    from ada.fem import FemSet


def get_eldata(fem_source: Assembly | Part | FEM):
    """Return a dictionary of basic mesh statistics"""
    from ada import FEM, Assembly, Part

    el_types = dict()

    def scan_elem(mesh):
        for el in mesh.elements:
            if el.type not in el_types.keys():
                el_types[el.type] = 1
            else:
                el_types[el.type] += 1

    if isinstance(fem_source, Assembly):
        for p in fem_source.parts.values():
            scan_elem(p.fem)
    elif issubclass(type(fem_source), Part):
        scan_elem(fem_source.fem)
    elif isinstance(fem_source, FEM):
        scan_elem(fem_source)
    else:
        raise ValueError(f'Unknown fem_source "{fem_source}"')
    return el_types


def get_beam_end_nodes(bm: Beam, end=1, tol=1e-3) -> list[Node]:
    """Get list of nodes from end of beam"""
    p = bm.parent
    nodes = p.fem.nodes
    w = bm.section.w_btn
    h = bm.section.h
    xv = np.array(bm.xvec)
    yv = np.array(bm.yvec)
    zv = np.array(bm.up)
    if end == 1:
        p = bm.n1.p
    else:
        p = bm.n2.p
    n_min = p - xv * tol - (h / 2 + tol) * zv - (w / 2 + tol) * yv
    n_max = p + xv * tol + (h / 2 + tol) * zv + (w / 2 + tol) * yv
    members = [e for e in nodes.get_by_volume(n_min, n_max)]
    return members


def get_nodes_along_plate_edges(pl: Plate, fem: FEM, edge_indices=None, tol=1e-3) -> list[Node]:
    """Return FEM nodes from edges of a plate"""

    res = []
    bmin, bmax = list(zip(*pl.bbox()))
    bmin_smaller = np.array(bmin) + pl.poly.xdir * tol + pl.poly.ydir * tol
    bmax_smaller = np.array(bmax) - pl.poly.xdir * tol - pl.poly.ydir * tol
    all_res = fem.nodes.get_by_volume(bmin, bmax)
    res = [n.id for n in fem.nodes.get_by_volume(bmin_smaller, bmax_smaller)]
    return list(filter(lambda x: x.id not in res, all_res))


def is_line_elem(elem: Elem):
    return True if isinstance(elem.type, shape_def.LineShapes) else False


def is_tri6_shell_elem(sh_fs):
    elem_check = [x.type == ElemShape.TYPES.shell.TRI6 for x in sh_fs.elset.members]
    return all(elem_check)


def is_quad8_shell_elem(sh_fs):
    elem_check = [x.type == ElemShape.TYPES.shell.QUAD8 for x in sh_fs.elset.members]
    return all(elem_check)


def is_parent_of_node_solid(no: Node) -> bool:
    refs = no.refs
    for elem in refs:
        if isinstance(elem.type, shape_def.SolidShapes):
            return True
    return False


def elset_to_part(name: str, elset: FemSet) -> Part:
    """Create a new part based on a specific element set."""
    from ada import Part

    fem = elset.parent
    p = Part(name)

    for mem in elset.members:
        if elset.type == elset.TYPES.ELSET:
            p.fem.add_elem(mem)
            if mem.fem_sec.name not in p.fem.sections.name_map.keys():
                p.fem.add_section(mem.fem_sec)
                fem.sections.remove(mem.fem_sec)
            if mem.fem_sec.elset.name not in p.fem.elsets.keys():
                p.fem.add_set(mem.fem_sec.elset)
                fem.sets.remove(mem.fem_sec.elset)
            for n in mem.nodes:
                if n not in p.fem.nodes:
                    p.fem.nodes.add(n)
            # fem.nodes.remove(mem.nodes)
        else:
            p.fem.nodes.add(mem)

    if elset.type == elset.TYPES.ELSET:
        fem.elements.remove_elements_by_set(elset)
    else:
        fem.nodes.remove(elset.members)

    return p


def split_line_element_in_two(el: Elem) -> Elem:
    from ada import Node

    n1 = el.nodes[0]
    n2 = el.nodes[-1]
    midp = (n1.p + n2.p) / 2
    new_node = el.parent.nodes.add(Node(midp))
    el.nodes[-1] = new_node
    elset = el.elset
    elem = Elem(None, [new_node, n2], el.type, elset=elset, fem_sec=el.fem_sec, parent=el.parent)
    fs = el.fem_sec
    fs.elset.add_members([elem])
    return elem


def _is_mass(obj) -> bool:
    from ada.fem.elements import Mass

    return isinstance(obj, Mass) or isinstance(getattr(obj, "type", None), shape_def.MassTypes)


#: The DOFs a boundary condition given by name fixes (1..3 translations, 4..6 rotations): what the Abaqus
#: keywords mean. A reader that keeps the name instead of the numbers (``ENCASTRE`` is always kept, the others
#: when a deck states them one per line) leaves it to whoever reads ``Bc.dofs`` to know them.
NAMED_BC_DOFS = {
    "encastre": (1, 2, 3, 4, 5, 6),
    "pinned": (1, 2, 3),
    "xsymm": (1, 5, 6),
    "ysymm": (2, 4, 6),
    "zsymm": (3, 4, 5),
    "xasymm": (2, 3, 4),
    "yasymm": (1, 3, 5),
    "zasymm": (1, 2, 6),
}


def bc_fixed_dofs(dofs) -> set[int]:
    """The DOFs ``Bc.dofs`` fixes, as numbers: its integers, and for a restraint given by name (``"ENCASTRE"``, one
    name or a list of them) the DOFs that name stands for. A name that is not one of :data:`NAMED_BC_DOFS` fixes
    nothing known."""
    if isinstance(dofs, str):
        dofs = [dofs]
    fixed: set[int] = set()
    for d in dofs or ():
        if isinstance(d, str):
            fixed.update(NAMED_BC_DOFS.get(d.strip().lower(), ()))
        elif d is not None:
            fixed.add(int(d))
    return fixed


def node_is_held(node: Node) -> bool:
    """Whether something stiffens ``node``'s translations: an element that is not a mass (a beam, a shell, a solid, a
    spring, a connector), a constraint, or boundary conditions that fix all three translations.

    A constraint is on the node through a set the node is in, or through a surface made of such a set (what a tie
    between coincident nodes is built on). A boundary condition may state its DOFs by name (:func:`bc_fixed_dofs`):
    an encastred or a pinned node is held.

    A node only point masses are on has no stiffness at all: a static solve finds its stiffness matrix singular
    (Code_Aster stops at ``FACTOR_11``)."""
    from ada.fem.common import Csys
    from ada.fem.constraints import Bc, Constraint
    from ada.fem.sets import FemSet
    from ada.fem.surfaces import Surface

    fixed: set[int] = set()
    for ref in node.refs:
        if _is_mass(ref) or isinstance(ref, Csys):
            continue
        if not isinstance(ref, FemSet):
            return True
        for user in ref.refs:
            if isinstance(user, Constraint):
                return True
            if isinstance(user, Surface) and any(isinstance(u, Constraint) for u in user.refs):
                return True
            if isinstance(user, Bc):
                fixed |= bc_fixed_dofs(user.dofs)
    return {1, 2, 3} <= fixed


def mass_nodes(mass) -> list:
    """The nodes a mass is on: its members (a mass given a node set after it was made has no ``nodes``)."""
    members = mass.members if mass.members is not None else mass.nodes
    return [n for n in (members or []) if hasattr(n, "p") and not hasattr(n, "nodes")]


def free_point_masses(fem: FEM) -> list:
    """The point masses (``MassTypes.MASS``) of ``fem`` on a node nothing else holds (:func:`node_is_held`)."""
    out = []
    for mass in fem.elements.masses:
        if mass.type != shape_def.MassTypes.MASS:
            continue
        if any(not node_is_held(n) for n in mass_nodes(mass)):
            out.append(mass)
    return out


def report_free_point_masses(fem: FEM, stage: str) -> list:
    """Record each of :func:`free_point_masses` as suspect in the current conversion report under ``stage``, with the
    position of its free node(s); returns them. For a writer that writes such a mass as declared (the solver then
    stops on a singular stiffness matrix, or a mechanism, naming a node id only)."""
    from ada.fem.formats import conversion_report

    masses = free_point_masses(fem)
    for mass in masses:
        conversion_report.current().suspect(
            stage,
            "Mass",
            mass.name,
            "a point mass on a node no element, support or constraint holds (a mass off the structure): its "
            "translations have no stiffness, so a static solve is singular",
            positions=[[round(float(c), 6) for c in n.p] for n in mass_nodes(mass) if not node_is_held(n)],
            mass=mass.mass,
        )
    return masses
