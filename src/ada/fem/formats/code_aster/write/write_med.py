from __future__ import annotations

from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.config import Config
from ada.fem.shapes import definitions as shape_def

from ..common import ada_to_med_type
from ..elem_shapes import med_geometry_type
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
            # Point-mass / spring elements attach to a single node. The
            # ``Mass`` / ``Spring`` subclasses expose that node via
            # ``.members`` (populated when ``fem_set`` is assigned post-
            # construction — :attr:`Mass.fem_set.setter` writes
            # ``_members`` but leaves ``_nodes`` at whatever was passed
            # at construction time, which is ``None`` for the common
            # ``Mass(name, None, mass)`` flow). Cross-format readers
            # (e.g. Sesam → ada) emit MASS-typed elements as plain
            # ``Elem`` instances that only carry ``.nodes``. Read both
            # so either origin works without forcing one shape to copy
            # to the other.
            cells = np.array([_mass_or_spring_attach_id(el) for el in elements])
        else:
            cells = np.array(list(map(get_node_ids_from_element, elements)))

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
