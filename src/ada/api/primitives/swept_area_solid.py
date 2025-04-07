from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.api.curves import CurveOpen3d, CurvePoly2d
from ada.api.primitives.base import Shape
from ada.api.transforms import Direction, Placement
from ada.base.units import Units
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation

if TYPE_CHECKING:
    from ada.geom.solids import FixedReferenceSweptAreaSolid


class PrimSweep(Shape):
    def __init__(
        self,
        name,
        sweep_curve: Iterable[Iterable[float]] | CurveOpen3d,
        profile_curve_outer: Iterable[Iterable[float]] | CurvePoly2d,
        profile_xdir=None,
        origin=None,
        derived_reference=False,
        tol=1e-3,
        **kwargs,
    ):
        if not isinstance(sweep_curve, CurveOpen3d):
            sweep_curve = CurveOpen3d(sweep_curve, tol=tol)

        # In the IFC schema the start vector of the sweep curve is always the z-axis.
        # So we apply the necessary transform to the placement object
        target_zdir = Direction(0, 0, 1)
        base_xdir = Direction(1, 0, 0)
        origin = sweep_curve.points3d[0] if origin is None else origin

        start_norm = sweep_curve.start_vector.get_normalized()
        if np.allclose(base_xdir, start_norm):
            base_xdir = Direction(0.7, -1, 0)

        orth_vec = Direction(np.cross(start_norm, np.cross(start_norm, base_xdir))).get_normalized()
        place = Placement(origin=origin, zdir=start_norm, xdir=orth_vec)

        if not start_norm.is_equal(target_zdir):
            new_place = Placement(origin=origin)
            radiis = sweep_curve.radiis
            raw_points = [x.tolist() for x in sweep_curve.points3d]
            raw_points_np = np.asarray(raw_points)
            sweep_curve_new = place.transform_array_from_other_place(raw_points_np, new_place)
            sweep_curve = CurveOpen3d(sweep_curve_new, radiis=radiis, tol=tol)

        if not isinstance(profile_curve_outer, CurvePoly2d):
            profile_xdir = Direction(*profile_xdir) if profile_xdir is not None else Direction(1, 0, 0)
            profile_curve_outer = CurvePoly2d(
                profile_curve_outer, origin=origin, normal=target_zdir, xdir=profile_xdir, tol=tol
            )
        else:
            profile_curve_outer = profile_curve_outer

        sweep_curve.parent = self
        profile_curve_outer.parent = self

        self._sweep_curve = sweep_curve
        self._profile_curve_outer = profile_curve_outer
        self.derived_reference = derived_reference

        super(PrimSweep, self).__init__(name, placement=place, **kwargs)

    def _realign_sweep_curve(self):
        start_norm = self.sweep_curve.start_vector.get_normalized()
        if not start_norm.is_equal(Direction(0, 0, 1)):
            new_place = self.placement.with_zdir(Direction(0, 0, 1))
            raw_points = [x.tolist() for x in self.sweep_curve.points3d]
            raw_points_np = np.asarray(raw_points)
            sweep_curve_new = new_place.transform_array_from_other_place(raw_points_np, self.placement)

            self._sweep_curve = CurveOpen3d(sweep_curve_new, tol=1e-3)
            self.placement = new_place

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

    def solid_geom(self) -> Geometry[FixedReferenceSweptAreaSolid]:
        from ada.geom.solids import FixedReferenceSweptAreaSolid
        from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

        outer_curve = self.profile_curve_outer.curve_geom()

        profile = ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

        place = self.placement.to_axis2placement3d()

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]

        solid = FixedReferenceSweptAreaSolid(profile, place, self.sweep_curve.curve_geom())
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def __repr__(self):
        return f"PrimSweep({self.name})"
