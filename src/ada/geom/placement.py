from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

import numpy as np

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


class Direction(Point):
    def __new__(cls, *iterable):
        obj = cls.create_ndarray(iterable)
        return obj

    def __array_finalize__(self, obj, *args, **kwargs):
        if obj is None:
            return

        self.id = getattr(obj, "id", None)

    def get_normalized(self) -> Direction:
        return self / np.linalg.norm(self)

    def get_length(self) -> float:
        return np.linalg.norm(self)

    def get_angle(self, other: Direction) -> float:
        return np.arccos(np.clip(np.dot(self.get_normalized(), other.get_normalized()), -1.0, 1.0))

    def is_parallel(self, other: Direction, angle_tol=1e-1) -> bool:
        a = self.get_angle(other)
        return True if abs(abs(a) - abs(np.pi)) < angle_tol or abs(abs(a) - 0.0) < angle_tol else False

    @staticmethod
    def from_points(p1: Point, p2: Point):
        return Direction(*(p2 - p1))

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
class IfcLocalPlacement:
    relative_placement: Axis2Placement3D
    placement_rel_to: IfcLocalPlacement | None = None


@dataclass
class Axis1Placement:
    location: Point
    axis: Direction
