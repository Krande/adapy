from __future__ import annotations

from enum import Enum


class SpatialTypes(Enum):
    # TODO: Check to see if this enum can be grabbed from ifcopenshell directly
    # Non-physical Container Top Element
    IfcSite = "IfcSite"
    # Non-physical Container SubElements
    IfcBuilding = "IfcBuilding"
    IfcSpatialZone = "IfcSpatialZone"
    IfcBuildingStorey = "IfcBuildingStorey"

    @staticmethod
    def from_str(class_name: str):
        key_map = {x.value.lower(): x for x in SpatialTypes}
        return key_map.get(class_name.lower())

    @staticmethod
    def is_valid_spatial_type(ifc_class: str | SpatialTypes) -> bool:
        if isinstance(ifc_class, str):
            ifc_class = SpatialTypes.from_str(ifc_class)

        return ifc_class in list(SpatialTypes)
