from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

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
        origin=None,
        derived_reference=False,
        tol=1e-3,
        radiis: dict[int, float] = None,
        **kwargs,
    ):
        if not isinstance(sweep_curve, CurveOpen3d):
            sweep_curve = CurveOpen3d(sweep_curve, radiis=radiis, tol=tol)

        origin = sweep_curve.points3d[0] if origin is None else origin
        start_norm = sweep_curve.start_vector.get_normalized() if profile_normal is None else Direction(*profile_normal)
        place = Placement(origin=origin)

        if not isinstance(profile_curve_outer, CurvePoly2d):
            profile_xdir = Direction(*profile_xdir) if profile_xdir is not None else Direction(1, 0, 0)
            profile_curve_outer = CurvePoly2d(
                profile_curve_outer, origin=origin, normal=start_norm, xdir=profile_xdir, tol=tol
            )
        else:
            profile_curve_outer = profile_curve_outer

        sweep_curve.parent = self
        profile_curve_outer.parent = self

        self._sweep_curve = sweep_curve
        self._profile_curve_outer = profile_curve_outer
        self.derived_reference = derived_reference

        super(PrimSweep, self).__init__(name, placement=place, **kwargs)

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
        return f'{self.__class__.__name__}("{self.name}")'
