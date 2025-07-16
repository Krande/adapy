from __future__ import annotations

from typing import Iterable

import numpy as np
from OCC.Core.TopoDS import TopoDS_Shape

from ada.api.curves import CurvePoly2d
from ada.api.primitives.base import Shape
from ada.base.units import Units
from ada.config import Config
from ada.core.vector_utils import unit_vector, vector_length
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation


class PrimExtrude(Shape):
    def __init__(self, name, curve2d: list[tuple], h, normal=None, origin=None, xdir=None, tol=1e-3, **kwargs):
        self._name = name

        poly = CurvePoly2d(
            points2d=curve2d,
            normal=normal,
            origin=origin,
            xdir=xdir,
            tol=tol,
            parent=self,
        )

        self._poly = poly
        self._extrude_depth = h
        super(PrimExtrude, self).__init__(name=name, **kwargs)

    @staticmethod
    def from_2points_and_curve(name: str, p1: Iterable, p2: Iterable, profile: list[tuple], xdir: tuple) -> PrimExtrude:
        p1 = np.array(p1)
        p2 = np.array(p2)
        normal = unit_vector(p2 - p1)
        length = vector_length(p2 - p1)
        return PrimExtrude(name=name, curve2d=profile, h=length, normal=normal, origin=p1, xdir=xdir)

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
            self._extrude_depth = self._extrude_depth * scale_factor
            self._units = value

    @property
    def poly(self) -> CurvePoly2d:
        return self._poly

    @property
    def extrude_depth(self):
        return self._extrude_depth

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.curves import IndexedPolyCurve
        from ada.geom.direction import Direction
        from ada.geom.placement import Axis2Placement3D
        from ada.geom.solids import ExtrudedAreaSolid
        from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

        outer_curve: IndexedPolyCurve = self.poly.curve_geom()
        profile = ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

        place = Axis2Placement3D(self.poly.origin, axis=self.poly.normal, ref_direction=self.poly.xdir)

        solid = ExtrudedAreaSolid(profile, place, self.extrude_depth, Direction(0, 0, 1))
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}", curve2d={self.poly.points2d}, h={self.extrude_depth})'
