from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Union

import h5py
import numpy as np
import trimesh

from ada.core.file_system import get_list_of_files

from .colors import VisColor


@dataclass
class VisMesh:
    """Visual Mesh"""

    name: str
    project: str = None
    world: List[PartMesh] = field(default_factory=list)
    meshes: Dict[str, VisNode] = field(default_factory=dict)
    meta: Union[None, dict] = None
    created: str = None
    translation: np.ndarray = None
    cache_file: pathlib.Path = pathlib.Path(".cache/meshes.h5")
    overwrite_cache: bool = False
    colors: Dict[str, VisColor] = field(default_factory=dict)

    def __enter__(self):
        logging.debug("Starting Visual Mesh session")
        os.makedirs(self.cache_file.parent, exist_ok=True)
        if self.overwrite_cache is True and self.cache_file.exists():
            os.remove(self.cache_file)
        self._h5cache = h5py.File(self.cache_file, "w")
        self._h5cache_group = self._h5cache.create_group("VISMESH")
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self._h5cache.close()
        self._h5cache = None
        self._h5cache_group = None

    def __post_init__(self):
        if self.created is None:
            self.created = datetime.datetime.utcnow().strftime("%m/%d/%Y, %H:%M:%S")

    def move_objects_to_center(self, override_center=None):
        self.translation = override_center if override_center is not None else -self.vol_center
        for pm in self.world:
            pm.move_objects_to_center(self.translation)

    def add_color(self, color: VisColor, primitive_guid):
        existing = self.colors.get(color.name, None)
        if existing:
            existing.used_by.append(primitive_guid)
            return existing
        self.colors[color.name] = color

        return color

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
    def bbox(self) -> Tuple[np.ndarray, np.ndarray]:
        res = np.concatenate([np.array(x.bbox) for x in self.world])
        return res.min(0), res.max(0)

    @property
    def num_polygons(self):
        return sum([x.num_polygons for x in self.world])

    def _get_mesh_obj_from_cache(self, guid, h5) -> ObjectMesh:
        obj_group = h5[guid]
        index = obj_group["INDEX"][()]
        position = obj_group["POSITION"][()]
        # normals = obj_group["NORMAL"][()]
        color_obj = self.colors[obj_group.attrs["COLOR"]]
        base_color = color_obj.pbrMetallicRoughness.baseColorFactor
        faces = index.reshape(int(len(index) / 3), 3)
        return ObjectMesh(guid, faces, position, None, base_color)

    def _convert_to_trimesh2(self, only_these_guids: List[str] = None) -> trimesh.Scene:
        scene = trimesh.Scene()
        from trimesh.visual.material import PBRMaterial

        h5_file = None
        if self._h5cache is None and self.cache_file.exists():
            h5_file = h5py.File(self.cache_file)
            h5 = h5_file["VISMESH"]
        else:
            h5 = self._h5cache_group
        totnum = len(self.meshes.keys())
        for i, vn in enumerate(self.meshes.values()):
            if only_these_guids is not None and vn.guid not in only_these_guids:
                continue
            obj_group = h5[vn.guid]
            index = obj_group["INDEX"][()]
            position = obj_group["POSITION"][()] * 1e-3
            normals = obj_group["NORMAL"][()]
            color = self.colors[obj_group.attrs["COLOR"]]
            # matrix = obj_group.attrs.get("MATRIX")
            if index.shape[1] != 3:
                index = index.reshape(int(len(index) / 3), 3)

            new_mesh = trimesh.Trimesh(
                vertices=position,
                faces=index,
                vertex_normals=normals,
                # face_colors=obj.color,
                metadata=dict(guid=vn.guid),
            )
            print(f"Exporting ({i} of {totnum})")
            base_color = [int(x * 255) for x in color.pbrMetallicRoughness.baseColorFactor]
            new_mesh.visual.material = PBRMaterial(baseColorFactor=base_color)
            scene.add_geometry(new_mesh, node_name=vn.guid, geom_name=vn.guid)

        if h5_file is not None:
            h5_file.close()

        return scene

    def _convert_to_trimesh(self) -> trimesh.Scene:
        scene = trimesh.Scene()
        from trimesh.visual.material import PBRMaterial

        for world in self.world:
            for key, obj in world.id_map.items():
                if len(obj.index.shape) == 1:
                    shape = len(obj.index.shape)
                else:
                    shape = obj.index.shape[1]

                if shape != 3:
                    faces = obj.index.reshape(int(len(obj.index) / 3), 3)
                else:
                    faces = obj.index
                vertices = obj.position
                vertex_normals = obj.normal
                new_mesh = trimesh.Trimesh(
                    vertices=vertices,
                    faces=faces,
                    vertex_normals=vertex_normals,
                    # face_colors=obj.color,
                    metadata=dict(guid=obj.guid),
                )
                if obj.color is not None:
                    base_color = [int(x * 255) for x in obj.color]
                    new_mesh.visual.material = PBRMaterial(baseColorFactor=base_color)
                scene.add_geometry(new_mesh, node_name=key, geom_name=key)
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

    def merge_meshes_by_color(self) -> List[str]:
        from ada.ifc.utils import create_guid

        h5_file = None
        if self._h5cache is None and self.cache_file.exists():
            h5_file = h5py.File(self.cache_file)
            h5 = h5_file["VISMESH"]
        else:
            h5 = self._h5cache_group

        listofobj = []
        for color in self.colors.values():
            obj0 = self._get_mesh_obj_from_cache(color.used_by[0], h5)
            tot_num = len(color.used_by[1:])
            for i, obj_guid in enumerate(color.used_by[1:]):
                print(f"Merging ({i} of {tot_num}) into color {color.name}")
                # TODO: Optimize this by unwrapping everything before concatenating
                obj1 = self._get_mesh_obj_from_cache(obj_guid, h5)
                obj0 += obj1

            new_guid = create_guid()

            self.add_mesh(new_guid, create_guid(), obj0.position, obj0.index, obj0.normal, color_ref=color.name)
            listofobj.append(new_guid)

        if self._h5cache is None and h5_file is not None:
            h5_file.close()

        return listofobj

    def to_gltf(self, dest_file, only_these_guids: List[str] = None):
        dest_file = pathlib.Path(dest_file).with_suffix(".glb")
        if hasattr(self, "_h5cache"):
            mesh: trimesh.Trimesh = self._convert_to_trimesh2(only_these_guids)
        else:
            mesh: trimesh.Trimesh = self._convert_to_trimesh()

        self._export_using_trimesh(mesh, dest_file)

    def to_cache(self, overwrite=False):
        import h5py

        os.makedirs(".cache", exist_ok=True)
        if self.cache_file.exists():
            if overwrite is False:
                print("Cache already exists and overwrite is not passed")
                return None
            os.remove(self.cache_file)

        with h5py.File(self.cache_file, "w") as f:
            vis_mesh_group = f.create_group("VISMESH")
            for world in self.world:
                for key, obj_mesh in world.id_map.items():
                    # TODO: add last modified date check for original element if cache exists
                    obj_group = vis_mesh_group.create_group(key)
                    obj_group.attrs.create("COLOR", obj_mesh.color)
                    transl = obj_mesh.translation if obj_mesh.translation is not None else np.array([0, 0, 0])
                    obj_group.attrs.create("TRANSLATION", transl)

                    obj_group.create_dataset("POSITION", data=obj_mesh.position)
                    # obj_group.create_dataset("NORMAL", data=obj_mesh.normal)
                    obj_group.create_dataset("INDEX", data=obj_mesh.index)

    def to_binary_and_json(self, dest_dir, auto_zip=True, export_dir=None, skip_normals=False):
        dest_dir = pathlib.Path(dest_dir)

        if dest_dir.exists():
            shutil.rmtree(dest_dir)

        wrld = []
        data_dir = dest_dir / "data"
        if data_dir.exists():
            shutil.rmtree(data_dir)

        for part in self.world:
            wrld_obj = {
                "name": part.name,
                "rawdata": part.rawdata,
                "guiParam": part.guiparam,
                "id_map": {
                    key: value.to_binary_json(dest_dir=data_dir, skip_normals=skip_normals)
                    for key, value in part.id_map.items()
                },
            }
            wrld.append(wrld_obj)

        output = {
            "name": self.name,
            "created": self.created,
            "project": self.project,
            "world": wrld,
            "meta": self.meta,
        }

        if dest_dir is None:
            return output

        os.makedirs(dest_dir, exist_ok=True)
        json_file = (dest_dir / self.name).with_suffix(".json")
        with open(json_file, "w") as f:
            json.dump(output, f)

        if auto_zip is True:
            import zipfile

            zip_dir = dest_dir / "export" if export_dir is None else pathlib.Path(export_dir)
            zip_data = zip_dir / "data"
            if zip_data.exists():
                shutil.rmtree(zip_data, ignore_errors=True)

            os.makedirs(zip_dir, exist_ok=True)
            os.makedirs(zip_data, exist_ok=True)

            for f in get_list_of_files(data_dir, ".npy"):
                fp = pathlib.Path(f)
                zfile = (zip_data / fp.stem).with_suffix(".zip")
                with zipfile.ZipFile(zfile, "w") as zip_archive:
                    zip_archive.write(fp, fp.name, compress_type=zipfile.ZIP_DEFLATED)

            zfile = (zip_dir / json_file.stem).with_suffix(".zip")
            with zipfile.ZipFile(zfile, "w") as zip_archive:
                zip_archive.write(json_file, json_file.name, compress_type=zipfile.ZIP_DEFLATED)

    def to_custom_json(self, dest_path=None, auto_zip=False):
        output = {
            "name": self.name,
            "created": self.created,
            "project": self.project,
            "world": [x.to_custom_json() for x in self.world],
            "meta": self.meta,
            "translation": self.translation.tolist() if self.translation is not None else None,
        }
        if dest_path is None:
            return output

        dest_path = pathlib.Path(dest_path).resolve().absolute()
        os.makedirs(dest_path.parent, exist_ok=True)

        with open(dest_path, "w") as f:
            json.dump(output, f)

        if auto_zip:
            import zipfile

            zfile = dest_path.with_suffix(".zip")
            with zipfile.ZipFile(zfile, "w") as zip_archive:
                zip_archive.write(dest_path, dest_path.name, compress_type=zipfile.ZIP_DEFLATED)

    def merge_objects_in_parts_by_color(self) -> VisMesh:
        to_be_merged_part = None
        for pmesh in self.world:
            if to_be_merged_part is None:
                to_be_merged_part = pmesh
                continue
            to_be_merged_part += pmesh
        if to_be_merged_part is None:
            logging.error(f"{self.name} has no parts!?. returning empty model")
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
    id_map: Dict[str, ObjectMesh]
    guiparam: Union[None, dict] = None
    rawdata: bool = True

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
            if len(pm.index) == 0:
                continue
            id_map[guid] = pm

        return PartMesh(name=self.name, id_map=id_map)

    def __add__(self, other: PartMesh):
        self.id_map.update(other.id_map)
        return self


@dataclass
class ObjectMesh:
    guid: str
    index: np.ndarray
    position: np.ndarray
    normal: Union[np.ndarray, None]
    color: Union[list, None] = None
    vertex_color: np.ndarray = None
    instances: Union[np.ndarray, None] = None
    id_sequence: dict = field(default_factory=dict)
    translation: np.ndarray = None

    def translate(self, translation):
        self.position += translation

    @property
    def num_polygons(self):
        return int(len(self.index) / 3)

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
            np.save(str(dest_dir / vertex_guid), self.vertex_color)

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

    @property
    def index_flat(self):
        return self.index.astype(dtype="int32").flatten()

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
        new_index = other.index + pos_len
        ma = int((len(other.index) + len(self.index))) - 1
        mi = int(len(self.index))

        self.index = np.concatenate([self.index, new_index])
        if len(self.position) == 0:
            self.position = other.position
        else:
            self.position = np.concatenate([self.position, other.position])

        if self.color is None:
            self.color = other.color
        else:
            if other.color[-1] == 1.0 and self.color[-1] != 1.0:
                logging.warning("Will merge colors with different opacity.")
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
