import logging
from typing import TYPE_CHECKING

import numpy as np

from ada.config import Settings
from ada.core.constants import O, X, Y, Z
from ada.core.curve_utils import get_center_from_3_points_and_radius
from ada.core.vector_utils import (
    angle_between,
    normal_to_points_in_plane,
    transform3d,
    unit_vector,
    vector_length,
)
from ada.ifc.utils import (
    create_guid,
    create_ifc_placement,
    create_ifcpolyline,
    create_local_placement,
    create_property_set,
    get_tolerance,
    tesselate_shape,
    to_real,
)

if TYPE_CHECKING:
    from ada import Pipe, PipeSegElbow, PipeSegStraight


def write_ifc_pipe(pipe: "Pipe"):
    from ada import PipeSegElbow, PipeSegStraight

    ifc_pipe = write_pipe_ifc_elem(pipe)

    a = pipe.get_assembly()
    f = a.ifc_file

    owner_history = a.user.to_ifc()

    segments = []
    for param_seg in pipe.segments:
        if type(param_seg) is PipeSegStraight:
            assert isinstance(param_seg, PipeSegStraight)
            res = param_seg.get_ifc_elem()
        else:
            assert isinstance(param_seg, PipeSegElbow)
            res = param_seg.get_ifc_elem()
        if res is None:
            logging.error(f'Branch "{param_seg.name}" was not converted to ifc element')
        f.add(res)
        segments += [res]

    f.createIfcRelContainedInSpatialStructure(
        create_guid(),
        owner_history,
        "Pipe Segments",
        None,
        segments,
        ifc_pipe,
    )

    return ifc_pipe


def write_pipe_ifc_elem(pipe: "Pipe"):
    if pipe.parent is None:
        raise ValueError("Cannot build ifc element without parent")

    a = pipe.get_assembly()
    f = a.ifc_file

    owner_history = a.user.to_ifc()
    parent = pipe.parent.get_ifc_elem()

    placement = create_local_placement(
        f,
        origin=pipe.n1.p.astype(float).tolist(),
        loc_x=X,
        loc_z=Z,
        relative_to=parent.ObjectPlacement,
    )

    ifc_elem = f.createIfcSpace(
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
    if len(pipe.metadata.keys()) > 0:
        props = create_property_set("Properties", f, pipe.metadata, owner_history)
        f.createIfcRelDefinesByProperties(
            create_guid(),
            owner_history,
            "Properties",
            None,
            [ifc_elem],
            props,
        )

    return ifc_elem


def write_pipe_straight_seg(pipe_seg: "PipeSegStraight"):
    if pipe_seg.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = pipe_seg.parent.get_assembly()
    f = a.ifc_file

    context = f.by_type("IfcGeometricRepresentationContext")[0]
    owner_history = a.user.to_ifc()

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

    solid = f.createIfcExtrudedAreaSolid(pipe_seg.section.ifc_profile, extrusion_placement, ifcdir, seg_l)

    polyline = create_ifcpolyline(f, [rp1, rp2])

    axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [polyline])
    body_representation = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [solid])

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
        create_guid(),
        owner_history,
        pipe_seg.name,
        "An awesome pipe",
        None,
        local_placement,
        product_shape,
        None,
    )

    ifc_mat = pipe_seg.material.ifc_mat
    mat_profile = f.createIfcMaterialProfile(
        pipe_seg.material.name, None, ifc_mat, pipe_seg.section.ifc_profile, None, None
    )
    mat_profile_set = f.createIfcMaterialProfileSet(None, None, [mat_profile], None)
    mat_profile_set = f.createIfcMaterialProfileSetUsage(mat_profile_set, 8, None)
    f.createIfcRelAssociatesMaterial(create_guid(), None, None, None, [pipe_segment], mat_profile_set)

    return pipe_segment


