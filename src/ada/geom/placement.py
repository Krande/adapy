from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

import numpy as np

from ada.geom.direction import Direction
from ada.geom.points import Point


@lru_cache
def O() -> Point:  # noqa
    return Point(0, 0, 0)


@lru_cache
def XV() -> Direction:  # noqa
    return Direction(1, 0, 0)


@lru_cache
def YV() -> Direction:  # noqa
    return Direction(0, 1, 0)


@lru_cache
def ZV() -> Direction:  # noqa
    return Direction(0, 0, 1)


@dataclass
class Axis2Placement3D:

    location: Point | Iterable = field(default_factory=O)
    axis: Direction | Iterable = field(default_factory=ZV)
    ref_direction: Direction | Iterable = field(default_factory=XV)

    def __post_init__(self):
        if isinstance(self.location, Iterable):
            self.location = Point(*self.location)
        if isinstance(self.axis, Iterable):
            self.axis = Direction(*self.axis)
        if isinstance(self.ref_direction, Iterable):
            self.ref_direction = Direction(*self.ref_direction)

    def get_pdir(self):
        return Direction(*np.cross(self.axis, self.ref_direction))


@dataclass
class IfcLocalPlacement:
    relative_placement: Axis2Placement3D
    placement_rel_to: IfcLocalPlacement | None = None


@dataclass
class Axis1Placement:
    location: Point
    axis: Direction
