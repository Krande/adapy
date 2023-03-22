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
