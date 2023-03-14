from __future__ import annotations

import datetime
import os
import pathlib
from dataclasses import dataclass, field

import numpy as np
import trimesh

from ada.config import get_logger

from .colors import VisColor

logger = get_logger()


@dataclass
class VisMesh:
    """Visual Mesh"""

    name: str
    project: str = None
    world: list[PartMesh] = field(default_factory=list, repr=False)
    meshes: dict[str, VisNode] = field(default_factory=dict, repr=False)
    meta: None | dict = field(default=None, repr=False)
    created: str = None
    translation: np.ndarray = None
    cache_file: pathlib.Path = field(default=pathlib.Path(".cache/meshes.h5"), repr=False)
    overwrite_cache: bool = False
    colors: dict[str, VisColor] = field(default_factory=dict)
    merged: bool = False

    def __post_init__(self):
        if self.created is None:
            self.created = datetime.datetime.utcnow().strftime("%m/%d/%Y, %H:%M:%S")

    def move_objects_to_center(self, override_center=None):
        self.translation = override_center if override_center is not None else -self.vol_center
        for pm in self.world:
            pm.move_objects_to_center(self.translation)

    def add_mesh(self, guid, parent_guid, position, indices, normals=None, matrix=None, color_ref=None):
        obj_group = self._h5cache_group.create_group(guid)
        obj_group.attrs.create("COLOR", color_ref)
        if matrix is not None:
            obj_group.attrs.create("MATRIX", matrix)
        obj_group.create_dataset("POSITION", data=position)
        obj_group.create_dataset("NORMAL", data=normals)
        obj_group.create_dataset("INDEX", data=indices)
        self.meshes[guid] = VisNode(guid, parent_guid)

    @property
    def vol_center(self) -> np.ndarray:
        return (self.bbox[0] + self.bbox[1]) / 2

    @property
    def bbox(self) -> tuple[np.ndarray, np.ndarray]:
        res = np.concatenate([np.array(x.bbox) for x in self.world])
        return res.min(0), res.max(0)

    @property
    def num_polygons(self):
        return sum([x.num_polygons for x in self.world])

    def _convert_to_trimesh(self, embed_meta=True) -> trimesh.Scene:
        scene = trimesh.Scene()
        meta_set = set(self.meta.keys())

        id_sequence = dict()

        for world in self.world:
            world_map_set = set(world.id_map.keys())
            res = meta_set - world_map_set
            if self.merged is False:
                for spatial_node in res:
                    spatial_name, spatial_parent = self.meta.get(spatial_node)
                    scene.graph.update(
                        frame_to=spatial_name, frame_from=spatial_parent if spatial_parent != "*" else None
                    )

            for key, obj in world.id_map.items():
                if self.merged is False:
                    name, parent_guid = self.meta.get(key)
                    parent_name, _ = self.meta.get(parent_guid)
                else:
                    name = key
                    parent_name = "world"

                for i, new_mesh in enumerate(obj.to_trimesh()):
                    name = name if i == 0 else f"{name}_{i:02d}"
                    scene.add_geometry(new_mesh, node_name=name, geom_name=name, parent_node_name=parent_name)
                    id_sequence[name] = obj.id_sequence

        if embed_meta:
            scene.metadata["meta"] = self.meta
            scene.metadata["id_sequence"] = id_sequence

        return scene

    def _export_using_trimesh(self, mesh: trimesh.Scene, dest_file: pathlib.Path):
        os.makedirs(dest_file.parent, exist_ok=True)
        print(f'Writing Visual Mesh to "{dest_file}"')
        with open(dest_file, "wb") as f:
            mesh.export(file_obj=f, file_type=dest_file.suffix[1:])

    def to_stl(self, dest_file):
        dest_file = pathlib.Path(dest_file).with_suffix(".stl")
        mesh: trimesh.Trimesh = self._convert_to_trimesh()
        self._export_using_trimesh(mesh, dest_file)

    def to_gltf(self, dest_file, only_these_guids: list[str] = None, embed_meta=False):
        from ada.core.vector_utils import rot_matrix

        dest_file = pathlib.Path(dest_file).with_suffix(".glb")
        mesh: trimesh.Trimesh = self._convert_to_trimesh(embed_meta=embed_meta)

        # Trimesh automatically transforms by setting up = Y. This will counteract that transform
        m3x3 = rot_matrix((0, -1, 0))
        m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
        m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
        mesh.apply_transform(m4x4)

        self._export_using_trimesh(mesh, dest_file)

    def merge_objects_in_parts_by_color(self) -> VisMesh:
        to_be_merged_part = None
        for pmesh in self.world:
            if to_be_merged_part is None:
                to_be_merged_part = pmesh
                continue
            to_be_merged_part += pmesh
        if to_be_merged_part is None:
            logger.error(f"{self.name} has no parts!?. returning empty model")
            merged_part = []
        else:
            merged_part = to_be_merged_part.merge_by_color()

        return VisMesh(
            name=self.name,
            created=self.created,
            project=self.project,
            world=[merged_part],
            meta=self.meta,
            translation=self.translation,
            merged=True,
        )

    def __add__(self, other: VisMesh):
        new_meta = dict()
        if self.meta is not None:
            new_meta.update(self.meta)
        if other.meta is not None:
            new_meta.update(other.meta)
        return VisMesh(
            name=self.name,
            project=self.project,
            world=self.world + other.world,
            meta=new_meta,
        )


