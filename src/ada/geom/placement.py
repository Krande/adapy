from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from ada.geom.points import Point


def O() -> Point: # noqa
    return Point(0, 0, 0)


def XV() -> Direction: # noqa
    return Direction(1, 0, 0)


def YV() -> Direction: # noqa
    return Direction(0, 1, 0)


def ZV() -> Direction: # noqa
    return Direction(0, 0, 1)


class Direction(Point):
    def __new__(cls, *iterable):
        obj = cls.create_ndarray(iterable)
        return obj

    def __array_finalize__(self, obj, *args, **kwargs):
        if obj is None:
            return

        self.id = getattr(obj, "id", None)

    def get_normalised(self):
        return self / np.linalg.norm(self)

    def get_length(self):
        return np.linalg.norm(self)

    def __repr__(self):
        return f"Direction({np.array2string(self, separator=', ')})"


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
class Axis1Placement:
    location: Point
    axis: Direction
