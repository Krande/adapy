import numpy as np

from ada.base import Backend
from ada.fem import Bc


class Node:
    """
    Base node object

    :param p: Array of [x, y, z] coords
    :param nid: Node id
    :param bc: Boundary condition
    :param r: Radius
    :param parent: Parent object
    """

    def __init__(self, p, nid=None, bc=None, r=None, parent=None, units="m"):
        self._id = nid
        self.p = np.array([*p], dtype=np.float64) if type(p) != np.ndarray else p
        if len(self.p) != 3:
            raise ValueError("Node object must have exactly 3 coordinates (x, y, z).")
        self._bc = bc
        self._r = r
        self._parent = parent
        self._units = units
        self._refs = []

    @property
    def id(self):
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
    def bc(self) -> Bc:
        return self._bc

    @bc.setter
    def bc(self, value):
        self._bc = value

    @property
    def r(self):
        return self._r

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            scale_factor = Backend._unit_conversion(self._units, value)
            self.p *= scale_factor
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
    def refs(self):
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
