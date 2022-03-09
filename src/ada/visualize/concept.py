from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Union

import numpy as np


@dataclass
class AssemblyMesh:
    name: str

    project: str
    world: List[PartMesh]
    meta: Union[None, dict]
    created: str = None

    def __post_init__(self):
        if self.created is None:
            self.created = datetime.datetime.utcnow().strftime("%m/%d/%Y, %H:%M:%S")

    @property
    def vol_center(self):
        return (self.bbox[0] + self.bbox[1]) / 2

    @property
    def bbox(self):
        res = np.concatenate([np.array(x.bbox) for x in self.world])
        return res.min(0), res.max(0)

    @property
    def num_polygons(self):
        return sum([x.num_polygons for x in self.world])

    def to_custom_json(self):
        return {
            "name": self.name,
            "created": self.created,
            "project": self.project,
            "world": [x.to_custom_json() for x in self.world],
            "meta": self.meta,
        }

    def merge_objects_in_parts_by_color(self) -> AssemblyMesh:
        part_list = []
        for pmesh in self.world:
            part_list.append(pmesh.merge_by_color())
        return AssemblyMesh(name=self.name, created=self.created, project=self.project, world=part_list, meta=self.meta)

    def __add__(self, other: AssemblyMesh):
        new_meta = dict()
        if self.meta is not None:
            new_meta.update(self.meta)
        if other.meta is not None:
            new_meta.update(other.meta)
        return AssemblyMesh(name=self.name, project=self.project, world=self.world + other.world, meta=new_meta)


@dataclass
class PartMesh:
    name: str
    rawdata: bool
    id_map: Dict[str, ObjectMesh]
    guiparam: Union[None, dict] = None

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

        from .formats.assembly_mesh.merge_utils import merge_mesh_objects

        colour_map: Dict[tuple, List[ObjectMesh]] = dict()
        for obj in self.id_map.values():
            colour = tuple(obj.color) if obj.color is not None else None
            if colour not in colour_map.keys():
                colour_map[colour] = []
            colour_map[colour].append(obj)

        id_map = dict()
        for colour, elements in colour_map.items():
            guid = create_guid()
            pm = merge_mesh_objects(elements)
            if len(pm.index) == 0:
                continue
            id_map[guid] = pm

        return PartMesh(name=self.name, rawdata=True, id_map=id_map, guiparam=None)


@dataclass
class ObjectMesh:
    guid: str
    index: np.ndarray
    position: np.ndarray
    normal: Union[np.ndarray, None]
    color: Union[list, None] = None
    vertexColor: np.ndarray = None
    instances: Union[np.ndarray, None] = None
    id_sequence: dict = field(default_factory=dict)
    translation: np.ndarray = None

    @property
    def num_polygons(self):
        return int(len(self.index) / 3)

    @property
    def bbox(self):
        pos: np.ndarray = self.position.reshape(int(len(self.position) / 3), 3)
        return pos.min(0), pos.max(0)

    def __post_init__(self):
        pos_shape = np.shape(self.position)
        normal_shape = np.shape(self.normal)

        if len(pos_shape) > 1 and pos_shape[1] == 3:
            self.position = self.position.flatten().astype(float)
        if len(normal_shape) > 1 and normal_shape[1] == 3:
            self.normal = self.normal.flatten().astype(float)

    def to_custom_json(self):
        normal = self.normal.astype(float).tolist() if self.normal is not None else self.normal
        translation = self.translation.astype(float).tolist() if self.translation is not None else None
        return dict(
            index=self.index.astype(int).tolist(),
            position=self.position.astype(float).tolist(),
            normal=normal,
            color=self.color,
            vertexColor=self.vertexColor,
            instances=self.instances,
            id_sequence=self.id_sequence,
            translation=translation,
        )

    def __add__(self, other: ObjectMesh):
        pos_len = int(len(self.position) / 3)
        new_index = other.index + pos_len
        ma = int((len(other.index) + len(self.index))) - 1
        mi = int(len(self.index))

        self.index = np.concatenate([self.index, new_index])
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
            self.normal = np.concatenate([self.normal, other.normal])

        self.id_sequence[other.guid] = (mi, ma)
        return self
