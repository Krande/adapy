from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Iterable, List

import numpy as np

from ada import FEM
from ada.fem.utils import is_line_elem

from .renderer_occ import occ_shape_to_faces

if TYPE_CHECKING:
    from ada.visualize.concept import ObjectMesh


def from_cache(hdf_file, guid):
    import h5py

    from .concept import ObjectMesh

    with h5py.File(hdf_file, "r") as f:
        res = f["VISMESH"].get(guid, None)
        if res is None:
            return None
        colour = list(res.attrs["COLOR"])
        translation = res.attrs["TRANSLATION"]
        return ObjectMesh(
            guid,
            res["INDEX"][()],
            res["POSITION"][()],
            res["NORMAL"][()],
            colour,
            translation=translation,
        )


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


def organize_by_colour(objects: Iterable[ObjectMesh]) -> Dict[tuple, List[ObjectMesh]]:
    colour_map: Dict[tuple, List[ObjectMesh]] = dict()
    for obj in objects:
        colour = tuple(obj.color) if obj.color is not None else None
        if colour not in colour_map.keys():
            colour_map[colour] = []
        colour_map[colour].append(obj)
    return colour_map


def merge_mesh_objects(list_of_objects: Iterable[ObjectMesh]) -> ObjectMesh:
    from ada.ifc.utils import create_guid

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
