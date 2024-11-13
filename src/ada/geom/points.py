from __future__ import annotations

import numpy as np


class Point(np.ndarray):
    precision = None

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
        if length == 1:
            if isinstance(iterable[0], np.ndarray):
                iterable = iterable[0]
                length = len(iterable)
            elif isinstance(iterable[0], (list, tuple)):
                iterable = iterable[0]
                length = len(iterable)
            else:
                raise ValueError(f"Input must have a length of 2 or 3. Got {length}")

        if length not in (2, 3):
            raise ValueError(f"Input must have a length of 2 or 3. Got {length}")

        if not all(isinstance(x, (float, int, np.int32, np.int64, np.float32)) for x in iterable):
            raise ValueError(f"All elements in the input must be of type float or int. Got {list(map(type, iterable))}")

        if cls.precision is not None:
            obj = np.round(np.asarray(iterable, dtype=float), cls.precision).view(cls)
        else:
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

    def is_equal(self, other, atol=1e-8):
        return np.allclose(self, other, atol=atol)

    def translate(self, dx, dy, dz):
        return Point(self.x + dx, self.y + dy, self.z + dz)

    @property
    def dim(self):
        return len(self)

    def get_3d(self) -> Point:
        """Returns self if it is a 3D point, or if self is 2d point a new 3d Point copy is returned."""
        if self.dim == 3:
            return self

        return Point(*self, 0)

    def __repr__(self):
        return f"Point({np.array2string(self, separator=', ')})"
