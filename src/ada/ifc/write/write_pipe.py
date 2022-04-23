import logging
from typing import TYPE_CHECKING

import numpy as np

from ada.config import Settings
from ada.core.constants import O, X, Z
from ada.core.curve_utils import get_center_from_3_points_and_radius
from ada.core.vector_utils import (
    angle_between,
    normal_to_points_in_plane,
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

    pfitting = f.createIfcPipeFitting(
        create_guid(),
        owner_history,
        pipe_elbow.name,
        "An awesome Elbow",
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

    center, _, _, _ = get_center_from_3_points_and_radius(
        pipe_elbow.p1.p, pipe_elbow.p2.p, pipe_elbow.p3.p, pipe_elbow.bend_radius
    )

    opening_axis_placement = create_ifc_placement(f, O, Z, X)

    profile = pipe_elbow.section.ifc_profile
    normal = normal_to_points_in_plane([pipe_elbow.arc_seg.p1, pipe_elbow.arc_seg.p2, pipe_elbow.arc_seg.midpoint])
    revolve_axis = pipe_elbow.arc_seg.center + normal
    revolve_angle = 10

    ifcorigin = f.createIfcCartesianPoint(pipe_elbow.arc_seg.p1.astype(float).tolist())
    ifcaxis1dir = f.createIfcAxis1Placement(ifcorigin, f.createIfcDirection(revolve_axis.astype(float).tolist()))

    ifc_shape = f.createIfcRevolvedAreaSolid(profile, opening_axis_placement, ifcaxis1dir, revolve_angle)

    curve = f.createIfcTrimmedCurve()

    body = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [ifc_shape])
    axis = f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [curve])
    prod_def_shp = f.createIfcProductDefinitionShape(None, None, (axis, body))

    return prod_def_shp
