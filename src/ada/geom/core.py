from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

from ada.geom.curves import CURVE_GEOM_TYPES
from ada.geom.solids import SOLID_GEOM_TYPES
from ada.geom.surfaces import SURFACE_GEOM_TYPES

if TYPE_CHECKING:
    from ada.geom.booleans import BooleanOperation
    from ada.visit.colors import Color

# Define a TypeVar that is constrained to specific geometry types
T = TypeVar("T", SOLID_GEOM_TYPES, SURFACE_GEOM_TYPES, CURVE_GEOM_TYPES)


@dataclass
class Geometry(Generic[T]):
    id: int | str
    geometry: T
    color: Color | None
    bool_operations: list[BooleanOperation] = field(default_factory=list)
