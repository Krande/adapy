"""Ports: typed connection points (nozzles, terminals) on an Equipment.

A ``Port`` sits at an equipment-local position with an outward direction and a
service category. ``Equipment.add_port`` sets ``parent``; ``System.connect``
sets ``connected_system`` — together they form the bidirectional
equipment <-> system reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Iterable

from ada.core.guid import create_guid
from ada.geom.direction import Direction
from ada.geom.points import Point

from .categories import PortCategory

if TYPE_CHECKING:
    from ada.api.spatial.equipment import Equipment

    from .base import System

__all__ = ["Port", "PortDirection"]


class PortDirection(str, Enum):
    IN = "IN"
    OUT = "OUT"
    INOUT = "INOUT"


@dataclass
class Port:
    name: str
    position: Point | Iterable[float]
    direction_vector: Direction | Iterable[float]
    direction: PortDirection = PortDirection.INOUT
    category: PortCategory = "process"
    parent: Equipment | None = field(default=None, repr=False)
    connected_system: System | None = field(default=None, repr=False)
    guid: str = field(default_factory=create_guid, repr=False)

    def __post_init__(self):
        if not isinstance(self.position, Point):
            self.position = Point(*self.position)
        if not isinstance(self.direction_vector, Direction):
            self.direction_vector = Direction(*self.direction_vector)

    def get_global_position(self) -> Point:
        """World position of the port. Note: adds the parent's origin only —
        equipment rotation is not modeled (Equipment carries no Placement frame)."""
        if self.parent is None:
            return self.position
        return Point(*(self.parent.origin + self.position))

    @property
    def is_connected(self) -> bool:
        return self.connected_system is not None

    def __repr__(self) -> str:
        parent = self.parent.name if self.parent is not None else None
        system = self.connected_system.name if self.connected_system is not None else None
        return (
            f"Port({self.name!r}, category={self.category!r}, direction={self.direction.value}, "
            f"parent={parent!r}, connected_system={system!r})"
        )
