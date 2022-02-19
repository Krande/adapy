import numpy as np

from ada import FEM
from ada.fem.utils import is_line_elem

from .renderer_occ import occ_shape_to_faces


def convert_obj_to_poly(obj, quality=1.0, render_edges=False, parallel=False):
    geom = obj.solid
    np_vertices, poly_indices, np_normals, _ = occ_shape_to_faces(geom, quality, render_edges, parallel)
    obj_buffer_arrays = np.concatenate([np_vertices, np_normals], 1)
    buffer, indices = np.unique(obj_buffer_arrays, axis=0, return_index=False, return_inverse=True)
    return dict(
        guid=obj.guid,
        index=indices.astype(int).tolist(),
        position=buffer.flatten().astype(float).tolist(),
        color=[*obj.colour_norm, obj.opacity],
        instances=[],
    )


def get_vertices_from_fem(fem: FEM) -> np.ndarray:
    return np.asarray([n.p for n in fem.nodes.nodes], dtype="float32")


def get_faces_from_fem(fem: FEM):
    ids = []
    for el in fem.elements.elements:
        if is_line_elem(el):
            continue
        for f in el.shape.faces:
            # Convert to indices, not id
            ids += [[int(e.id - 1) for e in f]]
    return ids


def get_edges_from_fem(fem: FEM):
    ids = []
    for el in fem.elements.elements:
        for f in el.shape.edges_seq:
            # Convert to indices, not id
            ids += [[int(el.nodes[e].id - 1) for e in f]]
    return ids
