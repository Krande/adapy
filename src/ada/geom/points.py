from __future__ import annotations

import weakref
from typing import Iterable

import numpy as np


def _make_key_and_array(
    coords: tuple[int | float, ...],
    precision: int | None,
    name: str,
    allowed_dims: tuple[int, ...],
) -> tuple[tuple[float, ...], np.ndarray]:
    """
    Validate coords length in allowed_dims, apply rounding if needed,
    and return a key tuple plus the ndarray.
    """
    if len(coords) not in allowed_dims:
        dims = " or ".join(map(str, allowed_dims))
        raise ValueError(f"{name} requires {dims} coordinates, got {len(coords)}")
    arr = np.asarray(coords, float)
    if precision is not None:
        arr = np.round(arr, precision)
    key = tuple(float(x) for x in arr.tolist())
    return key, arr


class ImmutableNDArrayMixin:
    """Mixin to block in-place mutation on instances (assumes WRITEABLE=False)."""

    def __setitem__(self, idx, val):
        raise TypeError(f"{type(self).__name__} is immutable")

    def fill(self, *args, **kwargs):
        raise TypeError(f"{type(self).__name__} is immutable")

    def resize(self, *args, **kwargs):
        raise TypeError(f"{type(self).__name__} is immutable")

    def put(self, *args, **kwargs):
        raise TypeError(f"{type(self).__name__} is immutable")

    def itemset(self, *args, **kwargs):
        raise TypeError(f"{type(self).__name__} is immutable")


class Point(np.ndarray, ImmutableNDArrayMixin):
    precision: int | None = None
    _cache: weakref.WeakValueDictionary[tuple[float, ...], Point] = weakref.WeakValueDictionary()

    def __new__(cls, *coords: float | int) -> Point:
        # allow a single iterable (list, tuple, ndarray, etc.)
        if len(coords) == 1 and isinstance(coords[0], Iterable) and not isinstance(coords[0], (str, bytes)):
            coords = tuple(coords[0])  # type: ignore
        key, arr = _make_key_and_array(coords, cls.precision, "Point", (2, 3))
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
        if self.shape[0] < 3:
            raise AttributeError("2D Point has no z coordinate")
        return float(self[2])

    @property
    def dim(self) -> int:
        return self.shape[0]

    def is_equal(self, other: Point, atol: float = 1e-8) -> bool:
        return np.allclose(self, other, atol=atol)

    def translate(self, dx: float, dy: float, dz: float = 0.0) -> Point:
        base = (self[0], self[1], self[2] if self.dim == 3 else 0.0)
        return Point(*(b + d for b, d in zip(base, (dx, dy, dz))))

    def get_3d(self) -> Point:
        if self.dim == 3:
            return self
        return Point(self[0], self[1], 0.0)

    def __repr__(self) -> str:
        return f"Point({np.array2string(self, separator=', ')})"


def calculate_bounding_box(points: list[Point]):
    """Calculate the minimum and maximum coordinates for a list of points using numpy operations."""
    if not points:
        raise ValueError("Points list cannot be empty")

    # Stack points into a 2D array and use numpy's vectorized min/max operations
    points_array = np.array(points)
    min_coords = np.min(points_array, axis=0)
    max_coords = np.max(points_array, axis=0)

    return Point(min_coords), Point(max_coords)
