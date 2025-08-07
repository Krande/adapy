from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Literal

from ada.api.bounding_box import BoundingBox
from ada.api.transforms import Placement
from ada.base.ifc_types import ShapeTypes
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation
from ada.geom.points import Point
from ada.materials.concept import Material
from ada.materials.utils import get_material

if TYPE_CHECKING:
    import trimesh
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
        material: Material | Literal["S355", "S420"] = None,
        units=Units.M,
        metadata=None,
        guid=None,
        placement=None,
        ifc_store: IfcStore = None,
        ifc_class: ShapeTypes = ShapeTypes.IfcBuildingElementProxy,
        parent=None,
    ):
        if placement is None:
            placement = Placement()
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

    def solid_trimesh(self) -> trimesh.Trimesh:
        from ada.occ.tessellating import shape_to_tri_mesh

        return shape_to_tri_mesh(self.solid_occ())

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
