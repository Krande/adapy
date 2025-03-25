from __future__ import annotations

import numpy as np

from ada import PipeSegElbow, Units
from ada.cadit.ifc.utils import (
    add_colour,
    create_ifc_placement,
    create_ifcpolyline,
    create_local_placement,
    write_elem_property_sets,
)
from ada.cadit.ifc.write.geom import solids as igeo_so
from ada.core.constants import O
from ada.core.utils import to_real
from ada.core.vector_transforms import global_2_local_nodes
from ada.core.vector_utils import angle_between, calc_yvec, calc_zvec, unit_vector
from ada.geom import solids as geo_so


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
        GlobalId=pipe_elbow.guid,
        OwnerHistory=owner_history,
        Name=pipe_elbow.name,
        Description="An curved pipe segment",
        ObjectType=None,
        ObjectPlacement=pfitting_placement,
        Representation=ifc_elbow,
        Tag=None,
        PredefinedType="BEND",
    )

    props = dict(
        bend_radius=pipe_elbow.bend_radius,
        p1=pipe_elbow.arc_seg.p1,
        p2=pipe_elbow.arc_seg.p2,
        midpoint=pipe_elbow.arc_seg.midpoint,
    )

    write_elem_property_sets(props, pfitting, f, owner_history)

    return pfitting


def alt_elbow_revolved_solid(elbow: PipeSegElbow, f, tol=1e-1):
    arc = elbow.arc_seg

    xvec1 = unit_vector(arc.s_normal)
    xvec2 = unit_vector(arc.e_normal)
    normal = unit_vector(calc_zvec(xvec2, xvec1))

    a = elbow.get_assembly()
    ifc_store = a.ifc_store

    # Profile
    profile = ifc_store.get_profile_def(elbow.section)

    # Revolve Angle
    revolve_angle = 180 - np.rad2deg(angle_between(xvec1, xvec2))

    # Revolve Point
    diff = arc.center - arc.p1

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
    ifc_shape = f.create_entity("IfcRevolvedAreaSolid", profile, position, revolve_axis1, revolve_angle)
    return ifc_shape


def elbow_revolved_solid(elbow: PipeSegElbow, f, tol=1e-1):
    core_geom = elbow.solid_geom(ifc_impl=True)
    geom: geo_so.RevolvedAreaSolid = core_geom.geometry

    rev_area_solid = igeo_so.revolved_area_solid(geom, f)

    if core_geom.color is not None:
        add_colour(f, rev_area_solid, str(core_geom.color), core_geom.color)

    p1, p2, p3 = elbow.p1.p, elbow.p2.p, elbow.p3.p

    a = elbow.get_assembly()
    ifc_store = a.ifc_store

    body = f.create_entity(
        "IfcShapeRepresentation", ifc_store.get_context("Body"), "Body", "SweptSolid", [rev_area_solid]
    )

    # Axis representation
    polyline = create_ifcpolyline(f, [p1, p2, p3])
    axis = f.create_entity("IfcShapeRepresentation", ifc_store.get_context("Axis"), "Axis", "Curve3D", [polyline])

    # Final Product Shape
    prod_def_shp = f.create_entity("IfcProductDefinitionShape", None, None, (body, axis))

    return prod_def_shp
