from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from ada.geom.booleans import BooleanResult, BooleanOperatorEnum
from ada.visit.colors import Color


@dataclass
class Geometry:
    id: int | str
    geometry: Any
    color: Color
    bool_operations: list[BooleanOperation] = field(default_factory=list)


@dataclass
class BooleanOperation:
    second_operand: Geometry
    operator: BooleanOperatorEnum
