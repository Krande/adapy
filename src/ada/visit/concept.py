from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from ada.config import logger


@dataclass
class ObjectMesh:
    guid: str
    faces: np.ndarray
    position: np.ndarray
    normal: np.ndarray | None = None
    color: list | None = None
    edges: np.ndarray = None
    vertex_color: np.ndarray = None
    instances: np.ndarray | None = None
    id_sequence: dict = field(default_factory=dict)
    translation: np.ndarray = None

    def translate(self, translation):
        self.position += translation

    @property
    def num_polygons(self):
        return int(len(self.faces) / 3)

    @property
    def bbox(self):
        return self.position.min(0), self.position.max(0)

    def to_trimesh(self) -> list[trimesh.Trimesh]:
        from trimesh.visual.material import PBRMaterial

        indices_shape = get_shape(self.faces)
        verts_shape = get_shape(self.position)

        if indices_shape == 1:
            faces = self.faces.reshape(int(len(self.faces) / 3), 3)
        else:
            faces = self.faces

        if verts_shape == 1:
            vertices = self.position.reshape(int(len(self.position) / 3), 3)
        else:
            vertices = self.position

        vertex_color = None
        if self.vertex_color is not None:
            verts_shape = get_shape(self.vertex_color)
            if verts_shape == 1:
                vcolor = self.vertex_color.reshape(int(len(self.vertex_color) / 3), 3)
            else:
                vcolor = self.vertex_color

            vertex_color = np.array([[i * 255 for i in x] + [1] for x in vcolor], dtype=np.uint8)
            # vertex_color = [int(x * 255) for x in self.vertex_color]

        # vertex_normals = self.normal
        new_mesh = trimesh.Trimesh(
            vertices=vertices,
            faces=faces,
            # vertex_normals=vertex_normals,
            metadata=dict(guid=self.guid),
            vertex_colors=vertex_color,
        )
        if vertex_color is not None:
            new_mesh.visual.material = PBRMaterial(doubleSided=True)

        if self.color is not None:
            needs_to_be_scaled = True
            for x in self.color:
                if x > 1.0:
                    needs_to_be_scaled = False

            if needs_to_be_scaled:
                base_color = [int(x * 255) for x in self.color[:3]] + [int(self.color[3])]
            else:
                base_color = self.color

            if vertex_color is None:
                new_mesh.visual.material = PBRMaterial(baseColorFactor=base_color)

        meshes = [new_mesh]
        if self.edges is not None:
            from trimesh.path.entities import Line

            shape_edges = get_shape(self.edges)
            if shape_edges == 1:
                reshaped = self.edges.reshape(int(len(self.edges) / 2), 2)
            elif shape_edges == 2:
                reshaped = self.edges
            else:
                raise NotImplementedError("Edges consisting of more than 2 vertices is not supported")

            entities = [Line(x) for x in reshaped]
            edge_mesh = trimesh.path.Path3D(entities=entities, vertices=vertices)
            meshes.append(edge_mesh)

        return meshes

    @property
    def normal_flat(self):
        return self.normal.astype(dtype="float32").flatten() if self.normal is not None else self.normal

    def __add__(self, other: ObjectMesh):
        pos_len = int(len(self.position))
        new_index = other.faces + pos_len
        ma = int((len(other.faces) + len(self.faces))) - 1
        mi = int(len(self.faces))

        if len(self.faces) == 0:
            self.faces = new_index
        else:
            self.faces = np.concatenate([self.faces, new_index])

        if len(self.position) == 0:
            self.position = other.position
        else:
            self.position = np.concatenate([self.position, other.position])

        if self.color is None:
            self.color = other.color
        else:
            if other.color[-1] == 1.0 and self.color[-1] != 1.0:
                logger.warning("Will merge colors with different opacity.")
                self.color[-1] = 1.0

        if self.translation is None and other.translation is not None:
            self.translation = other.translation

        if self.normal is None or other.normal is None:
            self.normal = None
        else:
            if len(self.normal) == 0:
                self.normal = other.normal
            else:
                self.normal = np.concatenate([self.normal, other.normal])

        self.id_sequence[other.guid] = (mi, ma)
        return self


def get_shape(np_array: np.ndarray) -> int:
    if len(np_array.shape) == 1:
        shape = len(np_array.shape)
    else:
        shape = np_array.shape[1]
    return shape
