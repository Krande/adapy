from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom.solids import Box, Sphere, Cylinder, Cone, ExtrudedAreaSolid
from ada.geom.surfaces import ArbitraryProfileDefWithVoids
from ada.geom.curves import IndexedPolyCurve
from ada.occ.geom.curves import make_indexed_poly_curve_from_geom
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


def make_extruded_area_solid_from_geom(eas: ExtrudedAreaSolid) -> TopoDS_Shape:
    area = eas.swept_area

    if isinstance(area, ArbitraryProfileDefWithVoids):
        outer_curve = area.outer_curve
        if isinstance(outer_curve, IndexedPolyCurve):
            curve = make_indexed_poly_curve_from_geom(outer_curve)

    vec = gp_Dir(0, 0, 1) if axis is None else gp_Dir(*axis)
    extruded_area_solid_maker = BRepPrimAPI_MakePrism(gp_Ax2(gp_Pnt(x, y, z), vec), width, height, depth)
    return extruded_area_solid_maker.Shape()
