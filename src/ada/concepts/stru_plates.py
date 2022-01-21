from __future__ import annotations

from typing import TYPE_CHECKING, List

import numpy as np

from ada.base.physical_objects import BackendGeom
from ada.concepts.bounding_box import BoundingBox
from ada.concepts.curves import CurvePoly
from ada.concepts.points import Node
from ada.concepts.transforms import Placement
from ada.config import Settings
from ada.materials import Material
from ada.materials.metals import CarbonSteel

if TYPE_CHECKING:
    from ada.ifc.concepts import IfcRef


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
        units="m",
        ifc_elem=None,
        guid=None,
        ifc_ref: "IfcRef" = None,
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
            if units == "mm":
                tol = Settings.mmtol
            elif units == "m":
                tol = Settings.mtol
            else:
                raise ValueError(f'Unknown unit "{units}"')

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

    def _generate_ifc_elem(self):
        from ada.ifc.write.write_plates import write_ifc_plate

        return write_ifc_plate(self)

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
    def material(self) -> "Material":
        return self._material

    @material.setter
    def material(self, value: "Material"):
        self._material = value

    @property
    def n(self) -> np.ndarray:
        """Normal vector"""
        return self.poly.normal

    @property
    def nodes(self) -> List[Node]:
        return self.poly.nodes

    @property
    def poly(self) -> "CurvePoly":
        return self._poly

    @property
    def bbox(self) -> BoundingBox:
        """Bounding Box of plate"""
        if self._bbox is None:
            self._bbox = BoundingBox(self)

        return self._bbox

    @property
    def metadata(self):
        return self._metadata

    @property
    def line(self):
        return self._poly.wire

    @property
    def shell(self):
        from ada.occ.utils import apply_penetrations

        geom = apply_penetrations(self.poly.face, self.penetrations)

        return geom

    @property
    def solid(self):
        from ada.occ.utils import apply_penetrations

        geom = apply_penetrations(self._poly.make_extruded_solid(self.t), self.penetrations)

        return geom

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if self._units != value:
            from ada.core.utils import unit_length_conversion

            scale_factor = unit_length_conversion(self._units, value)
            tol = Settings.mmtol if value == "mm" else Settings.mtol
            self._t *= scale_factor
            self.poly.scale(scale_factor, tol)
            for pen in self.penetrations:
                pen.units = value
            self.material.units = value
            self._units = value

    def __repr__(self):
        return f"Plate({self.name}, t:{self.t}, {self.material})"
