from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom import Geometry
import ada.geom.solids as so
import ada.geom.surfaces as su
import ada.occ.geom.solids as geo_so
import ada.occ.geom.surfaces as geo_su


def geom_to_occ_geom(geom: Geometry) -> TopoDS_Shape:
    geometry = geom.geometry
    # Solid models
    if isinstance(geometry, so.Box):
        occ_geom = geo_so.make_box_from_geom(geometry)
    elif isinstance(geometry, so.Cone):
        occ_geom = geo_so.make_cone_from_geom(geometry)
    elif isinstance(geometry, so.Cylinder):
        occ_geom = geo_so.make_cylinder_from_geom(geometry)
    elif isinstance(geometry, so.Sphere):
        occ_geom = geo_so.make_sphere_from_geom(geometry)
    elif isinstance(geometry, so.ExtrudedAreaSolid):
        occ_geom = geo_so.make_extruded_area_shape_from_geom(geometry)
    # Surface models
    elif isinstance(geometry, su.FaceBasedSurfaceModel):
        occ_geom = geo_su.make_shell_from_face_based_surface_geom(geometry)
    else:
        raise NotImplementedError(f"Geometry type {type(geometry)} not implemented")

    return occ_geom
