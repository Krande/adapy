from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Tuple, Union

import numpy as np

from ada.base.physical_objects import BackendGeom
from ada.core.utils import Counter, roundoff
from ada.materials import Material
from ada.materials.utils import get_material

from .bounding_box import BoundingBox
from .curves import CurvePoly
from .transforms import Placement

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from ada.ifc.concepts import IfcRef


class Shape(BackendGeom):
    def __init__(
        self,
        name,
        geom,
        colour=None,
        opacity=1.0,
        mass: float = None,
        cog: Tuple[float, float, float] = None,
        metadata=None,
        units="m",
        ifc_elem=None,
        guid=None,
        material: Union[Material, str] = None,
        placement=Placement(),
        ifc_ref: IfcRef = None,
    ):

        super().__init__(
            name,
            guid=guid,
            metadata=metadata,
            units=units,
            ifc_elem=ifc_elem,
            placement=placement,
            ifc_ref=ifc_ref,
            colour=colour,
            opacity=opacity,
        )
        if type(geom) in (str, pathlib.WindowsPath, pathlib.PurePath, pathlib.Path):
            from OCC.Extend.DataExchange import read_step_file

            geom = read_step_file(str(geom))

        self._geom = geom
        self._mass = mass
        self._cog = cog
        if isinstance(material, Material):
            self._material = material
        else:
            self._material = get_material(material)

        self._bbox = None

    def generate_ifc_solid_geom(self, f):
        raise NotImplementedError()

    def _generate_ifc_elem(self):
        from ada.ifc.write.write_shapes import write_ifc_shape

        return write_ifc_shape(self)

    @property
    def type(self):
        return type(self.geom)

    @property
    def mass(self) -> float:
        return self._mass

    @mass.setter
    def mass(self, value: float):
        self._mass = value

    @property
    def cog(self) -> Tuple[float, float, float]:
        return self._cog

    @cog.setter
    def cog(self, value: Tuple[float, float, float]):
        self._cog = value

    @property
    def bbox(self) -> BoundingBox:
        if self._bbox is None and self.geom is not None:
            self._bbox = BoundingBox(self)

        return self._bbox

    @property
    def point_on(self):
        return self.bbox[3:6]

    @property
    def geom(self) -> TopoDS_Shape:
        from ada.occ.utils import apply_penetrations

        from .exceptions import NoGeomPassedToShapeError

        if self._geom is None:
            from ada.ifc.read.read_shapes import get_ifc_geometry

            if self._ifc_elem is not None:
                ifc_elem = self._ifc_elem
            elif "ifc_file" in self.metadata.keys():
                a = self.parent.get_assembly()
                ifc_file = self.metadata["ifc_file"]
                ifc_f = a.get_ifc_source_by_name(ifc_file)
                ifc_elem = ifc_f.by_guid(self.metadata["ifc_guid"])
            else:
                raise NoGeomPassedToShapeError(f'No geometry information attached to shape "{self}"')

            geom, color, alpha = get_ifc_geometry(ifc_elem, self.ifc_settings)
            self._geom = geom
            self.colour = color
            self.opacity = alpha

        geom = apply_penetrations(self._geom, self.penetrations)

        return geom

    @property
    def solid(self):
        return self.geom

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.core.utils import unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)
            if self._geom is not None:
                from ada.occ.utils import transform_shape

                self._geom = transform_shape(self.geom, scale_factor)

            if self.metadata.get("ifc_source") is True:
                raise NotImplementedError()

            self._units = value

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value):
        self._material = value

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}")'


class PrimSphere(Shape):
    def __init__(self, name, cog, radius, **kwargs):
        self.radius = radius
        super(PrimSphere, self).__init__(name=name, geom=None, cog=cog, **kwargs)

    @property
    def geom(self):
        from ada.occ.utils import apply_penetrations

        if self._geom is None:
            from ada.occ.utils import make_sphere

            self._geom = make_sphere(self.cog, self.radius)

        geom = apply_penetrations(self._geom, self.penetrations)
        return geom

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.core.utils import unit_length_conversion
            from ada.occ.utils import make_sphere

            scale_factor = unit_length_conversion(self._units, value)
            self.cog = tuple([x * scale_factor for x in self.cog])
            self.radius = self.radius * scale_factor
            self._geom = make_sphere(self.cog, self.radius)
            self._units = value

    def __repr__(self):
        return f"PrimSphere({self.name})"


class PrimBox(Shape):
    """Primitive Box. Length, width & height are local x, y and z respectively"""

    def __init__(self, name, p1, p2, **kwargs):
        from ada.occ.utils import make_box_by_points

        self.p1 = p1
        self.p2 = p2
        super(PrimBox, self).__init__(name=name, geom=make_box_by_points(p1, p2), **kwargs)
        self._bbox = BoundingBox(self)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.core.utils import unit_length_conversion
            from ada.occ.utils import make_box_by_points

            scale_factor = unit_length_conversion(self._units, value)
            self.p1 = tuple([x * scale_factor for x in self.p1])
            self.p2 = tuple([x * scale_factor for x in self.p2])
            self._geom = make_box_by_points(self.p1, self.p2)
            self._units = value

    def __repr__(self):
        return f"PrimBox({self.name})"


