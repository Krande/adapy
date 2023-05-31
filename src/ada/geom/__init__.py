from __future__ import annotations

from dataclasses import dataclass, field

from ada.geom.booleans import BooleanOperatorEnum, BooleanResult
from ada.geom.solids import SOLID_GEOM_TYPES
from ada.geom.surfaces import SURFACE_GEOM_TYPES
from ada.visit.colors import Color


@dataclass
class Geometry:
    id: int | str
    geometry: SOLID_GEOM_TYPES | SURFACE_GEOM_TYPES
    color: Color
    bool_operations: list[BooleanOperation] = field(default_factory=list)


@dataclass
class BooleanOperation:
    second_operand: Geometry
    operator: BooleanOperatorEnum
