from itertools import chain

import meshio

from ada.api.containers import Nodes
from ada.api.nodes import Node
from ada.api.spatial import Assembly, Part
from ada.core.utils import Counter
from ada.fem import FEM, Elem
from ada.fem.containers import FemElements


def meshio_read_fem(fem_file, fem_name=None):
    """Import a FEM file using the meshio package"""

    mesh = meshio.read(fem_file)
    name = fem_name if fem_name is not None else "Part-1"
    fem = FEM(name)

    def to_node(data):
        return Node(data[1], data[0])

    point_ids = mesh.points_id if "points_id" in mesh.__dict__.keys() else [i + 1 for i, x in enumerate(mesh.points)]
    elem_counter = Counter(1)

    cell_ids = (
        mesh.cells_id
        if "cells_id" in mesh.__dict__.keys()
        else [[next(elem_counter) for cell in cellblock.data] for cellblock in mesh.cells]
    )
    fem.nodes = Nodes([to_node(p) for p in zip(point_ids, mesh.points)])

    cell_block_counter = Counter(0)

    def to_elem(cellblock):
        block_id = next(cell_block_counter)
        return [
            Elem(
                cell_ids[block_id][i],
                [fem.nodes.from_id(point_ids[c]) for c in cell],
                cellblock.type,
            )
            for i, cell in enumerate(cellblock.data)
        ]

    fem.elements = FemElements(chain.from_iterable(map(to_elem, mesh.cells)))
    return Assembly("TempAssembly") / Part(name, fem=fem)
