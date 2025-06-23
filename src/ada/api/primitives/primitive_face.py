from __future__ import annotations

from typing import TYPE_CHECKING

from ada.api.curves import CurvePoly2d
from ada.api.primitives.base import Shape
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation
from ada.geom.surfaces import CurveBoundedPlane, Plane

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape


class PrimFace(Shape):
    def __init__(self, name, curve2d: list[tuple], normal=None, origin=None, xdir=None, tol=1e-3, **kwargs):
        self._name = name
        if isinstance(curve2d, CurvePoly2d):
            poly = curve2d
        else:
            poly = CurvePoly2d(
                points2d=curve2d,
                normal=normal,
                origin=origin,
                xdir=xdir,
                tol=tol,
                parent=self,
            )

        self._poly = poly
        super(PrimFace, self).__init__(name=name, **kwargs)

    @property
    def poly(self) -> CurvePoly2d:
        return self._poly

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.curves import IndexedPolyCurve
        from ada.geom.placement import Axis2Placement3D

        outer_curve: IndexedPolyCurve = self.poly.curve_geom()

        place = Axis2Placement3D(self.poly.origin, axis=self.poly.normal, ref_direction=self.poly.xdir)
        plane = Plane(place)
        surface = CurveBoundedPlane(plane, outer_curve)
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]

        return Geometry(self.guid, surface, self.color, bool_operations=booleans)

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}", curve2d={self.poly.points2d})'

    @classmethod
    def from_3d_points(cls, name: str, points3d):
        curve = CurvePoly2d.from_3d_points(points3d)
        return cls(name, curve)
