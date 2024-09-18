from OCC.Core.TopoDS import TopoDS_Shape

import ada.geom.solids as so
import ada.geom.surfaces as su
import ada.occ.geom.solids as geo_so
import ada.occ.geom.surfaces as geo_su
from ada.geom import Geometry
from ada.occ.geom.boolean import apply_geom_booleans


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
    elif isinstance(geometry, so.ExtrudedAreaSolidTapered):
        occ_geom = geo_so.make_extruded_area_shape_tapered_from_geom(geometry)
    elif isinstance(geometry, so.ExtrudedAreaSolid):
        occ_geom = geo_so.make_extruded_area_shape_from_geom(geometry)
    elif isinstance(geometry, so.RevolvedAreaSolid):
        occ_geom = geo_so.make_revolved_area_shape_from_geom(geometry)
    elif isinstance(geometry, so.FixedReferenceSweptAreaSolid):
        occ_geom = geo_so.make_fixed_reference_swept_area_shape_from_geom(geometry)

    # Surface models
    elif isinstance(geometry, su.FaceBasedSurfaceModel):
        occ_geom = geo_su.make_shell_from_face_based_surface_geom(geometry)
    elif isinstance(geometry, su.CurveBoundedPlane):
        occ_geom = geo_su.make_shell_from_curve_bounded_plane_geom(geometry)
    elif isinstance(geometry, su.AdvancedFace):
        occ_geom = geo_su.make_face_from_geom(geometry)
    elif isinstance(geometry, su.ClosedShell):
        occ_geom = geo_su.make_closed_shell_from_geom(geometry)
    else:
        raise NotImplementedError(f"Geometry to OCC conversion for type {type(geometry)} not implemented")

    # Apply boolean operations
    occ_geom = apply_geom_booleans(occ_geom, geom.bool_operations)

    return occ_geom
