from __future__ import annotations

import numpy as np
from OCC.Core.TopoDS import TopoDS_Shape

from ada.api.primitives.base import Shape
from ada.base.units import Units
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation


class PrimCone(Shape):
    def __init__(self, name, p1, p2, r, **kwargs):
        self.p1 = np.array(p1)
        self.p2 = np.array(p2)
        self.r = r
        super(PrimCone, self).__init__(name, geom=None, **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)

        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)
            self.p1 = [x * scale_factor for x in self.p1]
            self.p2 = [x * scale_factor for x in self.p2]
            self.r = self.r * scale_factor
            self._units = value

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.points import Point
        from ada.geom.solids import Cone

        cone = Cone.from_2points(Point(*self.p1), Point(*self.p2), self.r)
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, cone, self.color, bool_operations=booleans)

    def __repr__(self):
        p1s = self.p1.tolist()
        p2s = self.p2.tolist()
        return f'PrimCone("{self.name}", {p1s}, {p2s}, {self.r})'
