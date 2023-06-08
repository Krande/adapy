import numpy as np

from ada import PrimExtrude
from ada.api.walls import WallInsert
from ada.base.units import Units


class Window(WallInsert):
    def __init__(self, name, width, height, depth, **kwargs):
        super().__init__(name, width, height, depth, **kwargs)
        self._metadata["ifc_type"] = "IfcWindow"

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value == self._units:
            return

        scale_factor = Units.get_scale_factor(self._units, value)
        self.placement.origin = np.array([x * scale_factor for x in self.placement.origin])
        self._width *= scale_factor
        self._height *= scale_factor
        self._depth *= scale_factor
        self._shapes = []
        self.build_geom()
        self._units = value

    def build_geom(self):
        normal = self.placement.zdir
        origin = self.placement.origin - self.placement.zdir * self.depth
        points = [(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)]
        prim = PrimExtrude(
            self.name, points, self.depth, origin=origin, normal=normal, xdir=self.placement.xdir, parent=self
        )
        self.add_shape(prim)


class Door(WallInsert):
    def __init__(self, name, width, height, depth, units="m", **kwargs):
        super().__init__(name, width, height, depth, units=units, **kwargs)
        self._metadata["ifc_type"] = "IfcDoor"

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)
            self.placement.origin = np.array([x * scale_factor for x in self.placement.origin])
            self._width *= scale_factor
            self._height *= scale_factor
            self._depth *= scale_factor
            self._shapes = []
            self.build_geom()
            self._units = value

    @property
    def placement(self):
        return self._placement

    @placement.setter
    def placement(self, value):
        self._placement = value
        self.build_geom()

    def build_geom(self):
        origin = self.placement.origin - self.placement.zdir * self.depth
        points = [(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)]

        prim = PrimExtrude(
            self.name,
            points,
            self.depth,
            origin=origin,
            normal=self.placement.zdir,
            xdir=self.placement.xdir,
            parent=self,
        )

        self.add_shape(prim)
