from __future__ import annotations

from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.config import Config
from ada.fem.shapes import definitions as shape_def

from ..common import ada_to_med_type
from ..elem_shapes import med_geometry_type
from ..node_order import CODE_ASTER_ORDER
from .write_sets import _add_cell_sets, _add_node_sets

if TYPE_CHECKING:
    from ada.api.spatial import Part


def _mass_or_spring_attach_id(el) -> int:
    """Resolve the node id a Mass/Spring element attaches to.

    Tries ``.members`` first (proper :class:`ada.fem.Mass` /
    :class:`ada.fem.Spring` instances populate it via ``fem_set``
    assignment) and falls back to ``.nodes`` for plain ``Elem``
    instances produced by cross-format readers.
    """
    members = getattr(el, "members", None)
    if members:
        return int(members[0].id)
    return int(el.nodes[0].id)


def cell_node_ids(el) -> list[int]:
    """The node ids of ``el``'s MED cell: one for a point mass or grounded spring, two for a two-node spring, the
    element's own in MED order otherwise."""
    el_type = getattr(el, "type", None)
    if el_type == shape_def.SpringTypes.SPRING2:
        nodes = list(getattr(el, "nodes", None) or el.members)
        return [int(nodes[0].id), int(nodes[-1].id)]
    if isinstance(el_type, (shape_def.MassTypes, shape_def.SpringTypes)):
        return [_mass_or_spring_attach_id(el)]
    # MED orders SEG3 (end, end, mid) where native is (end, mid, end); every other shape MED carries matches native.
    return [int(n.id) for n in CODE_ASTER_ORDER.nodes_to_format(el_type, list(el.nodes))]


def element_groups(part: Part, extra=()) -> list[tuple[object, list]]:
    """``[(element type, elements)]`` of the part's mesh plus ``extra`` cells (a solid pressure's skin,
    :mod:`.write_skins`; the point cells of :mod:`.write_masses`), one group per MED cell type in one order: the cells
    and the family table must agree on it. The type named is the group's first; a group holds every adapy type of its
    MED type -- a point mass and a grounded spring are both ``PO1``, a beam and a two-node spring both ``SE2``, and a
    MED file holds one block per type ("med_type ... is already defined" when they came as two).

    A NONSTRUCTURAL mass is no cell: it is spread over its region (write_masses, MASS_REP)."""
    # The container's own grouping (an array-backed container groups its own way), with the extra cells merged in.
    groups = [(t, list(g)) for t, g in part.fem.elements.group_by_type() if t != shape_def.MassTypes.NONSTRUCTURAL]
    # An array-backed container groups its block rows only; a mass or spring with no row of its own (its ``_overflow``:
    # where a merge puts an assembly-level mass, and the readers theirs) is a cell too, or the group naming it is
    # missing from the mesh ("le GROUP_MA ... ne fait pas partie du maillage", measured)
    overflow = [
        el for el in getattr(part.fem.elements, "_overflow", ()) if el.type != shape_def.MassTypes.NONSTRUCTURAL
    ]
    extra = [*extra, *overflow]
    if extra:
        by_type = dict(groups)
        for el in extra:
            by_type.setdefault(el.type, []).append(el)
        groups = sorted(by_type.items(), key=lambda kv: kv[0])
    merged: dict[str, tuple[object, list]] = {}
    for el_type, elements in groups:
        key = ada_to_med_type(el_type)
        if key in merged:
            merged[key][1].extend(elements)
        else:
            merged[key] = (el_type, list(elements))
    return list(merged.values())


def med_elements(
    part: Part, time_step: h5py.Group, profile: str, families: h5py.Group, extra_elements=(), extra_sets=()
):
    """
    Add the following ['FAM', 'NOD', 'NUM'] to the 'MAI' group

    **NOD** requires 'CGT' and 'NBR' attrs
    """

    elements_group = time_step.create_group("MAI")
    elements_group.attrs.create("CGT", 1)

    for group, elements in element_groups(part, extra_elements):
        med_type = ada_to_med_type(group)
        elements = list(elements)
        # Per element: a group may hold several adapy types of one MED type (element_groups). A point mass or
        # grounded spring attaches to one node, read from ``.members`` (a Mass / Spring) or ``.nodes`` (the plain Elem
        # a cross-format reader makes) by _mass_or_spring_attach_id.
        cells = np.array([cell_node_ids(el) for el in elements])
        if cells.ndim == 2 and cells.shape[1] == 1:
            cells = cells[:, 0]

        if med_type in elements_group:
            raise ValueError(f"med_type {med_type} is already defined. rewrite is needed.")

        med_cells = elements_group.create_group(med_type)
        med_cells.attrs.create("CGT", 1)
        med_cells.attrs.create("CGS", 1)
        med_cells.attrs.create("PFL", np.bytes_(profile))

        # Add GEO attribute with the MED geometry type code
        if med_type in med_geometry_type:
            med_cells.attrs.create("GEO", med_geometry_type[med_type])

        nod = med_cells.create_dataset("NOD", data=cells.flatten(order="F"))
        nod.attrs.create("CGT", 1)
        nod.attrs.create("NBR", len(cells))

        # Node Numbering is necessary for proper handling of
        num = med_cells.create_dataset("NUM", data=[int(el.id) for el in elements])
        num.attrs.create("CGT", 1)
        num.attrs.create("NBR", len(cells))

    # Add Element sets
    if len(part.fem.elsets.keys()) > 0 or extra_sets:
        _add_cell_sets(elements_group, part, families, extra_elements, extra_sets)


def med_nodes(part: "Part", time_step, profile, families):
    """
    TODO: Go through each data group and set in HDF5 file and make sure that it writes what was read 1:1.
        Use cylinder.med as a benchmark.

    Add the following datasets ['COO', 'FAM', 'NUM'] to the 'NOE' group
    """

    points = np.zeros((int(part.fem.nodes.max_nid), 3))

    def pmap(n):
        points[int(n.id - 1)] = n.p

    list(map(pmap, part.fem.nodes))

    # Try this
    if Config().code_aster_ca_experimental_id_numbering is True:
        points = np.array([n.p for n in part.fem.nodes])

    nodes_group = time_step.create_group("NOE")
    nodes_group.attrs.create("CGT", 1)
    nodes_group.attrs.create("CGS", 1)

    nodes_group.attrs.create("PFL", np.bytes_(profile))
    coo = nodes_group.create_dataset("COO", data=points.flatten(order="F"))
    coo.attrs.create("CGT", 1)
    coo.attrs.create("NBR", len(points))

    if Config().code_aster_ca_experimental_id_numbering is True:
        node_ids = [n.id for n in part.fem.nodes]
        num = nodes_group.create_dataset("NUM", data=node_ids)
        num.attrs.create("CGT", 1)
        num.attrs.create("NBR", len(points))

    # Collect node sets from the part and (when distinct) the assembly, then write the
    # single MED "FAM" node-family dataset once — a per-source call collided on "FAM".
    assembly = part.get_assembly()
    fems = [part.fem]
    if assembly != part and len(assembly.fem.nsets.keys()) > 0:
        fems.append(assembly.fem)

    if any(len(f.nsets.keys()) > 0 for f in fems):
        _add_node_sets(nodes_group, fems, points, families)
