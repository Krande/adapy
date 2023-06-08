from __future__ import annotations

from ada.api.primitives import Shape
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.core.utils import Counter
from ada.geom.booleans import BoolOpEnum


class Boolean(BackendGeom):
    _name_gen = Counter(1, "Bool")
    """A boolean object applied to the parent object (and all preceding booleans in its boolean stack) 
    using one of the boolean operators; DIFFERENCE (default), UNION or INTERSECT.
    """

    def __init__(
        self,
        primitive,
        bool_op: BoolOpEnum = BoolOpEnum.DIFFERENCE,
        metadata=None,
        parent=None,
        units=Units.M,
        guid=None,
    ):
        if issubclass(type(primitive), Shape) is False:
            raise ValueError(f'Unsupported primitive type "{type(primitive)}"')

        super(Boolean, self).__init__(primitive.name, guid=guid, metadata=metadata, units=units)
        self._primitive = primitive
        self._bool_op = bool_op
        self._parent = parent
        self._ifc_opening = None

    @property
    def bool_op(self) -> BoolOpEnum:
        return self._bool_op

    @property
    def primitive(self):
        return self._primitive

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            self.primitive.units = value
            self._units = value

    def __repr__(self):
        return f"Bool(type={self.primitive})"
