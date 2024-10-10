from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.base.ifc_types import ShapeTypes
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.core.vector_utils import unit_vector, vector_length
from ada.geom.booleans import BooleanOperation
from ada.materials import Material
from ada.materials.utils import get_material

from ..config import Config
from ..geom import Geometry
from ..geom.placement import Axis2Placement3D, Direction
from ..geom.points import Point
from .bounding_box import BoundingBox
from .curves import CurveOpen3d, CurvePoly2d
from .transforms import Placement

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from ada.cadit.ifc.store import IfcStore


class Shape(BackendGeom):
    IFC_CLASSES = ShapeTypes

    def __init__(
        self,
        name,
        geom: Geometry | list[Geometry] | None = None,
        color=None,
        opacity=1.0,
        mass: float = None,
        cog: Iterable = None,
        material: Material | str = None,
        units=Units.M,
        metadata=None,
        guid=None,
        placement=Placement(),
        ifc_store: IfcStore = None,
        ifc_class: ShapeTypes = ShapeTypes.IfcBuildingElementProxy,
        parent=None,
    ):
        super().__init__(
            name,
            guid=guid,
            metadata=metadata,
            units=units,
            placement=placement,
            ifc_store=ifc_store,
            color=color,
            opacity=opacity,
            parent=parent,
        )
        self._geom = geom
        self._mass = mass
        if cog is not None and not isinstance(cog, Point):
            cog = Point(*cog)

        self._cog = cog
        if isinstance(material, Material):
            self._material = material
        else:
            self._material = get_material(material)

        self._material.refs.append(self)
        self._bbox = None
        self._ifc_class = ifc_class

    @property
    def mass(self) -> float:
        return self._mass

    @mass.setter
    def mass(self, value: float):
        self._mass = value

    @property
    def cog(self) -> Point:
        return self._cog

    @cog.setter
    def cog(self, value: Iterable):
        if not isinstance(value, Point):
            value = Point(*value)
        self._cog = value

    @property
    def geom(self) -> Geometry:
        return self._geom

    def bbox(self) -> BoundingBox:
        if self._bbox is None and self.solid_occ() is not None:
            self._bbox = BoundingBox(self)

        return self._bbox

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        if self.geom is None:
            raise NotImplementedError(f"solid_geom() not implemented for {self.__class__.__name__}")

        import ada.geom.solids as geo_so
        import ada.geom.surfaces as geo_su

        if isinstance(self.geom.geometry, (geo_su.AdvancedFace, geo_su.ClosedShell, geo_so.Box)):

            self.geom.bool_operations = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
            return self.geom
        else:
            raise NotImplementedError(f"solid_geom() not implemented for {self.geom=}")

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)
            if self._geom is not None:
                from ada.occ.utils import transform_shape

                self._geom = transform_shape(self.solid_occ(), scale_factor)

            if self.metadata.get("ifc_source") is True:
                raise NotImplementedError()

            self._units = value

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value):
        self._material = value

    @property
    def ifc_class(self) -> ShapeTypes:
        return self._ifc_class

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}")'


class PrimSphere(Shape):
    def __init__(self, name, cog, radius, **kwargs):
        self.radius = radius
        super(PrimSphere, self).__init__(name=name, cog=cog, **kwargs)

    def geom_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.points import Point
        from ada.geom.solids import Sphere

        sphere = Sphere(Point(*self.cog), self.radius)
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, sphere, self.color, bool_operations=booleans)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)

            self.cog = [x * scale_factor for x in self.cog]
            self.radius = self.radius * scale_factor
            self._geom = self.geom_occ()
            self._units = value

    def __repr__(self):
        return f'PrimSphere("{self.name}", {self.cog.tolist()}, {self.radius})'


class PrimBox(Shape):
    """Primitive Box. Length, width & height are local x, y and z respectively"""

    def __init__(self, name, p1, p2, **kwargs):
        self.p1 = p1 if isinstance(p1, Point) else Point(*p1)
        self.p2 = p2 if isinstance(p2, Point) else Point(*p2)
        super(PrimBox, self).__init__(name=name, **kwargs)
        self._bbox = BoundingBox(self)

    def solid_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.points import Point
        from ada.geom.solids import Box

        box = Box.from_2points(Point(*self.p1), Point(*self.p2))
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, box, self.color, bool_operations=booleans)

    @staticmethod
    def from_p_and_dims(name, p, length, width, height, **kwargs):
        p1 = p
        p2 = [p[0] + length, p[1] + width, p[2] + height]
        return PrimBox(name, p1, p2, **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)
            self.p1 = Point(*[x * scale_factor for x in self.p1])
            self.p2 = Point(*[x * scale_factor for x in self.p2])
            self._units = value

    def __repr__(self):
        p1s = self.p1.tolist()
        p2s = self.p2.tolist()
        return f'PrimBox("{self.name}", {p1s}, {p2s})'


