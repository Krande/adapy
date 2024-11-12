from __future__ import annotations

from enum import Enum


class SectionTypes(Enum):
    IfcIShapeProfileDef = "IfcIShapeProfileDef"
    IfcArbitraryClosedProfileDef = "IfcArbitraryClosedProfileDef"
    IfcArbitraryProfileDefWithVoids = "IfcArbitraryProfileDefWithVoids"
    IfcCircleProfileDef = "IfcCircleProfileDef"
    IfcCircleHollowProfileDef = "IfcCircleHollowProfileDef"
    IfcUShapeProfileDef = "IfcUShapeProfileDef"

    @staticmethod
    def from_str(class_name: str):
        key_map = {x.value.lower(): x for x in SectionTypes}
        return key_map.get(class_name.lower())


class SpatialTypes(Enum):
    # Spatial Top Element
    IfcSite = "IfcSite"
    # Spatial SubElements
    IfcBuilding = "IfcBuilding"
    IfcSpatialZone = "IfcSpatialZone"
    IfcSpace = "IfcSpace"
    IfcBuildingStorey = "IfcBuildingStorey"
    IfcElementAssembly = "IfcElementAssembly"
    IfcGrid = "IfcGrid"

    @staticmethod
    def from_str(class_name: str):
        key_map = {x.value.lower(): x for x in SpatialTypes}
        return key_map.get(class_name.lower())

    @staticmethod
    def is_valid_spatial_type(ifc_class: str | SpatialTypes) -> bool:
        if isinstance(ifc_class, str):
            ifc_class = SpatialTypes.from_str(ifc_class)

        return ifc_class in list(SpatialTypes)


class ShapeTypes(Enum):
    IfcBuildingElementProxy = "IfcBuildingElementProxy"
