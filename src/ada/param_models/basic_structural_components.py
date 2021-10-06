import numpy as np

from ada import CurvePoly, Part, Shape


class Window(Part):
    def __init__(self, name, width, height, depth, **kwargs):
        super().__init__(name, **kwargs)
        self._metadata["ifc_type"] = "IfcWindow"
        self._width = width
        self._height = height
        self._depth = depth

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def depth(self):
        return self._depth

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.core.utils import unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)
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
        poly = CurvePoly(points2d=points, origin=origin, normal=normal, xdir=self.placement.xdir, parent=self)
        geom = poly.make_extruded_solid(self.depth)
        self.add_shape(Shape(self.name, geom, metadata=self.metadata))


class Door(Part):
    def __init__(self, name, width, height, depth, units="m", **kwargs):
        super().__init__(name, units=units, **kwargs)
        self._metadata["ifc_type"] = "IfcDoor"
        self._width = width
        self._height = height
        self._depth = depth

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def depth(self):
        return self._depth

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.core.utils import unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)
            self.placement.origin = np.array([x * scale_factor for x in self.placement.origin])
            self._width *= scale_factor
            self._height *= scale_factor
            self._depth *= scale_factor
            self._shapes = []
            self.build_geom()
            self._units = value

    def build_geom(self):
        origin = self.placement.origin - self.placement.zdir * self.depth
        points = [(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)]
        poly = CurvePoly(
            points2d=points, origin=origin, normal=self.placement.zdir, xdir=self.placement.xdir, parent=self
        )
        geom = poly.make_extruded_solid(self.depth)
        self.add_shape(Shape(self.name, geom, metadata=self.metadata))
