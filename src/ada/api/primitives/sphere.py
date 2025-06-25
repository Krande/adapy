from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ada.api.primitives.base import Shape
from ada.base.units import Units
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation

if TYPE_CHECKING:
    from ada import Point


class PrimSphere(Shape):
    def __init__(self, name, cog: Iterable[float] | Point, radius, **kwargs):
        self.radius = radius
        super(PrimSphere, self).__init__(name=name, cog=cog, **kwargs)

    def geom_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.points import Point
        from ada.geom.solids import Sphere

        cog = self.cog.copy()
        if self.placement.is_identity() is False:
            place_abs = self.placement.get_absolute_placement(include_rotations=False)
            cog += place_abs.origin

        sphere = Sphere(Point(*cog), self.radius)
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, sphere, self.color, bool_operations=booleans)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)

            self.cog = [x * scale_factor for x in self.cog]
            self.radius = self.radius * scale_factor
            self._geom = self.geom_occ()
            self._units = value

    def __repr__(self):
        return f'PrimSphere("{self.name}", {self.cog.tolist()}, {self.radius})'
