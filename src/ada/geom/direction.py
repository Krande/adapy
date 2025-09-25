from __future__ import annotations

import weakref
from functools import lru_cache
from typing import Iterable

import numpy as np

from ada.geom.points import ImmutableNDArrayMixin, Point, _make_key_and_array


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


class Direction(np.ndarray, ImmutableNDArrayMixin):
    __array_priority__ = 10.0
    precision: int | None = None
    _cache: weakref.WeakValueDictionary[tuple[float, ...], Direction] = weakref.WeakValueDictionary()

    def __new__(cls, *coords: float | int | Iterable[float]) -> Direction:
        if len(coords) == 1 and isinstance(coords[0], Iterable) and not isinstance(coords[0], (str, bytes)):
            coords = tuple(coords[0])  # type: ignore
        key, arr = _make_key_and_array(coords, cls.precision, "Direction", (2, 3))
        inst = cls._cache.get(key)
        if inst is not None:
            return inst
        obj = arr.view(cls)
        obj.flags.writeable = False
        cls._cache[key] = obj
        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return

    @property
    def x(self) -> float:
        return float(self[0])

    @property
    def y(self) -> float:
        return float(self[1])

    @property
    def z(self) -> float:
        return float(self[2])

    def get_normalized(self) -> Direction:
        cached = self.__dict__.get("_normalized")
        if cached is None:
            length = np.linalg.norm(self)
            if length == 0.0:
                raise ValueError("Cannot normalize a zero‐length vector")
            unit = (self / length).view(Direction)
            unit.flags.writeable = False
            self.__dict__["_normalized"] = unit
            return unit
        return cached

    def get_length(self) -> float:
        length = self.__dict__.get("_length")
        if length is None:
            tup = (float(self[0]), float(self[1]), float(self[2]))
            length = _length_cached(tup)  # your existing cached helper
            self.__dict__["_length"] = length
        return length

    def get_angle(self, other: Direction, as_degrees=False) -> float:
        a = (float(self[0]), float(self[1]), float(self[2]))
        b = (float(other[0]), float(other[1]), float(other[2]))
        result = _angle_between_tuples(a, b)
        if as_degrees:
            return np.degrees(result)
        else:
            return result

    def is_parallel(self, other: Direction, angle_tol: float = 1e-1) -> bool:
        a = (float(self[0]), float(self[1]), float(self[2]))
        b = (float(other[0]), float(other[1]), float(other[2]))
        return _is_parallel_tuples(a, b, angle_tol)

    def is_equal(self, other: Direction, atol: float = 1e-6) -> bool:
        dx = abs(self[0] - other[0])
        dy = abs(self[1] - other[1])
        if dx > atol or dy > atol:
            return False
        if self.shape[0] == 3:
            dz = abs(self[2] - other[2])
            if dz > atol:
                return False
        return True

    @staticmethod
    def from_points(p1: Point, p2: Point) -> Direction:
        delta = p2 - p1
        return Direction(delta)

    def __add__(self, other: Direction | np.ndarray) -> Direction | np.ndarray:
        arr = super().__add__(other)
        if arr.ndim == 1 and arr.shape[0] == 3:
            return type(self)(arr)
        return arr

    def __sub__(self, other: Direction | np.ndarray) -> Direction | np.ndarray:
        arr = super().__sub__(other)
        if arr.ndim == 1 and arr.shape[0] == 3:
            return type(self)(arr)
        return arr

    __iadd__ = __add__
    __isub__ = __sub__

    def __repr__(self) -> str:
        return f"Direction({np.array2string(self, separator=', ')})"
