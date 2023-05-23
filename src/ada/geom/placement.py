from dataclasses import dataclass
from typing import Iterable

import numpy as np

from ada.geom.points import Point


class Direction(Point):
    def __new__(cls, *iterable):
        obj = cls.create_ndarray(iterable)
        return obj

    def __array_finalize__(self, obj, *args, **kwargs):
        if obj is None:
            return

        self.id = getattr(obj, "id", None)

    def __repr__(self):
        return f"Direction({np.array2string(self, separator=', ')})"


@dataclass
class Axis2Placement3D:
    location: Point | Iterable
    axis: Direction | Iterable
    ref_direction: Direction | Iterable

    def __post_init__(self):
        if isinstance(self.location, Iterable):
            self.location = Point(*self.location)
        if isinstance(self.axis, Iterable):
            self.axis = Direction(*self.axis)
        if isinstance(self.ref_direction, Iterable):
            self.ref_direction = Direction(*self.ref_direction)


@dataclass
class Axis1Placement:
    location: Point
    axis: Direction
