from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.api.curves import CurveOpen3d, CurvePoly2d
from ada.api.primitives.base import Shape
from ada.api.transforms import Placement
from ada.base.units import Units
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation
from ada.geom.direction import Direction

if TYPE_CHECKING:
    from ada.geom.solids import FixedReferenceSweptAreaSolid


class PrimSweep(Shape):
    def __init__(
        self,
        name,
        sweep_curve: Iterable[Iterable[float]] | CurveOpen3d,
        profile_curve_outer: Iterable[Iterable[float]] | CurvePoly2d,
        profile_xdir=None,
        profile_normal=None,
        profile_ydir=None,
        origin=None,
        derived_reference=False,
        tol=1e-3,
        radiis: dict[int, float] = None,
        **kwargs,
    ):
        if not isinstance(sweep_curve, CurveOpen3d):
            sweep_curve = CurveOpen3d(sweep_curve, radiis=radiis, tol=tol)

        start_norm = sweep_curve.start_vector.get_normalized() if profile_normal is None else Direction(*profile_normal)

        if not isinstance(profile_curve_outer, CurvePoly2d):
            if profile_ydir is not None:

                profile_xdir = Direction(*np.cross(profile_ydir, start_norm))
            elif profile_xdir is not None:
                profile_xdir = Direction(*profile_xdir)
            else:
                if start_norm.is_equal(Direction(1, 0, 0)):
                    profile_xdir = Direction(0, 1, 0)
                else:
                    profile_xdir = Direction(1, 0, 0)
            profile_curve_outer = CurvePoly2d(
                profile_curve_outer, origin=sweep_curve.segments[0].p1, normal=start_norm, xdir=profile_xdir, tol=tol
            )
        else:
            profile_curve_outer = profile_curve_outer

        sweep_curve.parent = self
        profile_curve_outer.parent = self

        self._sweep_curve = sweep_curve
        self._profile_curve_outer = profile_curve_outer
        self.derived_reference = derived_reference

        super(PrimSweep, self).__init__(name, placement=Placement(origin=origin), **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            raise NotImplementedError()

    @property
    def sweep_curve(self) -> CurveOpen3d:
        return self._sweep_curve

    @property
    def profile_curve_outer(self) -> CurvePoly2d:
        return self._profile_curve_outer

    def solid_geom_2d_profile(self):
        from ada.geom.solids import FixedReferenceSweptAreaSolid
        from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

        outer_curve = self.profile_curve_outer.curve_geom(use_3d_segments=False)

        profile = ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

        place_ident = Placement()
        other_place = Placement(
            xdir=self.profile_curve_outer.xdir,
            ydir=self.profile_curve_outer.ydir,
            zdir=self.profile_curve_outer.normal,
        )
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]

        curve_pts = [p for p in self.sweep_curve.points3d]
        update_points = other_place.transform_array_from_other_place(curve_pts, place_ident)
        transformed_sweep_curve_pts = update_points

        transformed_sweep_curve = CurveOpen3d(
            transformed_sweep_curve_pts, radiis=self.sweep_curve.radiis, tol=self.sweep_curve._tol
        )
        other_place.origin = self.placement.origin
        solid = FixedReferenceSweptAreaSolid(
            profile, other_place.to_axis2placement3d(), transformed_sweep_curve.curve_geom()
        )
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def solid_geom(self) -> Geometry[FixedReferenceSweptAreaSolid]:
        from ada.geom.solids import FixedReferenceSweptAreaSolid
        from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

        outer_curve = self.profile_curve_outer.curve_geom(use_3d_segments=True)

        profile = ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

        a2place3d = self.placement.to_axis2placement3d()
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]

        transformed_sweep_curve = self.sweep_curve

        solid = FixedReferenceSweptAreaSolid(profile, a2place3d, transformed_sweep_curve.curve_geom())
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}")'
