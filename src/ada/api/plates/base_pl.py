from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Union

from ada.api.bounding_box import BoundingBox
from ada.api.curves import CurvePoly2d
from ada.api.nodes import Node
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.config import Config
from ada.geom import Geometry
from ada.geom.placement import Direction
from ada.geom.points import Point
from ada.geom.solids import ExtrudedAreaSolid
from ada.materials import Material
from ada.materials.metals import CarbonSteel

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape

    from ada import Placement

_NTYPE = Union[int, float]


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
    :param n: Explicitly define normal direction of plate. If not set
    """

    def __init__(
        self,
        name: str,
        points: CurvePoly2d | list[tuple[_NTYPE, _NTYPE]],
        t: float,
        mat: str | Material = "S420",
        origin: Iterable | Point = None,
        xdir: Iterable | Direction = None,
        n: Iterable | Direction = None,
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

        if tol is None:
            tol = Units.get_general_point_tol(self.units)

        if isinstance(points, CurvePoly2d):
            self._poly = points
        else:
            self._poly = CurvePoly2d(
                points2d=points,
                normal=n,
                origin=origin,
                xdir=xdir,
                tol=tol,
                parent=self,
                orientation=orientation,
            )

        self._bbox = None

    @staticmethod
    def from_3d_points(name, points, t, mat="S420", xdir=None, color=None, metadata=None, **kwargs) -> Plate:
        poly = CurvePoly2d.from_3d_points(points, xdir=xdir, **kwargs)
        return Plate(name, poly, t, mat=mat, color=color, metadata=metadata, **kwargs)

    @staticmethod
    def from_extruded_area_solid(name, solid: ExtrudedAreaSolid): ...

    def bbox(self) -> BoundingBox:
        """Bounding Box of plate"""
        if self._bbox is None:
            self._bbox = BoundingBox(self)

        return self._bbox

    def line_occ(self):
        return self._poly.wire()

    def shell_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.shell_geom())

    def solid_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

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
        import ada.geom.solids as geo_so
        import ada.geom.surfaces as geo_su
        from ada.geom.booleans import BooleanOperation
        from ada.geom.placement import Axis2Placement3D

        outer_curve = self.poly.curve_geom(use_3d_segments=False)
        profile = geo_su.ArbitraryProfileDef(geo_su.ProfileType.AREA, outer_curve, [])

        # Origin location is already included in the outer_curve definition
        place = Axis2Placement3D(location=self.poly.origin, axis=self.poly.normal, ref_direction=self.poly.xdir)
        solid = geo_so.ExtrudedAreaSolid(profile, place, self.t, Direction(0, 0, 1))
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

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

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value: Material):
        self._material = value

    @property
    def n(self) -> Direction:
        """Normal vector"""
        return self.poly.normal

    @property
    def nodes(self) -> list[Node]:
        return self.poly.nodes

    @property
    def poly(self) -> CurvePoly2d:
        return self._poly

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
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
        origin = f"origin={self.placement.origin.tolist()}"
        xdir = f"xdir={self.poly.xdir.tolist()}"
        normal = f"normal={self.poly.normal.tolist()}"
        return f'Plate("{self.name}", {pts}, t={self.t}, "{self.material.name}", {origin}, {xdir}, {normal})'


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
