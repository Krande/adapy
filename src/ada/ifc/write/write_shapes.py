from __future__ import annotations

import numpy as np

from ada import (
    Penetration,
    PrimBox,
    PrimCyl,
    PrimExtrude,
    PrimRevolve,
    PrimSphere,
    PrimSweep,
    Shape,
)
from ada.base.units import Units
from ada.core.constants import O, X, Z
from ada.core.vector_utils import unit_vector, vector_length
from ada.ifc.utils import (
    add_colour,
    create_ifc_placement,
    create_ifcextrudedareasolid,
    create_IfcFixedReferenceSweptAreaSolid,
    create_ifcindexpolyline,
    create_ifcpolyline,
    create_ifcrevolveareasolid,
    create_local_placement,
    tesselate_shape,
    to_real,
)

from .write_curves import write_curve_poly


def write_ifc_shape(shape: Shape):
    if shape.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = shape.parent.get_assembly()
    f = a.ifc_store.f

    owner_history = a.ifc_store.owner_history
    parent = f.by_guid(shape.parent.guid)
    schema = f.wrapped_data.schema

    shape_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)

    if isinstance(shape, (PrimBox, PrimCyl, PrimExtrude, PrimRevolve, PrimSphere, PrimSweep)):
        ifc_shape = generate_parametric_solid(shape, f)
    else:
        tol = Units.get_general_point_tol(a.units)
        serialized_geom = tesselate_shape(shape.geom(), schema, tol)
        ifc_shape = f.add(serialized_geom)

    # Add colour
    if shape.colour is not None:
        add_colour(f, ifc_shape.Representations[0].Items[0], str(shape.colour), shape.colour)

    ifc_elem = f.create_entity(
        str(shape.ifc_class.value),
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

    param_solid_map = {
        PrimSphere: generate_ifc_PrimSphere_geom,
        PrimBox: generate_ifc_box_geom,
        PrimCyl: generate_ifc_cylinder_geom,
        PrimExtrude: generate_ifc_prim_extrude_geom,
        PrimRevolve: generate_ifc_prim_revolve_geom,
        PrimSweep: generate_ifc_prim_sweep_geom,
    }

    ifc_geom_converter = param_solid_map.get(type(shape), None)
    if ifc_geom_converter is None:
        raise NotImplementedError(f'Shape type "{type(shape)}" is not yet supported for export to IFC')

    solid_geom = ifc_geom_converter(shape, f)

    if type(shape) is Penetration:
        raise ValueError(f'Penetration type "{shape}" is not yet supported')

    shape_representation = f.create_entity("IfcShapeRepresentation", body_context, "Body", "SweptSolid", [solid_geom])
    ifc_shape = f.create_entity("IfcProductDefinitionShape", None, None, [shape_representation])

    return ifc_shape


def generate_ifc_PrimSphere_geom(shape: PrimSphere, f):
    """Create IfcSphere from primitive PrimSphere"""
    opening_axis_placement = create_ifc_placement(f, to_real(shape.cog), Z, X)
    return f.createIfcSphere(opening_axis_placement, float(shape.radius))


def generate_ifc_box_geom(shape: PrimBox, f):
    """Create IfcExtrudedAreaSolid from primitive PrimBox"""
    p1 = shape.p1
    p2 = shape.p2
    points = [
        p1,
        (p1[0], p2[1], p1[2]),
        (p2[0], p2[1], p1[2]),
        (p2[0], p1[1], p1[2]),
    ]
    depth = p2[2] - p1[2]
    polyline = create_ifcpolyline(f, points)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
    opening_axis_placement = create_ifc_placement(f, O, Z, X)
    return create_ifcextrudedareasolid(f, profile, opening_axis_placement, (0.0, 0.0, 1.0), depth)


def generate_ifc_cylinder_geom(shape: PrimCyl, f):
    """Create IfcExtrudedAreaSolid from primitive PrimCyl"""
    p1 = shape.p1
    p2 = shape.p2
    r = shape.r

    vec = np.array(p2) - np.array(p1)
    uvec = unit_vector(vec)
    vecdir = to_real(uvec)

    cr_dir = np.array([0, 0, 1])

    if vector_length(abs(uvec) - abs(cr_dir)) == 0.0:
        cr_dir = np.array([1, 0, 0])

    perp_dir = np.cross(uvec, cr_dir)

    if vector_length(perp_dir) == 0.0:
        raise ValueError("Perpendicular dir cannot be zero")

    create_ifc_placement(f, to_real(p1), vecdir, to_real(perp_dir))

    opening_axis_placement = create_ifc_placement(f, to_real(p1), vecdir, to_real(perp_dir))

    depth = vector_length(vec)
    profile = f.createIfcCircleProfileDef("AREA", shape.name, None, r)
    return create_ifcextrudedareasolid(f, profile, opening_axis_placement, Z, depth)


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
    sweep_curve = write_curve_poly(shape.sweep_curve)
    outer_curve = write_curve_poly(shape.profile_curve_outer)
    profile = f.create_entity("IfcArbitraryClosedProfileDef", "AREA", None, outer_curve)
    ifc_xdir = f.create_entity("IfcDirection", [float(x) for x in shape.profile_curve_outer.xdir])
    opening_axis_placement = create_ifc_placement(f, O, Z, X)

    return create_IfcFixedReferenceSweptAreaSolid(f, sweep_curve, profile, opening_axis_placement, None, None, ifc_xdir)
