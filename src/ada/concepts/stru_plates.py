from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.concepts.bounding_box import BoundingBox
from ada.concepts.curves import CurvePoly
from ada.concepts.points import Node
from ada.concepts.transforms import Placement
from ada.config import Settings
from ada.materials import Material
from ada.materials.metals import CarbonSteel

if TYPE_CHECKING:
    from ada.ifc.store import IfcStore


class Plate(BackendGeom):
    """
    A plate object. The plate element covers all plate elements. Contains a dictionary with each point of the plate
    described by an id (index) and a Node object.

    :param name: Name of plate
    :param nodes: List of coordinates that make up the plate. Points can be Node, tuple or list
    :param t: Thickness of plate
    :param mat: Material. Can be either Material object or built-in materials ('S420' or 'S355')
    :param placement: Explicitly define origin of plate. If not set
    """

    def __init__(
        self,
        name,
        nodes,
        t,
        mat="S420",
        use3dnodes=False,
        placement=Placement(),
        pl_id=None,
        offset=None,
        colour=None,
        parent=None,
        ifc_geom=None,
        opacity=1.0,
        metadata=None,
        tol=None,
        units=Units.M,
        guid=None,
        ifc_store: IfcStore = None,
    ):
        super().__init__(
            name,
            guid=guid,
            metadata=metadata,
            units=units,
            placement=placement,
            ifc_store=ifc_store,
            colour=colour,
            opacity=opacity,
        )

        points2d = None
        points3d = None

        if use3dnodes is True:
            points3d = nodes
        else:
            points2d = nodes

        self._pl_id = pl_id
        self._material = mat if isinstance(mat, Material) else Material(mat, mat_model=CarbonSteel(mat), parent=parent)
        self._material.refs.append(self)
        self._t = t

        if tol is None:
            tol = Units.get_general_point_tol(units)

        self._poly = CurvePoly(
            points3d=points3d,
            points2d=points2d,
            normal=self.placement.zdir,
            origin=self.placement.origin,
            xdir=self.placement.xdir,
            tol=tol,
            parent=self,
        )

        self._offset = offset
        self._parent = parent
        self._ifc_geom = ifc_geom
        self._bbox = None

    @property
    def id(self):
        return self._pl_id

    @id.setter
    def id(self, value):
        self._pl_id = value

    @property
    def offset(self):
        return self._offset

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

    def line(self):
        return self._poly.wire

    def shell(self):
        from ada.occ.utils import apply_penetrations

        geom = apply_penetrations(self.poly.face, self.penetrations)

        return geom

    def solid(self):
        from ada.occ.utils import apply_penetrations

        geom = apply_penetrations(self._poly.make_extruded_solid(self.t), self.penetrations)

        return geom

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
            for pen in self.penetrations:
                pen.units = value
            self.material.units = value
            self._units = value

    def __repr__(self):
        return f"Plate({self.name}, t:{self.t}, {self.material})"


# https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/IfcBSplineSurfaceWithKnots.htm
