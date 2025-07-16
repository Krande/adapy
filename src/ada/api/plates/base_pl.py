from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Literal, TypeAlias, Union

from ada.api.bounding_box import BoundingBox
from ada.api.curves import CurvePoly2d
from ada.api.nodes import Node
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.config import Config
from ada.geom import Geometry
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.geom.solids import ExtrudedAreaSolid
from ada.materials import Material
from ada.materials.metals import CarbonSteel

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape, TopoDS_Solid

    from ada import Placement

_NTYPE: TypeAlias = Union[int, float]
# Define coordinate types
Coordinate: TypeAlias = tuple[_NTYPE, _NTYPE]
CoordinateSequence: TypeAlias = (
    list[Coordinate]
    | list[list[Coordinate]]
    | list[tuple[Coordinate, ...]]
    | tuple[Coordinate, ...]
    | tuple[list[Coordinate], ...]
)


class Plate(BackendGeom):
    """
    A plate object. The plate element covers all plate elements.

    Contains a dictionary with each point of the plate
    described by an id (index) and a Node object.

    :param name: Name of plate
    :param points: List of 2D point coordinates (or a PolyCurve) that make up the plate. Each point is (x, y, optional [radius])
    :param t: Thickness of plate
    :param mat: Material. Can be either Material object or built-in materials ('S420' or 'S355')
    :param origin: Explicitly define origin of plate. If not set
    :param xdir: Explicitly define x direction of plate. If not set
    :param normal: Explicitly define normal direction of plate. If not set
    """

    def __init__(
        self,
        name: str,
        points: CurvePoly2d | CoordinateSequence,
        t: float,
        mat: str | Material = "S420",
        origin: Iterable | Point = None,
        xdir: Iterable | Direction = None,
        normal: Iterable | Direction = None,
        orientation: Placement = None,
        pl_id=None,
        tol=None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self._pl_id = pl_id
        self._material = mat if isinstance(mat, Material) else Material(mat, mat_model=CarbonSteel(mat), parent=self)
        self._material.refs.append(self)
        self._t = t
        self._hash = None

        if tol is None:
            tol = Units.get_general_point_tol(self.units)

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
                orientation=orientation,
            )

        self._bbox = None

    @staticmethod
    def from_3d_points(
        name, points, t, mat="S420", xdir=None, color=None, metadata=None, flip_normal=False, **kwargs
    ) -> Plate:
        poly = CurvePoly2d.from_3d_points(points, xdir=xdir, flip_n=flip_normal, **kwargs)
        return Plate(name, poly, t, mat=mat, color=color, metadata=metadata, **kwargs)

    @staticmethod
    def from_extruded_area_solid(name, solid: ExtrudedAreaSolid): ...

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(self.guid)
        return self._hash

    def __eq__(self, other: Plate) -> bool:
        if self is other:
            return True
        if not isinstance(other, Plate):
            return NotImplemented
        return self._guid == other._guid

    def bbox(self) -> BoundingBox:
        """Bounding Box of plate"""
        if self._bbox is None:
            self._bbox = BoundingBox(self)

        return self._bbox

    def line_occ(self):
        return self._poly.occ_wire()

    def shell_occ(self):
        from ada.occ.geom.cache import get_shell_occ

        return get_shell_occ(self)

    def solid_occ(self) -> TopoDS_Solid:
        from ada.occ.geom.cache import get_solid_occ

        return get_solid_occ(self)

    def shell_geom(self) -> Geometry:
        import ada.geom.surfaces as geo_su
        from ada.geom.booleans import BooleanOperation
        from ada.geom.placement import Axis2Placement3D

        outer_curve = self.poly.curve_geom()
        place = Axis2Placement3D(self.poly.orientation.origin, axis=self.poly.normal, ref_direction=self.poly.xdir)
        face = geo_su.CurveBoundedPlane(geo_su.Plane(place), outer_curve, inner_boundaries=[])

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, face, self.color, bool_operations=booleans)

    def solid_geom(self) -> Geometry:
        import numpy as np

        import ada.geom.solids as geo_so
        import ada.geom.surfaces as geo_su
        from ada import Placement
        from ada.geom.booleans import BooleanOperation
        from ada.geom.placement import Axis2Placement3D

        outer_curve = self.poly.curve_geom(use_3d_segments=False)
        profile = geo_su.ArbitraryProfileDef(geo_su.ProfileType.AREA, outer_curve, [])
        origin = self.poly.origin
        normal = self.poly.normal
        xdir = self.poly.xdir

        if self.placement.is_identity() is False:
            ident_place = Placement()
            place_abs = self.placement.get_absolute_placement(include_rotations=True)
            place_abs_rot_mat = place_abs.rot_matrix
            ident_rot_mat = ident_place.rot_matrix
            if not np.allclose(place_abs_rot_mat, ident_rot_mat):
                new_vectors = place_abs.transform_array_from_other_place(
                    np.asarray([normal, xdir]), ident_place, ignore_translation=True
                )
                new_normal = new_vectors[0]
                if Direction(new_normal).get_length() != 0.0:
                    normal = new_normal
                xdir = new_vectors[1]

            origin = place_abs.origin + origin

        # Origin location is already included in the outer_curve definition
        place = Axis2Placement3D(location=origin, axis=normal, ref_direction=xdir)
        solid = geo_so.ExtrudedAreaSolid(profile, place, self.t, Direction(0, 0, 1))
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def copy_to(self, name: str = None, origin=None, xdir=None, n=None):
        import copy

        if name is None:
            name = self.name

        if origin is None:
            origin = self.placement.origin

        return Plate(name, self.poly.copy_to(origin, xdir, n), copy.copy(self.t), self.material.copy_to())

    @property
    def id(self):
        return self._pl_id

    @id.setter
    def id(self, value):
        self._pl_id = value

    @property
    def t(self) -> float:
        """Plate thickness"""
        return self._t

    @t.setter
    def t(self, value: float):
        self._t = value

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value: Material):
        self._material = value

    @property
    def normal(self) -> Direction:
        """Normal vector"""
        return self.poly.normal

    @property
    def nodes(self) -> list[Node]:
        return self.poly.nodes

    @property
    def poly(self) -> CurvePoly2d:
        return self._poly

    @property
    def units(self) -> Units:
        return self._units

    @units.setter
    def units(self, value: Units | Literal["mm", "m"]):
        if isinstance(value, str):
            value = Units.from_str(value)
        if self._units != value:
            scale_factor = Units.get_scale_factor(self._units, value)
            tol = Config().general_mmtol if value == "mm" else Config().general_mtol
            self._t *= scale_factor
            self.poly.scale(scale_factor, tol)
            for pen in self.booleans:
                pen.units = value
            self.material.units = value
            self._units = value
            # Todo: incorporate change_type
            # self.change_type = ChangeAction.MODIFIED

    def __repr__(self):
        pts = [
            list(x) + [self.poly.radiis.get(i)] if i in self.poly.radiis.keys() else list(x)
            for i, x in enumerate(self.poly.points2d)
        ]
        origin = f"origin={self.poly.origin.tolist()}"
        xdir = f"xdir={self.poly.xdir.tolist()}"
        normal = f"normal={self.poly.normal.tolist()}"
        return f'{self.__class__.__name__}("{self.name}", {pts}, t={self.t}, mat="{self.material.name}", {origin}, {xdir}, {normal})'


class PlateCurved(BackendGeom):
    def __init__(self, name, face_geom: Geometry, t: float, mat: str | Material = "S420", **kwargs):
        super().__init__(name, **kwargs)
        self._geom = face_geom
        self._material = mat if isinstance(mat, Material) else Material(mat, mat_model=CarbonSteel(mat), parent=self)
        self._t = t

    @property
    def t(self) -> float:
        return self._t

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value: Material):
        self._material = value

    @property
    def geom(self) -> Geometry:
        return self._geom

    def solid_geom(self) -> Geometry:
        return self.geom

    def solid_occ(self) -> TopoDS_Shape | TopoDS_Compound:
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())
