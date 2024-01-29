import meshio
import numpy as np

from ada.config import get_logger
from ada.fem.shapes import ElemShape
from ada.fem.shapes import definitions as shape_def

logger = get_logger()


def get_edges_and_faces_from_meshio(mesh: meshio.Mesh):
    from ada.fem.formats.mesh_io.common import meshio_to_ada

    edges = []
    faces = []
    for cell_block in mesh.cells:
        el_type = meshio_to_ada[cell_block.type]
        for elem in cell_block.data:
            elem_shape = ElemShape(el_type, elem)
            edges += elem_shape.edges
            if isinstance(elem_shape.type, shape_def.LineShapes):
                continue
            faces += elem_shape.faces
    return edges, faces


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
