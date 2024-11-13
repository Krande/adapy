from __future__ import annotations

from typing import TYPE_CHECKING

import ada.geom.surfaces as geo_su
from ada import (
    Boolean,
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
from ada.cadit.ifc.utils import (
    add_colour,
    create_ifc_placement,
    create_ifcextrudedareasolid,
    create_ifcindexpolyline,
    create_ifcrevolveareasolid,
    create_local_placement,
    tesselate_shape,
)
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.core.constants import O, X, Z
from ada.core.utils import to_real
from ada.geom.solids import Box, Cone, Cylinder

from ..write.geom.curves import indexed_poly_curve
from ..write.geom.surfaces import (
    advanced_face,
    arbitrary_profile_def,
    create_closed_shell,
    curve_bounded_plane,
)

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
    a = shape.parent.get_assembly()
    body_context = a.ifc_store.get_context("Body")

    if isinstance(shape, Boolean):
        raise ValueError(f'Penetration type "{shape}" is not yet supported')

    param_geom_map = {
        PrimSphere: generate_ifc_prim_sphere_geom,
        PrimBox: generate_ifc_box_geom,
        PrimCyl: generate_ifc_cylinder_geom,
        PrimCone: generate_ifc_cone_geom,
        PrimExtrude: generate_ifc_prim_extrude_geom,
        PrimRevolve: generate_ifc_prim_revolve_geom,
        PrimSweep: generate_ifc_prim_sweep_geom,
        geo_su.AdvancedFace: advanced_face,
        geo_su.CurveBoundedPlane: curve_bounded_plane,
        geo_su.ClosedShell: create_closed_shell,
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
        PrimSphere: "SweptSolid",
        PrimBox: "SweptSolid",
        PrimCyl: "SweptSolid",
        PrimCone: "SweptSolid",
        PrimExtrude: "SweptSolid",
        PrimRevolve: "SweptSolid",
        PrimSweep: "SweptSolid",
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


def generate_ifc_cone_geom(shape: PrimCone, f):
    c_geom: Cone = shape.solid_geom().geometry
    axis3d = ifc_placement_from_axis3d(c_geom.position, f)
    return f.createIfcRightCircularCone(Position=axis3d, Height=c_geom.height, BottomRadius=c_geom.bottom_radius)


def generate_ifc_prim_sphere_geom(shape: PrimSphere, f):
    """Create IfcSphere from primitive PrimSphere"""
    opening_axis_placement = create_ifc_placement(f, to_real(shape.cog), Z, X)
    return f.createIfcSphere(opening_axis_placement, float(shape.radius))


def generate_ifc_box_geom(shape: PrimBox, f):
    """Create IfcBlock from primitive PrimBox"""
    geom: Box = shape.solid_geom().geometry
    axis3d = ifc_placement_from_axis3d(geom.position, f)
    return f.createIfcBlock(Position=axis3d, XLength=geom.x_length, YLength=geom.y_length, ZLength=geom.z_length)


def generate_ifc_cylinder_geom(shape: PrimCyl, f):
    """Create IfcExtrudedAreaSolid from primitive PrimCyl"""
    cyl_geom: Cylinder = shape.solid_geom().geometry
    axis3d = ifc_placement_from_axis3d(cyl_geom.position, f)
    return f.createIfcRightCircularCylinder(Position=axis3d, Height=cyl_geom.height, Radius=cyl_geom.radius)


def generate_ifc_prim_extrude_geom(shape: PrimExtrude, f):
    """Create IfcExtrudedAreaSolid from primitive PrimExtrude"""
    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm
    # polyline = self.create_ifcpolyline(self.file, [p[:3] for p in points])
    normal = shape.poly.normal
    h = shape.extrude_depth
    points = [tuple(x.astype(float).tolist()) for x in shape.poly.seg_global_points]
    seg_index = shape.poly.seg_index
    polyline = create_ifcindexpolyline(f, points, seg_index)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
    opening_axis_placement = create_ifc_placement(f, O, Z, X)
    return create_ifcextrudedareasolid(f, profile, opening_axis_placement, [float(n) for n in normal], h)


def generate_ifc_prim_revolve_geom(shape: PrimRevolve, f):
    """Create IfcRevolveAreaSolid from primitive PrimRevolve"""
    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm
    # 8.8.3.28 IfcRevolvedAreaSolid

    revolve_axis = [float(n) for n in shape.revolve_axis]
    revolve_origin = [float(x) for x in shape.revolve_origin]
    revolve_angle = shape.revolve_angle
    points = [tuple(x.astype(float).tolist()) for x in shape.poly.seg_global_points]
    seg_index = shape.poly.seg_index
    polyline = create_ifcindexpolyline(f, points, seg_index)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
    opening_axis_placement = create_ifc_placement(f, O, Z, X)
    return create_ifcrevolveareasolid(
        f,
        profile,
        opening_axis_placement,
        revolve_origin,
        revolve_axis,
        revolve_angle,
    )


def generate_ifc_prim_sweep_geom(shape: PrimSweep, f):
    geom = shape.solid_geom()

    profile = arbitrary_profile_def(geom.geometry.swept_area, f)
    sweep_curve = indexed_poly_curve(geom.geometry.directrix, f)

    fixed_ref = f.create_entity("IfcDirection", to_real(shape.sweep_curve.start_vector.tolist()))
    axis3d = create_ifc_placement(f)
    return f.create_entity(
        "IfcFixedReferenceSweptAreaSolid",
        SweptArea=profile,
        Position=axis3d,
        Directrix=sweep_curve,
        FixedReference=fixed_ref,
    )
