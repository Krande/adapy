from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING

import numpy as np

from ada.api.nodes import Node
from ada.config import logger
from ada.core.utils import Counter
from ada.fem import Connector, Elem
from ada.fem.containers import FemElements
from ada.fem.formats.abaqus.elem_shapes import abaqus_el_type_to_ada
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes.definitions import ShapeResolver, SolidShapes

from . import cards

if TYPE_CHECKING:
    from ada.fem import FEM

_re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL


def get_elem_from_bulk_str(bulk_str, fem: "FEM") -> FemElements:
    """Read and import all *Element flags"""
    elements = FemElements(
        chain.from_iterable(
            filter(lambda x: x is not None, (grab_elements(m, fem) for m in cards.re_el.finditer(bulk_str)))
        ),
        fem_obj=fem,
    )

    return elements


def grab_elements(match, fem: "FEM"):
    d = match.groupdict()
    eltype = d["eltype"]

    if eltype in ("CONN3D2",):
        logger.info(f'Importing Connector type "{eltype}"')

    if eltype in ("MASS", "ROTARYI"):
        logger.info(f'Importing Mass type "{eltype}"')

    ada_el_type = abaqus_el_type_to_ada(eltype)
    elset = d["elset"]
    el_type_members_str = d["members"]
    res = re.search("[a-zA-Z]", el_type_members_str)
    is_cubic = ada_el_type in [SolidShapes.HEX20, SolidShapes.HEX27]
    if is_cubic or res is None:
        if is_cubic is True:
            elem_nodes_str = el_type_members_str.splitlines()
            ntext = "".join(
                [l1.strip() + "    " + l2.strip() + "\n" for l1, l2 in zip(elem_nodes_str[:-1:2], elem_nodes_str[1::2])]
            )
        else:
            ntext = d["members"]
        res = np.fromstring(ntext.replace("\n", ","), sep=",", dtype=int)
        n = ShapeResolver.get_el_nodes_from_type(ada_el_type) + 1
        return numpy_array_to_list_of_elements(res.reshape(int(res.size / n), n), eltype, elset, ada_el_type, fem)
    else:
        elems = []
        for li in el_type_members_str.splitlines():
            elem_nodes_str = li.split(",")
            elid = str_to_int(elem_nodes_str[0])
            elem_nodes = get_elem_nodes(elem_nodes_str, fem)
            elem = Elem(elid, elem_nodes, ada_el_type, elset, el_formulation_override=eltype, parent=fem)
            elems.append(elem)
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
            if type(r) is not Node:
                raise ValueError("Node ID not found")
            elem_nodes.append(r)
        else:
            r = fem.nodes.from_id(str_to_int(d))
            if type(r) is not Node:
                raise ValueError("Node ID not found")
            elem_nodes.append(r)
    return elem_nodes


con_names = Counter(1, "connector")


def numpy_array_to_list_of_elements(res_, eltype, elset, ada_el_type, fem: FEM) -> list[Elem]:
    if ada_el_type == Elem.EL_TYPES.CONNECTOR_SHAPES.CONNECTOR:
        connectors = []
        for e in res_:
            if len(e) != 3:
                raise ValueError()
            el_id = e[0]
            n1, n2 = [fem.nodes.from_id(n) for n in e[1:]]
            con = Connector(next(con_names), el_id, n1, n2, con_type=None, con_sec=None, parent=fem)
            connectors.append(con)
        return connectors
    else:
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


@dataclass
class ConnectorSectionData:
    elset: str
    behaviour: str
    connection_type: str
    csys: str


def update_connector_data(bulk_str: str, fem: FEM):
    """Extract connector elements from bulk string"""

    nsuffix = Counter(1, "_")
    for m in cards.connector_section.regex.finditer(bulk_str):
        d = m.groupdict()
        csys_ref = d["csys"].replace('"', "")
        name = d["behavior"] + next(nsuffix)
        elset = fem.elsets[d["elset"]]
        connector: Connector = elset.members[0]
        con_sec = fem.connector_sections[d["behavior"]]
        csys_ref = csys_ref[:-1] if csys_ref[-1] == "," else csys_ref
        csys = fem.lcsys[csys_ref]
        con_type = d["contype"]
        if con_type[-1] == ",":
            con_type = con_type[:-1]

        connector.elset = elset
        connector.name = name
        connector.con_sec = con_sec
        connector.con_type = con_type
        connector.csys = csys
