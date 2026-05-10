from __future__ import annotations

import numpy as np

from ada.config import get_logger
from ada.fem.results.common import MeshData
from ada.fem.shapes import ElemShape
from ada.fem.shapes import definitions as shape_def

logger = get_logger()


def get_edges_and_faces_from_mesh_data(mesh: MeshData):
    from ada.fem.shapes.mesh_types import str_to_ada_type

    edges = []
    faces = []
    for cell_block in mesh.cells:
        el_type = str_to_ada_type[cell_block.type]
        for elem in cell_block.data:
            elem_shape = ElemShape(el_type, elem)
            edges += elem_shape.edges
            if isinstance(elem_shape.type, shape_def.LineShapes):
                continue
            # ``get_faces()`` (method) splits quad faces of HEX8/HEX20
            # into triangle pairs via hex_face_to_tris before flattening.
            # ``elem_shape.faces`` (property) does *not* — for a HEX mesh
            # it returns 24 indices per cell (6 quads × 4 indices) and
            # reshaping into (-1, 3) produces garbage triangulation
            # crossing quad diagonals incorrectly. Use the method.
            faces += elem_shape.get_faces()
    return edges, faces


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
