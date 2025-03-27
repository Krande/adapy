from __future__ import annotations

from typing import TYPE_CHECKING

from ada.api.beams import geom_beams as geo_conv
from ada.geom import Geometry
from ada.geom.placement import Axis1Placement, Axis2Placement3D
from ada.geom.solids import RevolvedAreaSolid

from ...config import logger
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

    @staticmethod
    def from_points_and_radius(name: str, p1, p2, radius, rot_axis, sec: str | Section, up=None, **kwargs):
        from ada import CurveRevolve

        curve = CurveRevolve(p1, p2, radius, rot_axis)
        return BeamRevolve(name, curve, sec, up, **kwargs)

    @property
    def curve(self) -> CurveRevolve:
        return self._curve

    def solid_geom(self) -> Geometry[RevolvedAreaSolid]:
        # todo: This currently does not work as intended
        logger.warning("BeamRevolve.solid_geom() is not yet implemented correctly")
        from ada import Direction, Point
        from ada.core.constants import O
        from ada.core.vector_transforms import global_2_local_nodes
        from ada.geom.solids import RevolvedAreaSolid

        normal = self.curve.rot_axis.get_normalized()
        xvec1 = self.curve.profile_normal.get_normalized()
        yvec = self.curve.profile_perpendicular.get_normalized()

        new_csys = (normal, yvec, xvec1)

        # Revolve Point
        diff = self.curve.rot_origin - self.curve.p1
        # diff_tra = sec_place.transform_array_from_other_place([diff],loc_place)[0]
        # n_tra = Direction(sec_place.transform_array_from_other_place([normal],loc_place, ignore_translation=True)[0]).get_normalized()
        diff_tra = Point(global_2_local_nodes(new_csys, O, [diff])[0])
        n_tra = Direction(global_2_local_nodes(new_csys, O, [normal])[0]).get_normalized()

        profile = geo_conv.section_to_arbitrary_profile_def_with_voids(self.section)

        position = Axis2Placement3D(self.curve.p1, xvec1, normal)
        axis = Axis1Placement(location=diff_tra, axis=n_tra)

        # position = Placement(origin=Point(0, -0.1, 0.0)).to_axis2placement3d()
        # axis = Axis1Placement(location=Point(-1.3, 0.1, 0.0), axis=Direction(0, -1, 0))

        solid = RevolvedAreaSolid(profile, position, axis, self.curve.angle)
        return Geometry(self.guid, solid, self.color)
