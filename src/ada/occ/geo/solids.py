from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom.solids import Box, Sphere, Cylinder, Cone, ExtrudedAreaSolid
from ada.occ.geom import make_box, make_sphere, make_cylinder, make_cone


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