@dataclass
class PartMesh:
    name: str
    id_map: dict[str, ObjectMesh]

    def move_objects_to_center(self, override_center=None):
        for omesh in self.id_map.values():
            oc = override_center if override_center is not None else self.vol_center
            omesh.translate(oc)

    @property
    def vol_center(self):
        return (self.bbox[0] + self.bbox[1]) / 2

    @property
    def bbox(self):
        res = np.concatenate([np.array(x.bbox) for x in self.id_map.values()])
        return res.min(0), res.max(0)

    @property
    def num_polygons(self):
        return sum([x.num_polygons for x in self.id_map.values()])

    def to_custom_json(self):
        return {
            "name": self.name,
            "rawdata": self.rawdata,
            "guiParam": self.guiparam,
            "id_map": {key: value.to_custom_json() for key, value in self.id_map.items()},
        }

    def merge_by_color(self):
        from ada.ifc.utils import create_guid

        from .utils import merge_mesh_objects, organize_by_colour

        colour_map = organize_by_colour(self.id_map.values())

        id_map = dict()
        for colour, elements in colour_map.items():
            guid = create_guid()
            pm = merge_mesh_objects(elements)
            if len(pm.faces) == 0:
                continue
            id_map[guid] = pm

        return PartMesh(name=self.name, id_map=id_map)

    def __add__(self, other: PartMesh):
        self.id_map.update(other.id_map)
        return self


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

    def to_binary_json(self, dest_dir, skip_normals=False):
        from ada.ifc.utils import create_guid

        dest_dir = pathlib.Path(dest_dir).resolve().absolute()
        pos_guid = create_guid()
        norm_guid = create_guid()
        index_guid = create_guid()
        vertex_guid = create_guid() if self.vertex_color is not None else None
        os.makedirs(dest_dir, exist_ok=True)

        np.save(str(dest_dir / pos_guid), self.position_flat)
        if skip_normals is False:
            np.save(str(dest_dir / norm_guid), self.normal_flat)
        np.save(str(dest_dir / index_guid), self.index_flat)

        if vertex_guid is not None:
            np.save(str(dest_dir / vertex_guid), self.vertex_color.astype(dtype="float32").flatten())

        return dict(
            index=index_guid,
            position=pos_guid,
            normal=norm_guid if skip_normals is False else None,
            color=self.color,
            vertexColor=vertex_guid if vertex_guid is not None else None,
            instances=self.instances,
            id_sequence=self.id_sequence,
            translation=self.translation_norm,
        )

    def to_custom_json(self):
        return dict(
            index=self.index_norm_flat,
            position=self.position_norm_flat,
            normal=self.normal_norm_flat,
            color=self.color,
            vertexColor=self.vertex_color_norm,
            instances=self.instances,
            id_sequence=self.id_sequence,
            translation=self.translation_norm,
        )

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
    def index_flat(self):
        return self.faces.astype(dtype="int32").flatten()

    @property
    def index_norm_flat(self):
        return self.index_flat.astype(dtype="int32").tolist()

    @property
    def position_flat(self):
        return self.position.astype(dtype="float32").flatten()

    @property
    def position_norm_flat(self):
        return self.position_flat.tolist()

    @property
    def normal_flat(self):
        return self.normal.astype(dtype="float32").flatten() if self.normal is not None else self.normal

    @property
    def normal_norm_flat(self):
        return self.normal_flat.tolist() if self.normal is not None else self.normal

    @property
    def vertex_color_norm(self):
        return self.vertex_color.astype(dtype="float32").tolist() if self.vertex_color is not None else None

    @property
    def translation_norm(self):
        return self.translation.astype(dtype="float32").tolist() if self.translation is not None else None

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


@dataclass
class VisNode:
    guid: str
    parent: str


def get_shape(np_array: np.ndarray) -> int:
    if len(np_array.shape) == 1:
        shape = len(np_array.shape)
    else:
        shape = np_array.shape[1]
    return shape
