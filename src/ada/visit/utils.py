from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List

import numpy as np

from ada import FEM
from ada.core.vector_transforms import rot_matrix
from ada.fem.utils import is_line_elem

if TYPE_CHECKING:
    from ada.visit.concept import ObjectMesh

_m3x3 = rot_matrix((0, -1, 0))
_m3x3_with_col = np.append(_m3x3, np.array([[0], [0], [0]]), axis=1)
m4x4_z_up_rot = np.r_[_m3x3_with_col, [np.array([0, 0, 0, 1])]]


def rotate_to_z_up(points: np.ndarray) -> np.ndarray:
    return points @ _m3x3.T


def rotate_back_from_z_up(points: np.ndarray) -> np.ndarray:
    return points @ _m3x3


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


def organize_by_colour(objects: Iterable[ObjectMesh]) -> Dict[tuple, List[ObjectMesh]]:
    colour_map: Dict[tuple, List[ObjectMesh]] = dict()
    for obj in objects:
        colour = tuple(obj.color) if obj.color is not None else None
        if colour not in colour_map.keys():
            colour_map[colour] = []
        colour_map[colour].append(obj)
    return colour_map


def merge_mesh_objects(list_of_objects: Iterable[ObjectMesh]) -> ObjectMesh:
    from ada.core.guid import create_guid

    from .concept import ObjectMesh

    obj_mesh = ObjectMesh(
        create_guid(),
        np.array([], dtype=int),
        np.array([], dtype=float),
        np.array([], dtype=float),
    )

    for obj in list_of_objects:
        obj_mesh += obj

    return obj_mesh
