from typing import Iterable, Union

import ifcopenshell

from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

from .solids import extruded_solid_area

GEOM = Union[geo_so.SOLID_GEOM_TYPES | geo_cu.CURVE_GEOM_TYPES | geo_su.SURFACE_GEOM_TYPES]


def get_product_definitions(prod_def: ifcopenshell.entity_instance) -> Iterable[GEOM]:
    for representation in prod_def.Representation.Representations:
        if representation.RepresentationIdentifier != "Body":
            continue
        for item in representation.Items:
            yield import_geometry_from_ifc_geom(item)


def import_geometry_from_ifc_geom(geom_repr: ifcopenshell.entity_instance) -> GEOM:
    if geom_repr.is_a("IfcExtrudedAreaSolid"):
        return extruded_solid_area(geom_repr)
    else:
        raise NotImplementedError(f"Geometry type {geom_repr.is_a()} not implemented")
