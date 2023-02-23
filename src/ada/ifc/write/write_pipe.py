from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell
import numpy as np

from ada.base.units import Units
from ada.config import get_logger
from ada.core.constants import O, X, Z
from ada.core.curve_utils import get_center_from_3_points_and_radius
from ada.core.vector_utils import (
    angle_between,
    calc_yvec,
    calc_zvec,
    global_2_local_nodes,
    unit_vector,
    vector_length,
)
from ada.ifc.utils import (
    create_guid,
    create_ifc_placement,
    create_ifcpolyline,
    create_local_placement,
    tesselate_shape,
    to_real,
    write_elem_property_sets,
)

if TYPE_CHECKING:
    from ada import Pipe, PipeSegElbow, PipeSegStraight

logger = get_logger()


def write_ifc_pipe(pipe: Pipe):
    ifc_pipe = write_pipe_ifc_elem(pipe)

    a = pipe.get_assembly()
    ifc_store = a.ifc_store
    f = ifc_store.f

    segments = []
    for param_seg in pipe.segments:
        res = write_pipe_segment(param_seg)
        if res is None:
            logger.error(f'Branch "{param_seg.name}" was not converted to ifc element')
        f.add(res)
        segments += [res]

    ifc_store.writer.add_related_elements_to_spatial_container(segments, ifc_pipe.GlobalId)

    return ifc_pipe


def write_pipe_segment(segment: PipeSegElbow | PipeSegStraight) -> ifcopenshell.entity_instance:
    from ada import PipeSegElbow, PipeSegStraight

    if isinstance(segment, PipeSegElbow):
        pipe_seg = write_pipe_elbow_seg(segment)
    elif isinstance(segment, PipeSegStraight):
        pipe_seg = write_pipe_straight_seg(segment)
    else:
        raise ValueError(f'Unrecognized Pipe Segment type "{type(segment)}"')

    assembly = segment.get_assembly()
    ifc_store = assembly.ifc_store
    ifc_store.writer.associate_elem_with_material(segment.material, pipe_seg)

    return pipe_seg


def write_pipe_ifc_elem(pipe: Pipe):
    if pipe.parent is None:
        raise ValueError("Cannot build ifc element without parent")

    a = pipe.get_assembly()
    f = a.ifc_store.f

    owner_history = a.ifc_store.owner_history
    parent = f.by_guid(pipe.parent.guid)

    placement = create_local_placement(
        f,
        origin=pipe.n1.p.astype(float).tolist(),
        loc_x=X,
        loc_z=Z,
        relative_to=parent.ObjectPlacement,
    )

    ifc_elem = f.create_entity(
        "IfcSpatialZone",
        pipe.guid,
        owner_history,
        pipe.name,
        "Description",
        None,
        placement,
        None,
        None,
        None,
    )

    f.createIfcRelAggregates(
        create_guid(),
        owner_history,
        "Site Container",
        None,
        parent,
        [ifc_elem],
    )

    return ifc_elem


def write_pipe_straight_seg(pipe_seg: PipeSegStraight):
    if pipe_seg.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    assembly = pipe_seg.parent.get_assembly()
    ifc_store = assembly.ifc_store
    f = ifc_store.f
    owner_history = ifc_store.owner_history

    p1 = pipe_seg.p1
    p2 = pipe_seg.p2

    ifcdir = f.createIfcDirection((0.0, 0.0, 1.0))

    rp1 = to_real(p1.p)
    rp2 = to_real(p2.p)
    xvec = unit_vector(p2.p - p1.p)
    a = angle_between(xvec, np.array([0, 0, 1]))
    zvec = np.array([0, 0, 1]) if a != np.pi and a != 0 else np.array([1, 0, 0])
    yvec = unit_vector(np.cross(zvec, xvec))
    seg_l = vector_length(p2.p - p1.p)

    extrusion_placement = create_ifc_placement(f, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

    section_profile = ifc_store.get_profile_def(pipe_seg.section)
    if section_profile is None:
        raise ValueError("Section profile not found")

    solid = f.createIfcExtrudedAreaSolid(section_profile, extrusion_placement, ifcdir, seg_l)

    polyline = create_ifcpolyline(f, [rp1, rp2])

    axis_representation = f.createIfcShapeRepresentation(ifc_store.get_context("Axis"), "Axis", "Curve3D", [polyline])
    body_representation = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SweptSolid", [solid])

    product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body_representation])

    origin = f.createIfcCartesianPoint(O)
    local_z = f.createIfcDirection(Z)
    local_x = f.createIfcDirection(X)
    d237 = f.createIfcLocalPlacement(None, f.createIfcAxis2Placement3D(origin, local_z, local_x))

    d256 = f.createIfcCartesianPoint(rp1)
    d257 = f.createIfcDirection(to_real(xvec))
    d258 = f.createIfcDirection(to_real(yvec))
    d236 = f.createIfcAxis2Placement3D(d256, d257, d258)
    local_placement = f.createIfcLocalPlacement(d237, d236)

    pipe_segment = f.createIfcPipeSegment(
        pipe_seg.guid,
        owner_history,
        pipe_seg.name,
        "An awesome pipe",
        None,
        local_placement,
        product_shape,
        None,
    )

    return pipe_segment


