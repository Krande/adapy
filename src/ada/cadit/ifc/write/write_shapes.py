from __future__ import annotations

from typing import TYPE_CHECKING

import ada.geom.surfaces as geo_su
from ada import (
    Boolean,
    MassPoint,
    PrimBox,
    PrimCone,
    PrimCyl,
    PrimExtrude,
    PrimRevolve,
    PrimSphere,
    PrimSweep,
    Shape,
)
from ada.base.units import Units
from ada.cadit.ifc.utils import add_colour, create_local_placement, tesselate_shape
from ada.cadit.ifc.write.geom.surfaces import (
    advanced_face,
    create_closed_shell,
    create_half_space_geom,
    curve_bounded_plane,
)
from ada.cadit.ifc.write.shapes.box import generate_ifc_box_geom
from ada.cadit.ifc.write.shapes.cone import generate_ifc_cone_geom
from ada.cadit.ifc.write.shapes.cylinder import generate_ifc_cylinder_geom
from ada.cadit.ifc.write.shapes.prim_extrude_area import generate_ifc_prim_extrude_geom
from ada.cadit.ifc.write.shapes.prim_revolve_area_solid import (
    generate_ifc_prim_revolve_geom,
)
from ada.cadit.ifc.write.shapes.prim_sweep_area import generate_ifc_prim_sweep_geom
from ada.cadit.ifc.write.shapes.sphere import generate_ifc_prim_sphere_geom

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def write_ifc_shape(ifc_store: IfcStore, shape: Shape):
    if shape.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = shape.parent.get_assembly()
    f = a.ifc_store.f

    owner_history = a.ifc_store.owner_history
    parent = f.by_guid(shape.parent.guid)
    schema = f.wrapped_data.schema

    shape_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)

    if issubclass(type(shape), Shape):
        ifc_shape = generate_parametric_solid(shape, f)
    else:
        tol = Units.get_general_point_tol(a.units)
        serialized_geom = tesselate_shape(shape.solid_occ(), schema, tol)
        ifc_shape = f.add(serialized_geom)

    # Add colour
    if shape.color is not None:
        color_name = next(ifc_store.writer.color_name_gen)
        add_colour(f, ifc_shape.Representations[0].Items[0], color_name, shape.color)

    from ada.base.ifc_types import ShapeTypes

    ifc_elem = f.create_entity(
        str(shape.ifc_class.value) if isinstance(shape.ifc_class, ShapeTypes) else shape.ifc_class,
        GlobalId=shape.guid,
        OwnerHistory=owner_history,
        Name=shape.name,
        ObjectType=None,
        ObjectPlacement=shape_placement,
        Representation=ifc_shape,
    )

    return ifc_elem


def generate_parametric_solid(shape: Shape | PrimSphere, f):
    from ada.api.primitives.bool_half_space import BoolHalfSpace

    a = shape.parent.get_assembly()
    body_context = a.ifc_store.get_context("Body")

    if isinstance(shape, Boolean):
        raise ValueError(f'Penetration type "{shape}" is not yet supported')

    param_geom_map = {
        PrimSphere: generate_ifc_prim_sphere_geom,
        MassPoint: generate_ifc_prim_sphere_geom,
        PrimBox: generate_ifc_box_geom,
        PrimCyl: generate_ifc_cylinder_geom,
        PrimCone: generate_ifc_cone_geom,
        PrimExtrude: generate_ifc_prim_extrude_geom,
        PrimRevolve: generate_ifc_prim_revolve_geom,
        PrimSweep: generate_ifc_prim_sweep_geom,
        geo_su.AdvancedFace: advanced_face,
        geo_su.CurveBoundedPlane: curve_bounded_plane,
        geo_su.ClosedShell: create_closed_shell,
        # Various
        BoolHalfSpace: create_half_space_geom,
    }

    if type(shape) is Shape:
        param_geo = shape.geom.geometry
    else:
        param_geo = shape

    ifc_geom_converter = param_geom_map.get(type(param_geo), None)
    if ifc_geom_converter is None:
        raise NotImplementedError(f'Shape type "{type(shape)}" is not yet supported for export to IFC')

    solid_geom = ifc_geom_converter(param_geo, f)

    repr_type_map = {
        PrimSphere: "CSG",
        PrimBox: "CSG",
        PrimCyl: "CSG",
        PrimCone: "CSG",
        PrimExtrude: "SweptSolid",
        PrimRevolve: "SweptSolid",
        PrimSweep: "AdvancedSweptSolid",
        geo_su.AdvancedFace: "AdvancedSurface",
        geo_su.CurveBoundedPlane: "AdvancedSurface",
        geo_su.ClosedShell: "AdvancedSurface",
    }
    repr_type_str = repr_type_map.get(type(param_geo), None)
    shape_representation = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType=repr_type_str,
        Items=[solid_geom],
    )
    ifc_shape = f.create_entity(
        "IfcProductDefinitionShape", Name=None, Description=None, Representations=[shape_representation]
    )

    return ifc_shape
