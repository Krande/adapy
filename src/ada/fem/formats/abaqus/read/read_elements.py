from __future__ import annotations

import logging
import re
from itertools import chain
from typing import TYPE_CHECKING

import numpy as np

from ada.concepts.points import Node
from ada.fem import Elem
from ada.fem.containers import FemElements
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes import ElemShape

from .cards import re_el

_re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

if TYPE_CHECKING:
    from ada.concepts.levels import FEM

sh = ElemShape.TYPES.shell
so = ElemShape.TYPES.solids
li = ElemShape.TYPES.lines

ada_to_abaqus_format = {
    sh.TRI: ("S3", "S3R", "R3D3", "S3RS"),
    sh.TRI6: ("STRI65",),
    sh.TRI7: ("S7",),
    sh.QUAD: ("S4", "S4R", "R3D4"),
    sh.QUAD8: ("S8", "S8R"),
    so.HEX8: ("C3D8", "C3D8R", "C3D8H"),
    so.HEX20: ("C3D20", "C3D20R", "C3D20RH"),
    so.HEX27: ("C3D27",),
    so.TETRA: ("C3D4",),
    so.TETRA10: ("C3D10",),
    so.PYRAMID5: ("C3D5", "C3D5H"),
    so.WEDGE: ("C3D6",),
    so.WEDGE15: ("C3D15",),
    li.LINE: ("B31",),
    li.LINE3: ("B32",),
    "MASS": ("MASS",),
    "ROTARYI": ("ROTARYI",),
    "CONNECTOR": ("CONN3D2",),
}


def abaqus_el_type_to_ada(el_type):
    for key, val in ada_to_abaqus_format.items():
        if el_type in val:
            return key
    raise ValueError(f'Element type "{el_type}" has not been added to conversion to ada map yet')


def get_elem_from_bulk_str(bulk_str, fem: "FEM") -> FemElements:
    """Read and import all *Element flags"""
    return FemElements(
        chain.from_iterable(filter(lambda x: x is not None, (grab_elements(m, fem) for m in re_el.finditer(bulk_str)))),
        fem_obj=fem,
    )


def grab_elements(match, fem: "FEM"):
    d = match.groupdict()
    eltype = d["eltype"]

    if eltype in ("CONN3D2", "MASS", "ROTARYI"):
        logging.info(f'Importing type "{eltype}"')
    ada_el_type = abaqus_el_type_to_ada(eltype)
    elset = d["elset"]
    el_type_members_str = d["members"]
    res = re.search("[a-zA-Z]", el_type_members_str)
    is_cubic = ada_el_type in [so.HEX20, so.HEX27]
    if is_cubic or res is None:
        if is_cubic is True:
            elem_nodes_str = el_type_members_str.splitlines()
            ntext = "".join(
                [l1.strip() + "    " + l2.strip() + "\n" for l1, l2 in zip(elem_nodes_str[:-1:2], elem_nodes_str[1::2])]
            )
        else:
            ntext = d["members"]
        res = np.fromstring(ntext.replace("\n", ","), sep=",", dtype=int)
        n = ElemShape.num_nodes(ada_el_type) + 1
        res_ = res.reshape(int(res.size / n), n)
        return [
            Elem(
                e[0],
                [fem.nodes.from_id(n) for n in e[1:]],
                ada_el_type,
                elset,
                el_formulation_override=eltype,
                parent=fem,
            )
            for e in res_
        ]
    else:
        # TODO: This code needs to be re-worked!
        elems = []
        for li in el_type_members_str.splitlines():
            elem_nodes_str = li.split(",")
            elid = str_to_int(elem_nodes_str[0])
            elem_nodes = get_elem_nodes(elem_nodes_str, fem)
            elems.append(Elem(elid, elem_nodes, ada_el_type, elset, el_formulation_override=eltype, parent=fem))
        return elems


def get_elem_nodes(elem_nodes_str, fem: "FEM"):
    elem_nodes = []
    for d in elem_nodes_str[1:]:
        temp2 = [x.strip() for x in d.split(".")]
        par_ = None
        if len(temp2) == 2:
            par, setr = temp2
            pfems = []
            parents = fem.parent.get_all_parts_in_assembly()
            for p in parents:
                pfems.append(p.fem.name)
                if p.fem.name == par:
                    par_ = p
                    break
            if par_ is None:
                raise ValueError(f'Unable to find parent for "{par}"')
            r = par_.fem.nodes.from_id(str_to_int(setr))
            if type(r) != Node:
                raise ValueError("Node ID not found")
            elem_nodes.append(r)
        else:
            r = fem.nodes.from_id(str_to_int(d))
            if type(r) != Node:
                raise ValueError("Node ID not found")
            elem_nodes.append(r)
    return elem_nodes
