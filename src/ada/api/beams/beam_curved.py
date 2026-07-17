from __future__ import annotations

from typing import TYPE_CHECKING

from ada.geom import Geometry

from .base_bm import Beam

if TYPE_CHECKING:
    from ada import Section
    from ada.geom.curves import CURVE_GEOM_TYPES


class BeamCurved(Beam):
    """A beam whose axis is an arbitrary 3D curve, carried natively.

    Where :class:`BeamRevolve` models a circular arc (revolve of the section) and a
    plain :class:`Beam` a straight chord, ``BeamCurved`` holds the exact ngeom curve
    its axis follows — e.g. the ``BSplineCurveWithKnots`` a Genie stiffener's arc was
    authored as in the ACIS body. The curve is the sweep *path* (the section is the
    profile swept along it), so no read-side approximation is needed: the geometry
    lives in its native container rather than being collapsed to the guide chord.
    """

    def __init__(self, name: str, n1, n2, curve3d: CURVE_GEOM_TYPES, sec: str | Section, up=None, **kwargs):
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, up=up, **kwargs)
        self._curve3d = curve3d

    @property
    def curve3d(self) -> CURVE_GEOM_TYPES:
        return self._curve3d

    def solid_geom(self) -> Geometry:
        """Sweep the section profile along the axis curve (a fixed-reference sweep).

        The profile is placed at the first node, its plane perpendicular to the
        chord (a stable, twist-free reference); the ``directrix`` is the exact 3D
        curve, so the swept solid follows the real arc, not the chord.
        """
        import numpy as np

        from ada import Direction, Point
        from ada.api.beams import geom_beams as geo_conv
        from ada.geom.booleans import BooleanOperation
        from ada.geom.placement import Axis2Placement3D
        from ada.geom.solids import FixedReferenceSweptAreaSolid

        profile = geo_conv.section_to_arbitrary_profile_def_with_voids(self.section)

        p1 = np.asarray(self.n1.p, dtype=float)
        p2 = np.asarray(self.n2.p, dtype=float)
        chord = p2 - p1
        n = float(np.linalg.norm(chord))
        tangent = chord / n if n > 1e-9 else np.array([1.0, 0.0, 0.0])
        up = np.asarray(self.up, dtype=float)
        ref = np.cross(up, tangent)
        if float(np.linalg.norm(ref)) < 1e-9:
            ref = np.array([1.0, 0.0, 0.0])
        ref = ref / np.linalg.norm(ref)

        position = Axis2Placement3D(Point(*p1), Direction(*tangent), Direction(*ref))
        solid = FixedReferenceSweptAreaSolid(profile, position, self._curve3d, fixed_reference=Direction(*up))
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)
