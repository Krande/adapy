from __future__ import annotations

import datetime
import json
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

    def move_objects_to_center(self, override_center=None):
        for pm in self.world:
            oc = override_center if override_center is not None else -self.vol_center
            pm.move_objects_to_center(oc)

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

    def to_custom_json(self, dest_path=None):
        output = {
            "name": self.name,
            "created": self.created,
            "project": self.project,
            "world": [x.to_custom_json() for x in self.world],
            "meta": self.meta,
        }
        if dest_path is None:
            return output

        with open(dest_path, "w") as f:
            json.dump(output, f)

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

    def translate(self, translation):
        self.position += translation

    @property
    def num_polygons(self):
        return int(len(self.index) / 3)

    @property
    def bbox(self):
        return self.position.min(0), self.position.max(0)

    def to_custom_json(self):
        normal = self.normal.astype(float).flatten().tolist() if self.normal is not None else self.normal
        translation = self.translation.astype(float).tolist() if self.translation is not None else None
        vert_color = self.vertexColor.astype(float).tolist() if self.vertexColor is not None else None
        return dict(
            index=self.index.astype(int).flatten().tolist(),
            position=self.position.astype(float).flatten().tolist(),
            normal=normal,
            color=self.color,
            vertexColor=vert_color,
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
