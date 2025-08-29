import math

import OCC.Core.BRepPrimAPI as occBrep
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_RoundCorner, BRepBuilderAPI_Transform
from OCC.Core.BRepOffsetAPI import (
    BRepOffsetAPI_MakePipeShell,
    BRepOffsetAPI_ThruSections,
)
from OCC.Core.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Solid
from OCC.Extend.TopologyUtils import TopologyExplorer

import ada.geom.solids as geo_so
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.occ.geom.curves import make_wire_from_curve
from ada.occ.geom.surfaces import make_profile_from_geom
from ada.occ.utils import transform_shape_to_pos


def make_box_from_geom(box: geo_so.Box) -> TopoDS_Shape:
    axis1 = box.position.axis
    axis2 = box.position.ref_direction
    vec1 = gp_Dir(0, 0, 1) if axis1 is None else gp_Dir(*axis1)
    vec2 = gp_Dir(0, 1, 0) if axis2 is None else gp_Dir(*axis2)

    box_maker = occBrep.BRepPrimAPI_MakeBox(
        gp_Ax2(
            gp_Pnt(*box.position.location),
            vec1,
            vec2,
        ),
        box.x_length,
        box.y_length,
        box.z_length,
    )
    return box_maker.Shape()


def make_sphere_from_geom(sphere: geo_so.Sphere) -> TopoDS_Shape:
    return occBrep.BRepPrimAPI_MakeSphere(gp_Pnt(*sphere.center), sphere.radius).Shape()


def make_cylinder_from_geom(cylinder: geo_so.Cylinder) -> TopoDS_Shape:
    axis = cylinder.position.axis
    vec = gp_Dir(0, 0, 1) if axis is None else gp_Dir(*axis)
    place = gp_Ax2(gp_Pnt(*cylinder.position.location), vec)
    cylinder_maker = occBrep.BRepPrimAPI_MakeCylinder(place, cylinder.radius, cylinder.height)
    return cylinder_maker.Shape()


def make_cone_from_geom(cone: geo_so.Cone) -> TopoDS_Shape:
    axis = cone.position.axis
    vec = gp_Dir(0, 0, 1) if axis is None else gp_Dir(*axis)
    cone_maker = occBrep.BRepPrimAPI_MakeCone(
        gp_Ax2(gp_Pnt(*cone.position.location), vec), cone.bottom_radius, 0, cone.height
    )
    return cone_maker.Shape()


def make_extruded_area_shape_tapered_from_geom(eas: geo_so.ExtrudedAreaSolidTapered):
    o = Point(0, 0, 0)
    z = Direction(0, 0, 1)
    p2 = o + eas.depth * z

    profile1 = make_profile_from_geom(eas.swept_area)
    _profile2 = make_profile_from_geom(eas.end_swept_area)
    profile2 = transform_shape_to_pos(_profile2, p2, z, Direction(1, 0, 0))

    wire1 = list(TopologyExplorer(profile1).wires())[0]
    wire2 = list(TopologyExplorer(profile2).wires())[0]
    ts = BRepOffsetAPI_ThruSections(True)
    ts.AddWire(wire1)
    ts.AddWire(wire2)
    ts.Build()
    shape = ts.Shape()
    return transform_shape_to_pos(shape, eas.position.location, eas.position.axis, eas.position.ref_direction)


def make_extruded_area_shape_from_geom(eas: geo_so.ExtrudedAreaSolid) -> TopoDS_Shape | TopoDS_Solid:
    profile = make_profile_from_geom(eas.swept_area)

    # Build direction is always Z
    vec = Direction(0, 0, 1) * eas.depth
    eas_shape = occBrep.BRepPrimAPI_MakePrism(profile, gp_Vec(*vec)).Shape()

    # Transform to correct position before returning
    return transform_shape_to_pos(eas_shape, eas.position.location, eas.position.axis, eas.position.ref_direction)


def make_revolved_area_shape_from_geom(ras: geo_so.RevolvedAreaSolid) -> TopoDS_Shape | TopoDS_Solid:
    profile = make_profile_from_geom(ras.swept_area)

    # Transform 2d profile to position before revolving the shape
    profile = transform_shape_to_pos(profile, ras.position.location, ras.position.axis, ras.position.ref_direction)

    rev_axis = gp_Ax1(gp_Pnt(*ras.axis.location), gp_Dir(*ras.axis.axis))
    ras_shape = occBrep.BRepPrimAPI_MakeRevol(profile, rev_axis, math.radians(ras.angle)).Shape()

    return ras_shape


def make_fixed_reference_swept_area_shape_from_geom(frs: geo_so.FixedReferenceSweptAreaSolid) -> TopoDS_Solid:
    spine = make_wire_from_curve(frs.directrix)

    profile_face = make_profile_from_geom(frs.swept_area)

    # Extract the outer wire from the profile face
    profile_wire = list(TopologyExplorer(profile_face).wires())[0]

    # Use PipeShell for better handling of 90-degree bends
    pipe_builder = BRepOffsetAPI_MakePipeShell(spine)

    # Set frenet frame algorithm for better orientation around bends
    # BRepBuilderAPI_RoundCorner
    # BRepBuilderAPI_RightCorner
    pipe_builder.SetTransitionMode(BRepBuilderAPI_RoundCorner)

    # Add the wire profile (not the face)
    pipe_builder.Add(profile_wire, True, False)  # with contact and correction

    pipe_builder.Build()
    pipe_builder.MakeSolid()
    swept_solid = pipe_builder.Shape()

    location = frs.position.location.tolist()

    # Then translate to final position
    trsf_to_pos = gp_Trsf()
    trsf_to_pos.SetTranslation(gp_Vec(*location))
    transformed_solid = BRepBuilderAPI_Transform(swept_solid, trsf_to_pos, True, True).Shape()
    return transformed_solid