def write_pipe_elbow_seg(pipe_elbow: "PipeSegElbow"):
    if pipe_elbow.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = pipe_elbow.parent.get_assembly()
    f = a.ifc_file

    context = f.by_type("IfcGeometricRepresentationContext")[0]
    owner_history = a.user.to_ifc()
    schema = a.ifc_file.wrapped_data.schema

    if Settings.make_param_elbows is False:
        ifc_elbow = elbow_tesselated(pipe_elbow, f, schema, a)
        # Link to representation context
        for rep in ifc_elbow.Representations:
            rep.ContextOfItems = context
    else:
        ifc_elbow = elbow_revolved_solid(pipe_elbow, f, context)

    pfitting_placement = create_local_placement(f)

    pfitting = f.create_entity(
        "IfcPipeFitting",
        create_guid(),
        owner_history,
        pipe_elbow.name,
        "An curved pipe segment",
        None,
        pfitting_placement,
        ifc_elbow,
        None,
        None,
    )

    ifc_mat = pipe_elbow.material.ifc_mat
    mat_profile = f.createIfcMaterialProfile(
        pipe_elbow.material.name, None, ifc_mat, pipe_elbow.section.ifc_profile, None, None
    )
    mat_profile_set = f.createIfcMaterialProfileSet(None, None, [mat_profile], None)
    mat_profile_set = f.createIfcMaterialProfileSetUsage(mat_profile_set, 8, None)
    f.createIfcRelAssociatesMaterial(create_guid(), None, None, None, [pfitting], mat_profile_set)

    return pfitting


def elbow_tesselated(self: "PipeSegElbow", f, schema, a):
    shape = self.solid

    if shape is None:
        logging.error(f"Unable to create geometry for Branch {self.name}")
        return None

    serialized_geom = tesselate_shape(shape, schema, get_tolerance(a.units))
    ifc_shape = f.add(serialized_geom)

    return ifc_shape


def elbow_revolved_solid(pipe_elbow: "PipeSegElbow", f, context):
    p1, p2, p3 = pipe_elbow.p1.p, pipe_elbow.p2.p, pipe_elbow.p3.p

    # Profile
    profile = pipe_elbow.section.ifc_profile
    normal = normal_to_points_in_plane([p1, p2, p3])

    # Revolve Angle
    xvec1 = unit_vector(pipe_elbow.xvec1)
    xvec2 = unit_vector(pipe_elbow.xvec2)
    revolve_angle = np.rad2deg(angle_between(xvec1, xvec2))

    # Revolve Point
    cd = get_center_from_3_points_and_radius(p1, p2, p3, pipe_elbow.bend_radius)
    arc_p1 = pipe_elbow.arc_seg.p1
    diff = cd.center - arc_p1

    # Transform Axis normal and position to the local coordinate system
    yvec = np.cross(xvec1, normal)
    global_csys = (X, Y, Z)
    new_csys = (normal, yvec, xvec1)

    n_tra, diff_tra = transform3d(global_csys, new_csys, O, [normal, diff])
    n_tra_norm = to_real(unit_vector(n_tra))
    diff_tra_norm = to_real(diff_tra)

    # Revolve Axis
    rev_axis_dir = f.create_entity("IfcDirection", n_tra_norm)
    revolve_point = f.create_entity("IfcCartesianPoint", diff_tra_norm)
    revolve_axis1 = f.create_entity("IfcAxis1Placement", revolve_point, rev_axis_dir)

    position = create_ifc_placement(f, arc_p1, xvec1, normal)

    # Body representation
    ifc_shape = f.create_entity("IfcRevolvedAreaSolid", profile, position, revolve_axis1, revolve_angle)
    body = f.create_entity("IfcShapeRepresentation", context, "Body", "SweptSolid", [ifc_shape])

    # Axis representation
    # curve = f.create_entity("IfcTrimmedCurve")
    # axis = f.create_entity("IfcShapeRepresentation", context, "Axis", "Curve3D", [curve])

    # Final Product Shape
    prod_def_shp = f.create_entity("IfcProductDefinitionShape", None, None, (body,))

    return prod_def_shp