class PrimCone(Shape):
    def __init__(self, name, p1, p2, r, **kwargs):
        self.p1 = np.array(p1)
        self.p2 = np.array(p2)
        self.r = r
        super(PrimCone, self).__init__(name, geom=None, **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)

        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)
            self.p1 = [x * scale_factor for x in self.p1]
            self.p2 = [x * scale_factor for x in self.p2]
            self.r = self.r * scale_factor
            self._units = value

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.points import Point
        from ada.geom.solids import Cone

        cone = Cone.from_2points(Point(*self.p1), Point(*self.p2), self.r)
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, cone, self.color, bool_operations=booleans)

    def __repr__(self):
        p1s = self.p1.tolist()
        p2s = self.p2.tolist()
        return f'PrimCone("{self.name}", {p1s}, {p2s}, {self.r})'


class PrimCyl(Shape):
    def __init__(self, name, p1, p2, r, **kwargs):
        self.p1 = np.array(p1)
        self.p2 = np.array(p2)
        self.r = r
        super(PrimCyl, self).__init__(name, geom=None, **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)

        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)
            self.p1 = [x * scale_factor for x in self.p1]
            self.p2 = [x * scale_factor for x in self.p2]
            self.r = self.r * scale_factor
            self._geom = self.solid_occ()

    def solid_occ(self) -> TopoDS_Shape:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        from ada.geom.points import Point
        from ada.geom.solids import Cylinder

        cyl = Cylinder.from_2points(Point(*self.p1), Point(*self.p2), self.r)
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, cyl, self.color, bool_operations=booleans)

    def __repr__(self):
        p1s = self.p1.tolist()
        p2s = self.p2.tolist()
        return f'PrimCyl("{self.name}", {p1s}, {p2s}, {self.r})'


class PrimExtrude(Shape):
    def __init__(self, name, curve: list[tuple], h, normal=None, origin=None, xdir=None, tol=1e-3, **kwargs):
        self._name = name

        poly = CurvePoly2d(
            points2d=curve,
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
        return PrimExtrude(name=name, curve=profile, h=length, normal=normal, origin=p1, xdir=xdir)

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
        from ada.geom.placement import Axis2Placement3D, Direction
        from ada.geom.solids import ExtrudedAreaSolid
        from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

        outer_curve = self.poly.curve_geom()
        profile = ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

        place = Axis2Placement3D(self.poly.origin, axis=self.poly.normal, ref_direction=self.poly.xdir)

        solid = ExtrudedAreaSolid(profile, place, self.extrude_depth, Direction(0, 0, 1))
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def __repr__(self):
        return f'PrimExtrude("{self.name}")'


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


class PrimSweep(Shape):
    def __init__(
        self,
        name,
        sweep_curve,
        profile_curve_outer,
        profile_zdir=None,
        profile_xdir=None,
        origin=None,
        tol=1e-3,
        **kwargs,
    ):
        sweep_curve = CurveOpen3d(sweep_curve, tol=tol)

        if not isinstance(profile_curve_outer, CurvePoly2d):
            origin = sweep_curve.orientation.origin if origin is None else origin
            svec = sweep_curve.start_vector if profile_zdir is None else profile_zdir
            xdir = sweep_curve.orientation.xdir if profile_xdir is None else profile_xdir
            profile_curve_outer = CurvePoly2d(profile_curve_outer, origin=origin, normal=svec, xdir=xdir, tol=tol)
        else:
            profile_curve_outer = profile_curve_outer

        sweep_curve.parent = self
        profile_curve_outer.parent = self

        self._sweep_curve = sweep_curve
        self._profile_curve_outer = profile_curve_outer

        super(PrimSweep, self).__init__(name, **kwargs)

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

    def solid_geom(self) -> Geometry:
        from ada.geom.solids import FixedReferenceSweptAreaSolid
        from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

        outer_curve = self.profile_curve_outer.curve_geom()

        profile = ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

        place = Axis2Placement3D()

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]

        solid = FixedReferenceSweptAreaSolid(profile, place, self.sweep_curve.curve_geom())
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def __repr__(self):
        return f"PrimSweep({self.name})"
