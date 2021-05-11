from dataclasses import dataclass

import numpy as np
from pythreejs import Group

import ada.fem

from .common import (
    get_bounding_box,
    get_edges_from_fem,
    get_faces_from_fem,
    get_vertices_from_fem,
)
from .threejs_geom import edges_to_mesh, faces_to_mesh, vertices_to_mesh


@dataclass
class ViewItem:
    fem: ada.fem.FEM
    vertices: np.array
    edges: np.array
    faces: np.array


class BBox:
    max: list
    min: list
    center: list


class FemRenderer:
    def __init__(self):
        self._view_items = []
        self._meshes = []

        # the group of 3d and 2d objects to render
        self._displayed_pickable_objects = Group()

    def add_fem(self, fem):
        vertices, faces, edges = get_vertices_from_fem(fem), get_faces_from_fem(fem), get_edges_from_fem(fem)
        self._view_items.append(ViewItem(fem, vertices, edges, faces))

    def _view_to_mesh(
        self, vt, face_colors=None, vertex_colors=(8, 8, 8), edge_color=(8, 8, 8), edge_width=1, vertex_width=1
    ):
        """

        :param vt:
        :type vt: ViewItem
        :return:
        """
        fem = vt.fem
        vertices = vt.vertices
        edges = vt.edges
        faces = vt.faces

        vertices_m = vertices_to_mesh(f"{fem.name}_vertices", vertices, vertex_colors, vertex_width)
        edges_m = edges_to_mesh(f"{fem.name}_edges", vertices, edges, edge_color=edge_color, linewidth=edge_width)
        face_geom, faces_m = faces_to_mesh(f"{fem.name}_faces", vertices, faces, colors=face_colors)

        return vertices_m, edges_m, faces_m

    def get_bounding_box(self):
        bounds = np.asarray([get_bounding_box(m) for m in self._meshes], dtype="float32")
        mi, ma = np.min(bounds, 0), np.max(bounds, 0)
        center = (mi + ma) / 2
        return mi, ma, center
