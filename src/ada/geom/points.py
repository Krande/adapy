from __future__ import annotations

import numpy as np


class Point(np.ndarray):
    def __new__(cls, *iterable):
        obj = cls.create_ndarray(iterable)

        return obj

    def __array_finalize__(self, obj, *args, **kwargs):
        if obj is None:
            return

    @classmethod
    def create_ndarray(cls, iterable):
        if not hasattr(iterable, "__iter__"):
            raise TypeError("Input must be an iterable.")

        length = len(iterable)

        if length not in (2, 3):
            raise ValueError("Input must have a length of 2 or 3.")

        if not all(isinstance(x, (float, int, np.int32, np.float32)) for x in iterable):
            raise ValueError(f"All elements in the input must be of type float or int. Got {list(map(type, iterable))}")

        obj = np.asarray(iterable, dtype=float).view(cls)

        return obj

    @property
    def x(self):
        return self[0]

    @property
    def y(self):
        return self[1]

    @property
    def z(self):
        return self[2]

    def is_equal(self, other):
        return np.allclose(self, other)

    def __repr__(self):
        return f"Point({np.array2string(self, separator=', ')})"
