from __future__ import annotations

import numpy as np


class Point(np.ndarray):
    def __new__(cls, iterable, pid=None):
        obj = cls.create_ndarray(iterable)

        if pid is not None and not isinstance(pid, int):
            raise TypeError("id must be an integer or None.")

        obj.id = pid

        return obj

    def __array_finalize__(self, obj, *args, **kwargs):
        if obj is None:
            return

        self.id = getattr(obj, "id", None)

    @classmethod
    def create_ndarray(cls, iterable):
        if not hasattr(iterable, "__iter__"):
            raise TypeError("Input must be an iterable.")

        length = len(iterable)

        if length not in (2, 3):
            raise ValueError("Input must have a length of 2 or 3.")

        if not all(isinstance(x, (float, int)) for x in iterable):
            raise ValueError("All elements in the input must be of type float or int.")

        obj = np.asarray(iterable, dtype=float).view(cls)

        return obj

    def __repr__(self):
        return f"Point({np.array2string(self, separator=', ')}, id={self.id})"
