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


#: Metadata key naming the IFC element entity an object is written as. Its value is the mapping
#: :func:`ifc_entity_spec` returns. Honoured on a ``Shape`` and on a ``Part`` (which is then written
#: as that element instead of a spatial one) by the in-memory IFC writer, and never written as a
#: property set.
IFC_ENTITY_KEY = "ifc_entity"


def ifc_entity_spec(
    entity: str,
    predefined_type: str | None = None,
    schemas: dict[str, dict] | None = None,
) -> dict:
    """The value of :data:`IFC_ENTITY_KEY`.

    ``schemas`` maps a schema identifier (``IFC4``, ``IFC4X3`` or ``IFC4X3_ADD2``) to the keys
    (``entity``, ``predefined_type``) that replace the base ones when writing that schema.
    Validity against a schema is checked when the file is written, not here.
    """
    if not isinstance(entity, str) or not entity.strip():
        raise ValueError("an IFC entity must be a non-empty string")
    spec: dict = {"entity": entity.strip()}
    if predefined_type:
        spec["predefined_type"] = str(predefined_type).strip().upper()
    for schema, override in (schemas or {}).items():
        if not isinstance(override, dict):
            raise ValueError(f"the {schema!r} override must be a mapping")
        unknown = set(override) - {"entity", "predefined_type"}
        if unknown:
            raise ValueError(f"unknown key(s) {sorted(unknown)} in the {schema!r} override")
        spec.setdefault("schemas", {})[str(schema).upper()] = dict(override)
    return spec
