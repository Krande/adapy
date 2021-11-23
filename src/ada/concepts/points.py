from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, List, Union

import numpy as np

if TYPE_CHECKING:
    from ada import Beam
    from ada.fem import Bc, Csys, Elem

numeric = Union[int, float, np.number]


class Node:
    """Base node object"""

    def __init__(
        self, p: Iterable[numeric, numeric, numeric], nid=None, bc=None, r=None, parent=None, units="m", refs=None
    ):
        self._id = nid
        self.p: np.ndarray = np.array([*p], dtype=np.float64) if type(p) != np.ndarray else p
        if len(self.p) != 3:
            raise ValueError("Node object must have exactly 3 coordinates (x, y, z).")
        self._bc = bc
        self._r = r
        self._parent = parent
        self._units = units
        self._refs = [] if refs is None else refs

    @property
    def id(self) -> int:
        return self._id

    @id.setter
    def id(self, value):
        self._id = value

    @property
    def x(self):
        return self.p[0]

    @property
    def y(self):
        return self.p[1]

    @property
    def z(self):
        return self.p[2]

    @property
    def bc(self) -> "Bc":
        return self._bc

    @bc.setter
    def bc(self, value):
        self._bc = value

    @property
    def r(self) -> float:
        return self._r

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.core.utils import roundoff, unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)
            self.p = np.array([roundoff(scale_factor * x) for x in self.p])

            if self._r is not None:
                self._r *= scale_factor
            self._units = value

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def refs(self) -> List[Union["Elem", "Beam", "Csys"]]:
        return self._refs

    def __getitem__(self, index):
        return self.p[index]

    def __gt__(self, other):
        return tuple(self.p) > tuple(other.p)

    def __lt__(self, other):
        return tuple(self.p) < tuple(other.p)

    def __ge__(self, other):
        return tuple(self.p) >= tuple(other.p)

    def __le__(self, other):
        return tuple(self.p) <= tuple(other.p)

    def __eq__(self, other):
        if not isinstance(other, Node):
            return NotImplemented
        return (*self.p, self.id) == (*other.p, other.id)

    def __ne__(self, other):
        if not isinstance(other, Node):
            return NotImplemented
        return (*self.p, self.id) != (*other.p, other.id)

    def __hash__(self):
        return hash((*self.p, self.id))

    def __repr__(self):
        return f"Node([{self.x}, {self.y}, {self.z}], {self.id})"
