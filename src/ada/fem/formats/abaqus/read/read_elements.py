from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING

import numpy as np

from ada.api.nodes import Node
from ada.config import logger
from ada.core.utils import Counter
from ada.fem import Connector, Elem, Mass
from ada.fem.containers import FemElements
from ada.fem.formats.abaqus.elem_shapes import (
    UnsupportedAbaqusElementType,
    abaqus_el_type_to_ada,
)
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes import definitions as shape_def
from ada.fem.shapes.definitions import ShapeResolver, SolidShapes

from .keywords import validate
from .lexer import KeywordBlock, iter_keywords
from .read_ref_points import node_by_id
from .read_springs import is_spring_block

if TYPE_CHECKING:
    from ada.fem import FEM

_re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL


def _parse_int_grid(text: str) -> np.ndarray:
    """Flat int array from an Abaqus element-data block, tolerant of line continuations.

    An element whose connectivity is too long for one line continues on the next (the data
    line ends in a comma): ``10, 11, 12, 18,\\n17``. Naively swapping newlines for commas
    yields a ``,,`` and breaks ``np.fromstring``. Splitting on any run of commas/whitespace
    and dropping empties parses both the one-line and continued forms."""
    toks = [t for t in re.split(r"[,\s]+", text.strip()) if t]
    return np.array(toks, dtype=int)


def get_elem_from_bulk_str(bulk_str, fem: "FEM") -> FemElements:
    """Read and import all *Element flags"""
    elements = FemElements(
        chain.from_iterable(
            filter(lambda x: x is not None, (grab_elements(c, fem) for c in iter_keywords(bulk_str, "ELEMENT")))
        ),
        fem_obj=fem,
    )

    return elements


def grab_elements(block: KeywordBlock, fem: "FEM"):
    validate(block)
    eltype = block.params.get("TYPE")
    if is_spring_block(eltype):  # read_springs builds these, with their *Spring
        return None

    if eltype in ("CONN3D2",):
        logger.info(f'Importing Connector type "{eltype}"')

    if eltype in ("MASS", "ROTARYI"):
        logger.info(f'Importing Mass type "{eltype}"')

    try:
        ada_el_type = abaqus_el_type_to_ada(eltype)
    except UnsupportedAbaqusElementType as exc:
        # User-defined (``U1``, ``U2``, ...) and other element types
        # we haven't mapped yet shouldn't abort the entire deck. Skip
        # the block — the rest of the file still loads, and the audit
        # log line tells the operator which element type we dropped.
        # Returning ``None`` here is filtered out by
        # ``get_elem_from_bulk_str`` so no half-built elements leak.
        logger.warning("abaqus read: skipping element block — %s", exc)
        return None
    elset = block.params.get("ELSET")
    el_type_members_str = block.data_text
    res = re.search("[a-zA-Z]", el_type_members_str)
    is_cubic = ada_el_type in [SolidShapes.HEX20, SolidShapes.HEX27]
    if is_cubic or res is None:
        # A HEX20/27 connectivity spans two lines. There is no need to pair them up first:
        # _parse_int_grid flattens the whole block and the reshape below re-groups it by node
        # count, so the token order — and therefore the result — is the same either way.
        res = _parse_int_grid(el_type_members_str)
        n = ShapeResolver.get_el_nodes_from_type(ada_el_type) + 1
        return numpy_array_to_list_of_elements(res.reshape(int(res.size / n), n), eltype, elset, ada_el_type, fem)
    else:
        elems = []
        for li in el_type_members_str.splitlines():
            elem_nodes_str = li.split(",")
            elid = str_to_int(elem_nodes_str[0])
            elem_nodes = get_elem_nodes(elem_nodes_str, fem)
            if ada_el_type == Elem.EL_TYPES.CONNECTOR_SHAPES.CONNECTOR:
                # A connector, not a plain Elem with a connector shape -- as on the numeric path.
                # Assembly-level connectors name their nodes ``instance.node``, which lands here;
                # built as Elem they were invisible to everything that looks for Connector, so the
                # writer dropped them on the next write.
                n1, n2 = elem_nodes
                elems.append(Connector(next(con_names), elid, n1, n2, con_type=None, con_sec=None, parent=fem))
                continue
            elem = Elem(elid, elem_nodes, ada_el_type, elset, el_formulation_override=eltype, parent=fem)
            elems.append(elem)
        return elems


