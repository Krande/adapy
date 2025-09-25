from __future__ import annotations

from typing import Iterable

from OCC.Core.TopoDS import TopoDS_Shape

from ada.api.curves import CurvePoly2d
from ada.api.primitives.base import Shape
from ada.core.utils import Counter
from ada.geom import Geometry
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.geom.surfaces import HalfSpaceSolid, Plane

_NAME_GEN = Counter(prefix="HalfSpaceSolid_")


class BoolHalfSpace(Shape):
    def __init__(
        self,
        origin: Point | Iterable[float],
        normal: Direction | Iterable[float],
        flip=False,
        name: str = None,
        plane_geo_width: float = 1.0,
        **kwargs,
    ):
        if name is None:
            name = next(_NAME_GEN)
        super().__init__(name, **kwargs)
        if not isinstance(normal, Direction):
            normal = Direction(*normal)
        self.flip = flip
        w = plane_geo_width
        self._poly = CurvePoly2d(
            points2d=[(0, 0), (w, 0), (w, w), (0, w)],
            normal=normal,
            origin=origin,
            parent=self,
        )

    @property
    def poly(self) -> CurvePoly2d:
        return self._poly

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry[HalfSpaceSolid]:
        from ada.geom.placement import Axis2Placement3D

        place = Axis2Placement3D(self.poly.origin, axis=self.poly.normal, ref_direction=self.poly.xdir)
        plane = Plane(place)
        half_space = HalfSpaceSolid(plane, self.flip)
        return Geometry(self.guid, half_space, self.color)

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}", curve2d={self.poly.points2d})'
