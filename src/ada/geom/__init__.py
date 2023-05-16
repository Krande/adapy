from dataclasses import dataclass
from typing import Any

from ada import Placement
from ada.geom.booleans import BooleanResult


@dataclass
class Color:
    red: float
    green: float
    blue: float


@dataclass
class Geometry:
    geometry: Any
    placement: Placement
    boolean_result: BooleanResult
    color: Color
    opacity: float