def get_elem_arrays(bulk_str: str):
    """Parse *Element blocks into per-type (el_ids, node-id conn) arrays without
    building Elem objects. Blocks needing object handling (cross-instance node refs,
    mass/rotaryi/connector) are returned as raw matches for the object fallback."""
    from collections import defaultdict

    # ctype -> (el_ids, conns, elsets, formulations). The shape is the block; the Abaqus type
    # each row was written as is kept per row, since several types share one shape.
    by_type: dict = defaultdict(lambda: ([], [], [], []))
    overflow: list = []

    for block in iter_keywords(bulk_str, "ELEMENT"):
        validate(block)
        eltype = block.params.get("TYPE")
        if is_spring_block(eltype):  # read_springs builds these, with their *Spring
            continue
        try:
            ada_el_type = abaqus_el_type_to_ada(eltype)
        except UnsupportedAbaqusElementType as exc:
            logger.warning("abaqus read: skipping element block — %s", exc)
            continue

        members = block.data_text
        elset = block.params.get("ELSET")
        is_cubic = ada_el_type in [SolidShapes.HEX20, SolidShapes.HEX27]
        has_letters = re.search("[a-zA-Z]", members) is not None
        if eltype in ("MASS", "ROTARYI", "CONN3D2") or (has_letters and not is_cubic):
            overflow.append(block)  # special / cross-instance -> object path
            continue

        res = _parse_int_grid(members)
        n = ShapeResolver.get_el_nodes_from_type(ada_el_type) + 1
        res2d = res.reshape(int(res.size / n), n)
        ids, conns, elsets, formulations = by_type[ada_el_type]
        ids.extend(int(x) for x in res2d[:, 0])
        conns.extend(res2d[:, 1:].tolist())
        elsets.extend([elset] * res2d.shape[0])
        formulations.extend([eltype] * res2d.shape[0])

    return by_type, overflow


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
            r = node_by_id(par_.fem, str_to_int(setr))
            if not isinstance(r, Node):
                raise ValueError("Node ID not found")
            elem_nodes.append(r)
        else:
            r = node_by_id(fem, str_to_int(d))
            if not isinstance(r, Node):
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
            n1, n2 = [node_by_id(fem, n) for n in e[1:]]
            con = Connector(next(con_names), el_id, n1, n2, con_type=None, con_sec=None, parent=fem)
            connectors.append(con)
        return connectors
    elif ada_el_type in (shape_def.MassTypes.MASS, shape_def.MassTypes.ROTARYI):
        # The mass element itself, as a Mass. Its values arrive with the *Mass / *Rotary Inertia
        # block that names its set (read_masses.get_mass fills them in); built as a plain Elem,
        # the reader then made a SECOND element for the mass, under a new id.
        masses = []
        for e in res_:
            m = Mass(
                elset or f"mass{int(e[0])}",
                [node_by_id(fem, n) for n in e[1:]],
                0.0,
                mass_type=ada_el_type,
                mass_id=int(e[0]),
                parent=fem,
            )
            # The *Element line's ELSET, as Elem gets it: build_sets makes the set from this, and
            # *Mass names that set.
            m._elset = elset
            masses.append(m)
        return masses
    else:
        return [
            Elem(
                e[0],
                [node_by_id(fem, n) for n in e[1:]],
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

    for block in iter_keywords(bulk_str, "CONNECTOR SECTION"):
        validate(block)
        if len(block.data_lines) < 2:
            logger.warning("abaqus read: *Connector Section (line %d) needs two data lines - skipping", block.lineno)
            continue
        behavior = block.params.first("BEHAVIOR", "BEHAVIOUR")
        csys_ref = block.data_lines[1].replace('"', "")
        # The connector is named after its element set: that is the name the writer gives the
        # set, so naming it after the behaviour (``cs_linear_1``) renamed it on every pass.
        name = block.params.get("ELSET")
        elset = fem.elsets[name]
        connector: Connector = elset.members[0]
        con_sec = fem.connector_sections[behavior]
        csys_ref = csys_ref[:-1] if csys_ref[-1] == "," else csys_ref
        csys = fem.lcsys[csys_ref]
        con_type = block.data_lines[0]
        if con_type[-1] == ",":
            con_type = con_type[:-1]

        connector.elset = elset
        connector.name = name
        connector.con_sec = con_sec
        connector.con_type = con_type
        connector.csys = csys
