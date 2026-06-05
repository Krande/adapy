from __future__ import annotations

from typing import TYPE_CHECKING

from ada.api.beams import geom_beams as geo_conv
from ada.geom import Geometry
from ada.geom.placement import Axis1Placement, Axis2Placement3D
from ada.geom.solids import RevolvedAreaSolid

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
        """Revolve the section profile around the curve's rotation axis.

        The profile is placed perpendicular to the arc at ``p1`` and revolved.
        The placement frame is X = radial (p1 -> away from axis), Y = rotation
        axis (the section "up"), Z = arc tangent (the profile normal).

        The revolution ``axis`` is in global coordinates — the convention both CAD
        backends build from. The IFC writer converts it to the Position-local frame
        that ``IfcRevolvedAreaSolid.Axis`` requires.
        """
        import numpy as np

        from ada import Direction, Point
        from ada.geom.booleans import BooleanOperation
        from ada.geom.solids import RevolvedAreaSolid

        p1 = np.asarray(self.curve.p1, dtype=float)
        rot_axis = np.asarray(self.curve.rot_axis, dtype=float)
        rot_axis = rot_axis / np.linalg.norm(rot_axis)
        rot_origin = np.asarray(self.curve.rot_origin, dtype=float)

        # Radial direction at p1 = component of (p1 - rot_origin) perpendicular to
        # the rotation axis; the arc tangent (profile normal) is axis x radial.
        radial = p1 - rot_origin
        radial = radial - np.dot(radial, rot_axis) * rot_axis
        radial = radial / np.linalg.norm(radial)
        tangent = np.cross(rot_axis, radial)
        tangent = tangent / np.linalg.norm(tangent)

        profile = geo_conv.section_to_arbitrary_profile_def_with_voids(self.section)
        position = Axis2Placement3D(Point(*p1), Direction(*tangent), Direction(*radial))
        axis = Axis1Placement(location=Point(*rot_origin), axis=Direction(*rot_axis))

        solid = RevolvedAreaSolid(profile, position, axis, self.curve.angle)
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)
