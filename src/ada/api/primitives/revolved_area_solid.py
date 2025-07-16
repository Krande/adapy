from __future__ import annotations

from OCC.Core.TopoDS import TopoDS_Shape

from ada.api.curves import CurvePoly2d
from ada.api.primitives.base import Shape
from ada.base.units import Units
from ada.config import Config
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation
from ada.geom.direction import Direction
from ada.geom.points import Point


class PrimRevolve(Shape):
    """Revolved Primitive"""

    def __init__(self, name, points, rev_angle, origin=None, xdir=None, normal=None, tol=1e-3, **kwargs):
        self._name = name
        if not isinstance(normal, Direction):
            normal = Direction(*normal)
        if not isinstance(xdir, Direction):
            xdir = Direction(*xdir)

        if isinstance(points, CurvePoly2d):
            self._poly = points
        else:
            self._poly = CurvePoly2d(
                points2d=points,
                normal=normal,
                origin=origin,
                xdir=xdir,
                tol=tol,
                parent=self,
            )
        self._revolve_angle = rev_angle
        super(PrimRevolve, self).__init__(name, **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)
            tol = Config().general_mmtol if value == "mm" else Config().general_mtol
            self.poly.scale(scale_factor, tol)

    @property
    def poly(self) -> CurvePoly2d:
        return self._poly

    @property
    def revolve_origin(self) -> Point:
        return self.poly.origin

    @property
    def revolve_axis(self) -> Direction:
        return self.poly.ydir

    @property
    def revolve_angle(self) -> float:
        """Revolve angle in degrees"""
        return self._revolve_angle

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.placement import Axis1Placement, Axis2Placement3D
        from ada.geom.solids import RevolvedAreaSolid
        from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

        outer_curve = self.poly.curve_geom()
        profile = ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

        place = Axis2Placement3D(self.poly.origin, axis=self.poly.normal, ref_direction=self.poly.xdir)
        rev_axis = Axis1Placement(self.revolve_origin, self.revolve_axis)
        solid = RevolvedAreaSolid(profile, place, rev_axis, self.revolve_angle)

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def __repr__(self):
        return f"PrimRevolve({self.name}, )"