def write_pipe_elbow_seg(pipe_elbow: PipeSegElbow):
    if pipe_elbow.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = pipe_elbow.parent.get_assembly()
    f = a.ifc_store.f

    owner_history = a.ifc_store.owner_history

    tol = Units.get_general_point_tol(a.units)

    ifc_elbow = elbow_revolved_solid(pipe_elbow, f, tol)

    pfitting_placement = create_local_placement(f)

    pfitting = f.create_entity(
        "IfcPipeFitting",
        pipe_elbow.guid,
        owner_history,
        pipe_elbow.name,
        "An curved pipe segment",
        None,
        pfitting_placement,
        ifc_elbow,
        None,
        None,
    )

    props = dict(
        bend_radius=pipe_elbow.bend_radius,
        p1=pipe_elbow.arc_seg.p1,
        p2=pipe_elbow.arc_seg.p2,
        midpoint=pipe_elbow.arc_seg.midpoint,
    )

    write_elem_property_sets(props, pfitting, f, owner_history)

    return pfitting


def elbow_tesselated(self: PipeSegElbow, f, schema, a):
    shape = self.solid

    if shape is None:
        logger.error(f"Unable to create geometry for Branch {self.name}")
        return None
    point_tol = Units.get_general_point_tol(a.units)
    serialized_geom = tesselate_shape(shape, schema, point_tol)
    ifc_shape = f.add(serialized_geom)

    return ifc_shape


def elbow_revolved_solid(elbow: PipeSegElbow, f, tol=1e-1):
    xvec1 = unit_vector(elbow.xvec1)
    xvec2 = unit_vector(elbow.xvec2)
    normal = unit_vector(calc_zvec(xvec1, xvec2))
    p1, p2, p3 = elbow.p1.p, elbow.p2.p, elbow.p3.p

    a = elbow.get_assembly()
    ifc_store = a.ifc_store

    # Profile
    profile = ifc_store.get_profile_def(elbow.section)

    # Revolve Angle
    revolve_angle = np.rad2deg(angle_between(xvec1, xvec2))

    # Revolve Point
    cd = get_center_from_3_points_and_radius(p1, p2, p3, elbow.bend_radius, tol=tol)
    diff = cd.center - elbow.arc_seg.p1

    # Transform Axis normal and position to the local coordinate system
    yvec = calc_yvec(normal, xvec1)
    new_csys = (normal, yvec, xvec1)

    diff_tra = global_2_local_nodes(new_csys, O, [diff])[0]
    n_tra = global_2_local_nodes(new_csys, O, [normal])[0]

    n_tra_norm = to_real(unit_vector(n_tra))
    diff_tra_norm = to_real(diff_tra)

    # Revolve Axis
    rev_axis_dir = f.create_entity("IfcDirection", n_tra_norm)
    revolve_point = f.create_entity("IfcCartesianPoint", diff_tra_norm)
    revolve_axis1 = f.create_entity("IfcAxis1Placement", revolve_point, rev_axis_dir)

    position = create_ifc_placement(f, elbow.arc_seg.p1, xvec1, normal)

    # Body representation

    ifc_shape = f.create_entity("IfcRevolvedAreaSolid", profile, position, revolve_axis1, revolve_angle)
    body = f.create_entity("IfcShapeRepresentation", ifc_store.get_context("Body"), "Body", "SweptSolid", [ifc_shape])

    # Axis representation
    polyline = create_ifcpolyline(f, [p1, p2, p3])
    axis = f.create_entity("IfcShapeRepresentation", ifc_store.get_context("Axis"), "Axis", "Curve3D", [polyline])

    # Final Product Shape
    prod_def_shp = f.create_entity("IfcProductDefinitionShape", None, None, (body, axis))

    return prod_def_shp
