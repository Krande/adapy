from dataclasses import dataclass

from ada.geom.points import Point
import numpy as np


class Direction(Point):
    def __new__(cls, iterable):
        obj = cls.create_ndarray(iterable)
        return obj

    def __array_finalize__(self, obj, *args, **kwargs):
        if obj is None:
            return

        self.id = getattr(obj, "id", None)

    def __repr__(self):
        return f"Vector({np.array2string(self, separator=', ')})"


@dataclass
class Axis2Placement3D:
    location: Point
    axis: Direction
    ref_direction: Direction
