from typing import Union

import ifcopenshell

from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

from .solids import extruded_solid_area, ifc_block, revolved_solid_area
from .surfaces import advanced_face, shell_based_surface_model, triangulated_face_set

GEOM = Union[geo_so.SOLID_GEOM_TYPES | geo_cu.CURVE_GEOM_TYPES | geo_su.SURFACE_GEOM_TYPES]


def get_product_definitions(prod_def: ifcopenshell.entity_instance) -> list[GEOM]:
    geometries = []
    for representation in prod_def.Representation.Representations:
        if representation.RepresentationIdentifier != "Body":
            continue
        for item in representation.Items:
            geometries.append(import_geometry_from_ifc_geom(item))

    return geometries


def import_geometry_from_ifc_geom(geom_repr: ifcopenshell.entity_instance) -> GEOM:
    if geom_repr.is_a("IfcExtrudedAreaSolid"):
        return extruded_solid_area(geom_repr)
    elif geom_repr.is_a("IfcRevolvedAreaSolid"):
        return revolved_solid_area(geom_repr)
    elif geom_repr.is_a("IfcTriangulatedFaceSet"):
        return triangulated_face_set(geom_repr)
    elif geom_repr.is_a("IfcBlock"):
        return ifc_block(geom_repr)
    elif geom_repr.is_a("IfcAdvancedFace"):
        return advanced_face(geom_repr)
    elif geom_repr.is_a("IfcShellBasedSurfaceModel"):
        return shell_based_surface_model(geom_repr)
    else:
        raise NotImplementedError(f"Geometry type {geom_repr.is_a()} not implemented")
