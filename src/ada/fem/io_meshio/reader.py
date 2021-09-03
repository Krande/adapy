from itertools import chain

import meshio

from ada.concepts.containers import Nodes
from ada.concepts.levels import FEM, Assembly, Part
from ada.concepts.points import Node
from ada.core.utils import Counter
from ada.fem import Elem
from ada.fem.containers import FemElements
from ada.fem.shapes.mesh_types import meshio_to_abaqus_type


def meshio_read_fem(assembly: Assembly, fem_file, fem_name=None):
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
                meshio_to_abaqus_type[cellblock.type],
            )
            for i, cell in enumerate(cellblock.data)
        ]

    fem.elements = FemElements(chain.from_iterable(map(to_elem, mesh.cells)))
    assembly.add_part(Part(name, fem=fem))
