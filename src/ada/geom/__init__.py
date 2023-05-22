from dataclasses import dataclass, field
from typing import Any

from ada.geom.booleans import BooleanResult
from ada.visit.colors import Color


@dataclass
class Geometry:
    id: int
    geometry: Any
    color: Color
    bool_operations: list[BooleanResult] = field(default_factory=list)
