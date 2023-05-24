from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Solid
from OCC.Core.gp import gp_Vec, gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir

from ada.core.vector_utils import angle_between
from ada.geom.curves import IndexedPolyCurve
from ada.geom.placement import Direction
from ada.geom.points import Point
from ada.geom.solids import Box, Sphere, Cylinder, Cone, ExtrudedAreaSolid
from ada.geom.surfaces import ArbitraryProfileDefWithVoids, ProfileType
from ada.occ.geom.curves import make_indexed_poly_curve_from_geom
from ada.occ.geom.surfaces import make_indexed_poly_curve_surface_from_geom
from ada.occ.primitives import make_box, make_sphere, make_cylinder, make_cone


def make_box_from_geom(box: Box) -> TopoDS_Shape:
    v1 = box.position.axis
    v2 = box.position.ref_direction
    return make_box(*box.position.location, box.x_length, box.y_length, box.z_length, v1, v2)


def make_sphere_from_geom(sphere: Sphere) -> TopoDS_Shape:
    return make_sphere(*sphere.center, sphere.radius)


def make_cylinder_from_geom(cylinder: Cylinder) -> TopoDS_Shape:
    return make_cylinder(
        *cylinder.position.location, radius=cylinder.radius, height=cylinder.height, axis=cylinder.position.axis
    )


def make_cone_from_geom(cone: Cone) -> TopoDS_Shape:
    return make_cone(*cone.position.location, r1=cone.bottom_radius, height=cone.height, r2=0, axis=cone.position.axis)


def make_extruded_area_solid_from_geom(eas: ExtrudedAreaSolid) -> TopoDS_Shape | TopoDS_Solid:
    area = eas.swept_area

    if isinstance(area, ArbitraryProfileDefWithVoids):
        outer_curve = area.outer_curve
        if isinstance(outer_curve, IndexedPolyCurve):
            if area.profile_type == ProfileType.AREA:
                profile = make_indexed_poly_curve_surface_from_geom(outer_curve)
            else:  # area.profile_type == ProfileType.CURVE:
                profile = make_indexed_poly_curve_from_geom(outer_curve)
        else:
            raise NotImplemented("Only IndexedPolyCurve is implemented")
    else:
        raise NotImplemented("Only ArbitraryProfileDefWithVoids is implemented")

    # Build direction is always Z
    vec = Direction(0, 0, 1) * eas.depth

    eas_shape = BRepPrimAPI_MakePrism(profile, gp_Vec(*vec)).Shape()

    # Create a transformation to move the extruded area solid to the correct position
    trsf = gp_Trsf()

    # Rotate the extruded area solid around 0,0,0
    ref_dir = gp_Dir(*eas.position.ref_direction)
    pdir = eas.position.get_pdir()
    revolve_axis = gp_Ax1(gp_Pnt(*Point(0, 0, 0)), ref_dir)
    angle = angle_between(pdir, eas.position.axis)
    trsf.SetRotation(revolve_axis, angle)
    shape1 = BRepBuilderAPI_Transform(eas_shape, trsf, True).Shape()

    # Translate the extruded area solid
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(*eas.position.location))

    return BRepBuilderAPI_Transform(shape1, trsf, True).Shape()
