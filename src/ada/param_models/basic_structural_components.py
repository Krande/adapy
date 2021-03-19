from ada import CurvePoly, Part, Shape


class Window(Part):
    def __init__(self, name, width, height, depth, **kwargs):
        origin = (0, 0, 0)
        lx = (1, 0, 0)
        ly = (0, 1, 0)
        lz = (0, 0, 1)
        super().__init__(name, origin=origin, lx=lx, ly=ly, lz=lz, **kwargs)
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
            scale_factor = self._unit_conversion(self._units, value)
            self._origin = tuple([x * scale_factor for x in self._origin])
            self._width *= scale_factor
            self._height *= scale_factor
            self._depth *= scale_factor
            self._shapes = []
            self.build_geom()
            self._units = value

    def build_geom(self):
        normal = self._lz
        origin = self.origin - self._lz * self.depth
        points = [(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)]
        poly = CurvePoly(points2d=points, origin=origin, normal=normal, xdir=self._lx, parent=self)
        geom = poly.make_extruded_solid(self.depth)
        self.add_shape(Shape(self.name, geom, metadata=self.metadata))


class Door(Part):
    def __init__(self, name, width, height, depth, units="m", **kwargs):
        origin = (0, 0, 0)
        lx = (1, 0, 0)
        ly = (0, 1, 0)
        lz = (0, 0, 1)
        super().__init__(name, origin=origin, lx=lx, ly=ly, lz=lz, units=units, **kwargs)
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
            scale_factor = self._unit_conversion(self._units, value)
            self._origin = tuple([x * scale_factor for x in self._origin])
            self._width *= scale_factor
            self._height *= scale_factor
            self._depth *= scale_factor
            self._shapes = []
            self.build_geom()
            self._units = value

    def build_geom(self):
        origin = self._origin - self._lz * self.depth

        points = [(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)]
        poly = CurvePoly(points2d=points, origin=origin, normal=self._lz, xdir=self._lx, parent=self)
        geom = poly.make_extruded_solid(self.depth)
        self.add_shape(Shape(self.name, geom, metadata=self.metadata))
