import OCC.Core.BRepPrimAPI as occBrep
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Solid
from OCC.Extend.TopologyUtils import TopologyExplorer

import ada.geom.solids as geo_so
import ada.geom.surfaces as geo_su
import ada.geom.curves as geo_cu
from ada.geom.placement import Direction
from ada.geom.points import Point
from ada.occ.geom.curves import make_wire_from_indexed_poly_curve_geom, make_wire_from_circle
from ada.occ.geom.surfaces import make_face_from_indexed_poly_curve_geom, make_face_from_circle
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


def make_face_from_curve(outer_curve: geo_cu.CURVE_GEOM_TYPES):
    if isinstance(outer_curve, geo_cu.IndexedPolyCurve):
        return make_face_from_indexed_poly_curve_geom(outer_curve)
    elif isinstance(outer_curve, geo_cu.Circle):
        return make_face_from_circle(outer_curve)
    else:
        raise NotImplementedError("Only IndexedPolyCurve is implemented")


def make_wire_from_curve(outer_curve: geo_cu.CURVE_GEOM_TYPES):
    if isinstance(outer_curve, geo_cu.IndexedPolyCurve):
        return make_wire_from_indexed_poly_curve_geom(outer_curve)
    elif isinstance(outer_curve, geo_cu.Circle):
        return make_wire_from_circle(outer_curve)
    else:
        raise NotImplementedError("Only IndexedPolyCurve is implemented")


def make_profile_from_geom(area: geo_su.ProfileDef) -> TopoDS_Shape:
    if isinstance(area, geo_su.ArbitraryProfileDefWithVoids):
        if area.profile_type == geo_su.ProfileType.AREA:
            profile = make_face_from_curve(area.outer_curve)
            for inner_curve in map(make_face_from_curve, area.inner_curves):
                profile = BRepAlgoAPI_Cut(profile, inner_curve).Shape()
        else:
            profile = make_wire_from_curve(area.outer_curve)
            for inner_curve in map(make_wire_from_curve, area.inner_curves):
                profile = BRepAlgoAPI_Cut(profile, inner_curve).Shape()
    else:
        raise NotImplementedError("Only ArbitraryProfileDefWithVoids is implemented")
    return profile


def make_extruded_area_shape_from_geom(eas: geo_so.ExtrudedAreaSolid) -> TopoDS_Shape | TopoDS_Solid:
    profile = make_profile_from_geom(eas.swept_area)

    # Build direction is always Z
    vec = Direction(0, 0, 1) * eas.depth
    eas_shape = occBrep.BRepPrimAPI_MakePrism(profile, gp_Vec(*vec)).Shape()

    # Transform to correct position before returning
    return transform_shape_to_pos(eas_shape, eas.position.location, eas.position.axis, eas.position.ref_direction)
