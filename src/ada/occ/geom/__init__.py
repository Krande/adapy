from __future__ import annotations

from typing import TYPE_CHECKING

import ada.geom.booleans as bo
import ada.geom.curves as cu
import ada.geom.solids as so
import ada.geom.surfaces as su
from ada.geom import Geometry

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Solid


def geom_to_occ_geom(geom: Geometry) -> TopoDS_Shape | TopoDS_Solid:
    # OCC builders imported lazily so importing this package (e.g. its sibling
    # ada.occ.geom.cache, which routes through active_backend().build) does not
    # require pythonocc. geom_to_occ_geom is OccBackend's builder and naturally
    # needs OCC only when actually called. See the internal design notes Phase 2.
    import ada.occ.geom.solids as geo_so
    import ada.occ.geom.surfaces as geo_su
    from ada.occ.geom.boolean import apply_geom_booleans

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
    elif isinstance(geometry, so.RectangularPyramid):
        occ_geom = geo_so.make_rectangular_pyramid_from_geom(geometry)
    elif isinstance(geometry, so.ExtrudedAreaSolidTapered):
        occ_geom = geo_so.make_extruded_area_shape_tapered_from_geom(geometry)
    elif isinstance(geometry, so.ExtrudedAreaSolid):
        occ_geom = geo_so.make_extruded_area_shape_from_geom(geometry)
    elif isinstance(geometry, so.RevolvedAreaSolid):
        occ_geom = geo_so.make_revolved_area_shape_from_geom(geometry)
    elif isinstance(geometry, so.FixedReferenceSweptAreaSolid):
        occ_geom = geo_so.make_fixed_reference_swept_area_shape_from_geom(geometry)
    elif isinstance(geometry, so.SweptDiskSolid):
        occ_geom = geo_so.make_swept_disk_solid_from_geom(geometry)
    elif isinstance(geometry, so.FacetedBrep):
        occ_geom = geo_so.make_faceted_brep_from_geom(geometry)

    # Surface models
    elif isinstance(geometry, su.FaceBasedSurfaceModel):
        occ_geom = geo_su.make_shell_from_face_based_surface_geom(geometry)
    elif isinstance(geometry, su.CurveBoundedPlane):
        occ_geom = geo_su.make_shell_from_curve_bounded_plane_geom(geometry)
    elif isinstance(geometry, su.AdvancedFace):
        occ_geom = geo_su.make_face_from_geom(geometry)
    elif isinstance(geometry, su.WireFilledFace):
        occ_geom = geo_su.make_face_from_wire_filled(geometry)
    elif isinstance(geometry, su.ClosedShell):
        occ_geom = geo_su.make_closed_shell_from_geom(geometry)
    elif isinstance(geometry, su.OpenShell):
        occ_geom = geo_su.make_open_shell_from_geom(geometry)
    elif isinstance(geometry, su.ShellBasedSurfaceModel):
        occ_geom = geo_su.make_shell_from_shell_based_surface_geom(geometry)
    elif isinstance(geometry, su.ConnectedFaceSet):
        # The native NGEOM reader's B-rep root form (closed/open not recorded in the buffer).
        occ_geom = geo_su.make_shell_from_connected_face_set_geom(geometry)
    elif isinstance(geometry, su.PolygonalFaceSet):
        occ_geom = geo_su.make_shell_from_polygonal_face_set_geom(geometry)
    elif isinstance(geometry, su.TriangulatedFaceSet):
        occ_geom = geo_su.make_shell_from_triangulated_face_set_geom(geometry)

    # Standalone boolean tree (ada.geom.booleans.BooleanResult): recursively build both operands
    # (each may itself be a BooleanResult) and apply the OCC boolean. This is the root-geometry form
    # adacpp/NGEOM uses; the Geometry.bool_operations path (applied below) is the other form.
    elif isinstance(geometry, bo.BooleanResult):
        from OCC.Core.BRepAlgoAPI import (
            BRepAlgoAPI_Common,
            BRepAlgoAPI_Cut,
            BRepAlgoAPI_Fuse,
        )

        from ada.core.guid import create_guid

        first = geom_to_occ_geom(Geometry(create_guid(), geometry.first_operand, None))
        second = geom_to_occ_geom(Geometry(create_guid(), geometry.second_operand, None))
        if geometry.operator == bo.BoolOpEnum.DIFFERENCE:
            occ_geom = BRepAlgoAPI_Cut(first, second).Shape()
        elif geometry.operator == bo.BoolOpEnum.UNION:
            occ_geom = BRepAlgoAPI_Fuse(first, second).Shape()
        elif geometry.operator == bo.BoolOpEnum.INTERSECTION:
            occ_geom = BRepAlgoAPI_Common(first, second).Shape()
        else:
            raise NotImplementedError(f"Boolean operator {geometry.operator} not implemented")

    # Bare curves (no surface): sectionless wire bodies / construction wireframes. Build an OCC
    # wire so the tessellator renders them as glTF line geometry. See ada.geom.curves.
    elif isinstance(geometry, cu.CURVE_GEOM_TUPLE):
        import ada.occ.geom.curves as geo_cu

        occ_geom = geo_cu.make_wire_from_curve(geometry)
    else:
        raise NotImplementedError(f"Geometry to OCC conversion for type {type(geometry)} not implemented")

    # Apply boolean operations
    occ_geom = apply_geom_booleans(occ_geom, geom.bool_operations)

    return occ_geom
