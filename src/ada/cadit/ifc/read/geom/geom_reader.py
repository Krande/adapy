from typing import Union

import ifcopenshell

from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

from .solids import (
    extruded_solid_area,
    ifc_block,
    ifc_cone,
    ifc_cylinder,
    ifc_sphere,
    revolved_solid_area,
)
from .surfaces import (
    advanced_face,
    half_space_solid,
    shell_based_surface_model,
    triangulated_face_set,
)

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
    elif geom_repr.is_a("IfcSphere"):
        return ifc_sphere(geom_repr)
    elif geom_repr.is_a("IfcRightCircularCylinder"):
        return ifc_cylinder(geom_repr)
    elif geom_repr.is_a("IfcRightCircularCone"):
        return ifc_cone(geom_repr)
    elif geom_repr.is_a("IfcAdvancedFace"):
        return advanced_face(geom_repr)
    elif geom_repr.is_a("IfcShellBasedSurfaceModel"):
        return shell_based_surface_model(geom_repr)
    elif geom_repr.is_a("IfcHalfSpaceSolid"):
        # Covers the IfcPolygonalBoundedHalfSpace subtype too.
        return half_space_solid(geom_repr)
    elif geom_repr.is_a("IfcBooleanResult"):
        # Covers IfcBooleanClippingResult (a subtype). Returns a wrapped Geometry carrying
        # the cut(s) as bool_operations, not a raw geom — callers handle both (read_shapes).
        return boolean_result(geom_repr)
    else:
        raise NotImplementedError(f"Geometry type {geom_repr.is_a()} not implemented")


def boolean_result(geom_repr: ifcopenshell.entity_instance) -> "Geometry":
    """Read an IfcBooleanResult/IfcBooleanClippingResult into a base Geometry with the cut
    operand(s) attached as bool_operations (applied downstream by apply_geom_booleans).

    Nested booleans (FirstOperand is itself a boolean — adapy stacks each clip as its own
    result) collapse onto a single base Geometry: the recursive call returns a Geometry and
    we append this level's operation to it."""
    from ada.geom import Geometry
    from ada.geom.booleans import BooleanOperation, BoolOpEnum

    first = import_geometry_from_ifc_geom(geom_repr.FirstOperand)
    second = import_geometry_from_ifc_geom(geom_repr.SecondOperand)

    base = first if isinstance(first, Geometry) else Geometry(geom_repr.FirstOperand.id(), first)
    operand = second if isinstance(second, Geometry) else Geometry(geom_repr.SecondOperand.id(), second)
    base.bool_operations.append(BooleanOperation(operand, BoolOpEnum.from_str(geom_repr.Operator)))
    return base
