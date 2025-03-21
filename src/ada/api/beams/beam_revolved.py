from __future__ import annotations

from typing import TYPE_CHECKING


from ada.api.beams import geom_beams as geo_conv
from ada.geom import Geometry
from ada.geom.placement import Axis1Placement

from .base_bm import Beam

if TYPE_CHECKING:
    from ada import CurveRevolve, Section


class BeamRevolve(Beam):
    def __init__(self, name: str, curve: CurveRevolve, sec: str | Section, up=None, **kwargs):
        n1 = curve.p1
        n2 = curve.p2
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, up=up, **kwargs)
        self._curve = curve
        curve.parent = self

    @property
    def curve(self) -> CurveRevolve:
        return self._curve

    def solid_geom(self) -> Geometry:
        from ada import Direction, Placement, Point
        from ada.geom.solids import RevolvedAreaSolid

        profile = geo_conv.section_to_arbitrary_profile_def_with_voids(self.section)
        # axis = Axis1Placement(self.curve.rot_origin, self.curve.rot_axis)
        # axis_pl = self.placement
        axis_pl = Placement(origin=Point(0, -0.1, 0.0))
        axis = Axis1Placement(location=Point(-1.3, 0.1, 0.0), axis=Direction(0, -1, 0))

        solid = RevolvedAreaSolid(profile, axis_pl.to_axis2placement3d(), axis, self.curve.angle)
        return Geometry(self.guid, solid, self.color)
