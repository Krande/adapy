from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING, Iterable

from ada.base.types import GeomRepr
from ada.cadit.sat.write import sat_entities as se
from ada.cadit.sat.write.sat_entities import SATEntity
from ada.cadit.sat.write.utils import IDGenerator
from ada.cadit.sat.write.write_plate import plate_to_sat_entities

if TYPE_CHECKING:
    from ada import Part, Assembly

HEADER_STR = """2000 0 1 0
18 SESAM - gmGeometry 14 ACIS 30.0.1 NT 24 Tue Jan 17 20:39:08 2023
1000 9.9999999999999995e-07 1e-10
"""


def part_to_sat_writer(part: Part | Assembly) -> SatWriter:
    from ada import Beam, Plate

    sw = SatWriter(part)

    # Beams
    for bm in part.get_all_physical_objects(by_type=Beam):
        pass

    # Plates
    for face_id, pl in enumerate(part.get_all_physical_objects(by_type=Plate), start=1):
        face_name = f"FACE{face_id:08d}"
        sw.face_map[face_name] = pl.guid
        new_entities = plate_to_sat_entities(pl, face_name, GeomRepr.SHELL, sw)
        for entity in new_entities:
            sw.add_entity(entity)

    return sw


@dataclass
class SatWriter:
    part: Part | Assembly
    entities: dict = field(default_factory=dict)
    header: str = HEADER_STR
    bbox: list[float] = field(default_factory=list)
    id_generator: IDGenerator = field(default_factory=IDGenerator)
    face_map: dict[str, str] = field(default_factory=dict)  # face_name -> plate guid

    def __post_init__(self):
        self.bbox = list(chain.from_iterable(zip(*self.part.nodes.bbox())))

    def add_entity(self, entity: SATEntity) -> None:
        self.entities[entity.entity_id] = entity

    def write(self, file_path: str | pathlib.Path) -> None:
        with open(file_path, "w") as f:
            f.write(self.header)
            for entity in self.entities.values():
                f.write(entity.to_string() + '\n')
            f.write("End-of-ACIS-data")

    def get_entities_by_type(self, by_type) -> list[SATEntity]:
        return list(filter(lambda x: type(x) is by_type, self.entities.values()))
