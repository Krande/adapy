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


@lru_cache(maxsize=1024)
def _length_cached(vec_tup: tuple[float, ...]) -> float:
    """Return - and cache - the Euclidean norm of a tuple-vector."""
    return float(np.linalg.norm(vec_tup))


@lru_cache(maxsize=1024)
def _angle_between_tuples(a_tup: tuple[float, ...], b_tup: tuple[float, ...]) -> float:
    """Compute and cache the angle (radians) between two vectors."""
    # normalize via the cached length
    la = _length_cached(a_tup)
    lb = _length_cached(b_tup)
    if la == 0.0 or lb == 0.0:
        raise ValueError("Cannot compute angle with zero‐length vector")
    dot = np.dot(a_tup, b_tup) / (la * lb)
    return float(np.arccos(np.clip(dot, -1.0, 1.0)))


@lru_cache(maxsize=1024)
def _is_parallel_tuples(a_tup: tuple[float, ...], b_tup: tuple[float, ...], angle_tol: float) -> bool:
    """Determine parallelism (within angle_tol) between two vectors."""
    ang = _angle_between_tuples(a_tup, b_tup)
    return abs(ang) < angle_tol or abs(abs(ang) - np.pi) < angle_tol


class Direction(Point):
    def __new__(cls, *iterable):
        obj = cls.create_ndarray(iterable)
        return obj

    def __array_finalize__(self, obj, *args, **kwargs):
        if obj is None:
            return

        self.id = getattr(obj, "id", None)

    def get_normalized(self) -> Direction:
        """
        Return (and cache) a unit‐length Direction.
        Subsequent calls reuse the cached result.
        """
        # check our own __dict__ for a stored value
        cached = self.__dict__.get("_normalized")
        if cached is None:
            length = np.linalg.norm(self)
            if length == 0.0:
                raise ValueError("Cannot normalize a zero‐length vector")
            # compute, cast back to Direction, and cache
            unit = (self / length).view(Direction)
            self.__dict__["_normalized"] = unit
            return unit
        return cached

    def get_length(self) -> float:
        """Return (and cache) self’s length."""
        length = self.__dict__.get("_length")
        if length is None:
            # call into our tuple‐based cache
            tup = tuple(self.tolist())
            length = _length_cached(tup)
            self.__dict__["_length"] = length
        return length

    def get_angle(self, other: Direction) -> float:
        """Return the angle (radians) between this and other."""
        a = tuple(self.tolist())
        b = tuple(other.tolist())
        return _angle_between_tuples(a, b)

    def is_parallel(self, other: Direction, angle_tol: float = 1e-1) -> bool:
        """True if vectors are parallel or antiparallel within angle_tol."""
        a = tuple(self.tolist())
        b = tuple(other.tolist())
        return _is_parallel_tuples(a, b, angle_tol)

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
