from __future__ import annotations

from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.config import Config
from ada.fem.shapes import definitions as shape_def

from ..common import ada_to_med_type
from .write_sets import _add_cell_sets, _add_node_sets

if TYPE_CHECKING:
    from ada.api.spatial import Part


def med_elements(part: Part, time_step: h5py.Group, profile: str, families: h5py.Group):
    """
    Add the following ['FAM', 'NOD', 'NUM'] to the 'MAI' group

    **NOD** requires 'CGT' and 'NBR' attrs
    """

    def get_node_ids_from_element(el_):
        return [int(n.id) for n in el_.nodes]

    elements_group = time_step.create_group("MAI")
    elements_group.attrs.create("CGT", 1)

    for group, elements in part.fem.elements.group_by_type():
        med_type = ada_to_med_type(group, part.fem.options.CODE_ASTER.use_reduced_integration)
        elements = list(elements)
        if isinstance(group, (shape_def.MassTypes, shape_def.SpringTypes)):
            cells = np.array([el.members[0].id for el in elements])
        else:
            cells = np.array(list(map(get_node_ids_from_element, elements)))

        if med_type in elements_group:
            raise ValueError(f"med_type {med_type} is already defined. rewrite is needed.")

        med_cells = elements_group.create_group(med_type)
        med_cells.attrs.create("CGT", 1)
        med_cells.attrs.create("CGS", 1)
        med_cells.attrs.create("PFL", np.bytes_(profile))

        nod = med_cells.create_dataset("NOD", data=cells.flatten(order="F"))
        nod.attrs.create("CGT", 1)
        nod.attrs.create("NBR", len(cells))

        # Node Numbering is necessary for proper handling of
        num = med_cells.create_dataset("NUM", data=[int(el.id) for el in elements])
        num.attrs.create("CGT", 1)
        num.attrs.create("NBR", len(cells))

    # Add Element sets
    if len(part.fem.elsets.keys()) > 0:
        _add_cell_sets(elements_group, part, families)


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

    if len(part.fem.nsets.keys()) > 0:
        _add_node_sets(nodes_group, part, points, families)

    assembly = part.get_assembly()

    if assembly != part and len(assembly.fem.nsets.keys()) > 0:
        _add_node_sets(nodes_group, assembly, points, families)
