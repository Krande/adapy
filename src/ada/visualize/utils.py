import numpy as np

from ada import FEM
from ada.fem.utils import is_line_elem


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