class PrimCyl(Shape):
    def __init__(self, name, p1, p2, r, **kwargs):
        from ada.occ.utils import make_cylinder_from_points

        self.p1 = np.array(p1)
        self.p2 = np.array(p2)
        self.r = r
        super(PrimCyl, self).__init__(name, make_cylinder_from_points(p1, p2, r), **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        from ada.occ.utils import make_cylinder_from_points

        if value != self._units:
            from ada.core.utils import unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)
            self.p1 = [x * scale_factor for x in self.p1]
            self.p2 = [x * scale_factor for x in self.p2]
            self.r = self.r * scale_factor
            self._geom = make_cylinder_from_points(self.p1, self.p2, self.r)

    def __repr__(self):
        return f"PrimCyl({self.name})"


class PrimExtrude(Shape):
    def __init__(self, name, points2d, h, normal, origin, xdir, tol=1e-3, **kwargs):
        self._name = name
        poly = CurvePoly(
            points2d=points2d,
            normal=normal,
            origin=origin,
            xdir=xdir,
            tol=tol,
            parent=self,
        )
        self._poly = poly
        self._extrude_depth = h

        super(PrimExtrude, self).__init__(name, self._poly.make_extruded_solid(self._extrude_depth), **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.config import Settings
            from ada.core.utils import unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)
            tol = Settings.mmtol if value == "mm" else Settings.mtol
            self.poly.scale(scale_factor, tol)
            self._extrude_depth = self._extrude_depth * scale_factor
            self._units = value

    @property
    def poly(self) -> "CurvePoly":
        return self._poly

    @property
    def extrude_depth(self):
        return self._extrude_depth

    def __repr__(self):
        return f"PrimExtrude({self.name})"


class PrimRevolve(Shape):
    """Revolved Primitive"""

    def __init__(self, name, points2d, origin, xdir, normal, rev_angle, tol=1e-3, **kwargs):
        self._name = name
        poly = CurvePoly(
            points2d=points2d,
            normal=[roundoff(x) for x in normal],
            origin=origin,
            xdir=[roundoff(x) for x in xdir],
            tol=tol,
            parent=self,
        )
        self._poly = poly
        self._revolve_angle = rev_angle
        self._revolve_axis = [roundoff(x) for x in poly.ydir]
        self._revolve_origin = origin
        super(PrimRevolve, self).__init__(
            name,
            self._poly.make_revolve_solid(
                self._revolve_axis,
                self._revolve_angle,
                self._revolve_origin,
            ),
            **kwargs,
        )

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.config import Settings
            from ada.core.utils import unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)
            tol = Settings.mmtol if value == "mm" else Settings.mtol
            self.poly.scale(scale_factor, tol)
            self._revolve_origin = [x * scale_factor for x in self.revolve_origin]
            self._geom = self._poly.make_revolve_solid(
                self._revolve_axis,
                self._revolve_angle,
                self._revolve_origin,
            )

    @property
    def poly(self) -> CurvePoly:
        return self._poly

    @property
    def revolve_origin(self):
        return self._revolve_origin

    @property
    def revolve_axis(self):
        return self._revolve_axis

    @property
    def revolve_angle(self):
        return self._revolve_angle

    def __repr__(self):
        return f"PrimRevolve({self.name})"


class PrimSweep(Shape):
    def __init__(
        self,
        name,
        sweep_curve,
        normal,
        xdir,
        profile_curve_outer,
        profile_curve_inner=None,
        origin=None,
        tol=1e-3,
        **kwargs,
    ):
        if type(sweep_curve) is list:
            sweep_curve = CurvePoly(points3d=sweep_curve, is_closed=False)

        if type(profile_curve_outer) is list:
            origin = sweep_curve.placement.origin if origin is None else origin
            profile_curve_outer = CurvePoly(profile_curve_outer, origin=origin, normal=normal, xdir=xdir)

        sweep_curve.parent = self
        profile_curve_outer.parent = self

        self._sweep_curve = sweep_curve
        self._profile_curve_outer = profile_curve_outer
        self._profile_curve_inner = profile_curve_inner

        super(PrimSweep, self).__init__(name, self._sweep_geom(), **kwargs)

    def _sweep_geom(self):
        from ada.occ.utils import sweep_geom

        return sweep_geom(self.sweep_curve.wire, self.profile_curve_outer.wire)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            raise NotImplementedError()

    @property
    def sweep_curve(self):
        return self._sweep_curve

    @property
    def profile_curve_outer(self):
        return self._profile_curve_outer

    @property
    def profile_curve_inner(self):
        return self._profile_curve_inner

    def __repr__(self):
        return f"PrimSweep({self.name})"


class Penetration(BackendGeom):
    _name_gen = Counter(1, "Pen")
    """A penetration object. Wraps around a primitive"""
    # TODO: Maybe this class should be evaluated for removal?
    def __init__(self, primitive, metadata=None, parent=None, units="m", guid=None):
        if issubclass(type(primitive), Shape) is False:
            raise ValueError(f'Unsupported primitive type "{type(primitive)}"')

        super(Penetration, self).__init__(primitive.name, guid=guid, metadata=metadata, units=units)
        self._primitive = primitive
        self._parent = parent
        self._ifc_opening = None

    @property
    def primitive(self):
        return self._primitive

    @property
    def geom(self):
        return self.primitive.geom

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            self.primitive.units = value
            self._units = value

    @property
    def ifc_opening(self):
        if self._ifc_opening is None:
            from ada.ifc.write.write_openings import generate_ifc_opening

            self._ifc_opening = generate_ifc_opening(self)
        return self._ifc_opening

    def __repr__(self):
        return f"Pen(type={self.primitive})"
