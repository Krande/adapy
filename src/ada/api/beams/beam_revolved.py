from __future__ import annotations

from .base_bm import Beam
from ada.api.beams import geom_beams as geo_conv
from ada.geom import Geometry
from ada.geom.placement import Axis1Placement
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import CurveRevolve, Section


class BeamRevolve(Beam):
    def __init__(self, name: str, curve: CurveRevolve, sec: str | Section, **kwargs):
        n1 = curve.p1
        n2 = curve.p2
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, **kwargs)
        self._curve = curve
        curve.parent = self

    @property
    def curve(self) -> CurveRevolve:
        return self._curve

    def solid_geom(self) -> Geometry:
        from ada.geom.solids import RevolvedAreaSolid

        profile = geo_conv.section_to_arbitrary_profile_def_with_voids(self.section)

        axis = Axis1Placement(self.curve.rot_origin, self.curve.rot_axis)

        solid = RevolvedAreaSolid(profile, self.placement.to_axis2placement3d(), axis, self.curve.angle)
        return Geometry(self.guid, solid, self.color)
