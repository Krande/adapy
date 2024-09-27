from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.geom.booleans import BooleanOperation
    from ada.geom.curves import CURVE_GEOM_TYPES
    from ada.geom.solids import SOLID_GEOM_TYPES
    from ada.geom.surfaces import SURFACE_GEOM_TYPES
    from ada.visit.colors import Color


@dataclass
class Geometry:
    id: int | str
    geometry: SOLID_GEOM_TYPES | SURFACE_GEOM_TYPES | CURVE_GEOM_TYPES
    color: Color | None
    bool_operations: list[BooleanOperation] = field(default_factory=list)
