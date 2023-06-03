from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np

import ada.concepts.plates.geom_plates as geo_pl
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.concepts.bounding_box import BoundingBox
from ada.concepts.curves import CurvePoly
from ada.concepts.nodes import Node
from ada.concepts.transforms import Placement
from ada.config import Settings
from ada.geom import Geometry
from ada.materials import Material
from ada.materials.metals import CarbonSteel

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


class Plate(BackendGeom):
    """
    A plate object. The plate element covers all plate elements. Contains a dictionary with each point of the plate
    described by an id (index) and a Node object.

    :param name: Name of plate
    :param points: List of coordinates that make up the plate. Points can be Node, tuple or list
    :param t: Thickness of plate
    :param mat: Material. Can be either Material object or built-in materials ('S420' or 'S355')
    :param placement: Explicitly define origin of plate. If not set
    """

    def __init__(
            self,
            name: str,
            points: CurvePoly | list[tuple[float, float, Optional[float]]],
            t: float,
            mat: str | Material = "S420",
            placement=None,
            origin=None,
            xdir=None,
            normal=None,
            pl_id=None,
            color=None,
            parent=None,
            opacity=1.0,
            metadata=None,
            tol=None,
            units=Units.M,
            guid=None,
            ifc_store: IfcStore = None,
    ):
        placement = Placement(origin, xdir=xdir, zdir=normal) if placement is None else placement

        super().__init__(
            name,
            guid=guid,
            metadata=metadata,
            units=units,
            placement=placement,
            ifc_store=ifc_store,
            color=color,
            opacity=opacity,
        )
        self._pl_id = pl_id
        self._material = mat if isinstance(mat, Material) else Material(mat, mat_model=CarbonSteel(mat), parent=parent)
        self._material.refs.append(self)
        self._t = t

        if tol is None:
            tol = Units.get_general_point_tol(units)

        if isinstance(points, CurvePoly):
            self._poly = points
        else:
            self._poly = CurvePoly(
                points=points,
                normal=self.placement.zdir,
                origin=self.placement.origin,
                xdir=self.placement.xdir,
                tol=tol,
                parent=self,
            )

        self._parent = parent
        self._bbox = None

    @staticmethod
    def from_3d_points(name, points, t, mat="S420", **kwargs):
        poly = CurvePoly.from_3d_points(points, **kwargs)
        return Plate(name, poly, t, mat=mat, **kwargs)

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
    def n(self) -> np.ndarray:
        """Normal vector"""
        return self.poly.normal

    @property
    def nodes(self) -> list[Node]:
        return self.poly.nodes

    @property
    def poly(self) -> CurvePoly:
        return self._poly

    def bbox(self) -> BoundingBox:
        """Bounding Box of plate"""
        if self._bbox is None:
            self._bbox = BoundingBox(self)

        return self._bbox

    def line_occ(self):
        return self._poly.wire()

    def shell_occ(self):
        from ada.occ.utils import apply_booleans

        geom = apply_booleans(self.poly.face(), self.booleans)

        return geom

    def solid_occ(self):
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def solid_geom(self) -> Geometry:
        return geo_pl.plate_to_geom(self)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if self._units != value:
            scale_factor = Units.get_scale_factor(self._units, value)
            tol = Settings.mmtol if value == "mm" else Settings.mtol
            self._t *= scale_factor
            self.poly.scale(scale_factor, tol)
            for pen in self.booleans:
                pen.units = value
            self.material.units = value
            self._units = value

    def __repr__(self):
        pts = [list(x) for x in self.poly.points2d]
        return f"Plate(\"{self.name}\", {pts}, t={self.t}, \"{self.material.name}\", {self.placement})"


class PlateCurved(Plate):
    def __init__(self):
        super().__init__()

# https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/IfcBSplineSurfaceWithKnots.htm
