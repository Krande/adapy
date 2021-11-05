from typing import TYPE_CHECKING, List, Union

import numpy as np

from ada.fem import Elem

from .shapes import ElemShape

if TYPE_CHECKING:
    from ada import FEM, Assembly, Beam, Node, Part, Plate


def get_eldata(fem_source: Union["Assembly", "Part", "FEM"]):
    """Return a dictionary of basic mesh statistics"""
    el_types = dict()

    def scan_elem(mesh):
        for el in mesh.elements:
            if el.type not in el_types.keys():
                el_types[el.type] = 1
            else:
                el_types[el.type] += 1

    if type(fem_source) is Assembly:
        for p in fem_source.parts.values():
            scan_elem(p.fem)
    elif issubclass(type(fem_source), Part):
        scan_elem(fem_source.fem)
    elif type(fem_source) is FEM:
        scan_elem(fem_source)
    else:
        raise ValueError(f'Unknown fem_source "{fem_source}"')
    return el_types


def get_beam_end_nodes(bm: "Beam", end=1, tol=1e-3) -> List["Node"]:
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


def get_nodes_along_plate_edges(pl: "Plate", fem: "FEM", edge_indices=None, tol=1e-3) -> List["Node"]:
    """Return FEM nodes from edges of a plate"""

    res = []
    bmin, bmax = list(zip(*pl.bbox))
    bmin_smaller = np.array(bmin) + pl.poly.xdir * tol + pl.poly.ydir * tol
    bmax_smaller = np.array(bmax) - pl.poly.xdir * tol - pl.poly.ydir * tol
    all_res = fem.nodes.get_by_volume(bmin, bmax)
    res = [n.id for n in fem.nodes.get_by_volume(bmin_smaller, bmax_smaller)]
    return list(filter(lambda x: x.id not in res, all_res))


def is_line_elem(elem: Elem):
    return True if elem.type in ElemShape.TYPES.lines.all else False


def is_tri6_shell_elem(sh_fs):
    elem_check = [x.type == ElemShape.TYPES.shell.TRI6 for x in sh_fs.elset.members]
    return all(elem_check)


def is_quad8_shell_elem(sh_fs):
    elem_check = [x.type == ElemShape.TYPES.shell.QUAD8 for x in sh_fs.elset.members]
    return all(elem_check)


def is_parent_of_node_solid(no: "Node") -> bool:
    refs = no.refs
    for elem in refs:
        if elem.type in ElemShape.TYPES.solids.all:
            return True
    return False
