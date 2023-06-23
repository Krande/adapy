from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.surfaces import FaceBasedSurfaceModel, PolyLoop
from ada.occ.geom.curves import (
    make_wire_from_circle,
    make_wire_from_curve,
    make_wire_from_indexed_poly_curve_geom,
    make_wire_from_poly_loop,
)
from ada.occ.utils import transform_shape_to_pos


def make_face_from_poly_loop(poly_loop: PolyLoop) -> TopoDS_Shape:
    wire = make_wire_from_poly_loop(poly_loop)
    return BRepBuilderAPI_MakeFace(wire).Shape()


def make_face_from_indexed_poly_curve_geom(curve: geo_cu.IndexedPolyCurve) -> TopoDS_Shape:
    wire = make_wire_from_indexed_poly_curve_geom(curve)
    return BRepBuilderAPI_MakeFace(wire).Shape()


def make_face_from_circle(circle: geo_cu.Circle):
    circle_wire = make_wire_from_circle(circle)
    return BRepBuilderAPI_MakeFace(circle_wire).Shape()


def make_shell_from_face_based_surface_geom(surface: FaceBasedSurfaceModel) -> TopoDS_Shape:
    occ_face = None
    for face in surface.fbsm_faces:
        for cfs_face in face.cfs_faces:
            if not isinstance(cfs_face.bound, PolyLoop):
                raise NotImplementedError("Only PolyLoop bounds are supported")
            new_face = make_face_from_poly_loop(cfs_face.bound)
            if occ_face is None:
                occ_face = new_face
            else:
                # Fuse the new face with the existing face
                occ_face = BRepAlgoAPI_Fuse(new_face, occ_face).Shape()

    return occ_face


def make_shell_from_curve_bounded_plane_geom(surface: geo_su.CurveBoundedPlane) -> TopoDS_Shape:
    if isinstance(surface.outer_boundary, geo_cu.IndexedPolyCurve):
        outer_curve = surface.outer_boundary
        face = make_face_from_indexed_poly_curve_geom(outer_curve)
        for inner_curve in map(make_face_from_curve, surface.inner_boundaries):
            face = BRepAlgoAPI_Cut(face, inner_curve).Shape()
    else:
        raise NotImplementedError(f"Curve type {type(surface.outer_boundary)} not implemented")

    position = surface.basis_surface.position
    face = transform_shape_to_pos(face, position.location, position.axis, position.ref_direction)

    return face


def make_face_from_curve(outer_curve: geo_cu.CURVE_GEOM_TYPES):
    if isinstance(outer_curve, geo_cu.IndexedPolyCurve):
        return make_face_from_indexed_poly_curve_geom(outer_curve)
    elif isinstance(outer_curve, geo_cu.Circle):
        return make_face_from_circle(outer_curve)
    else:
        raise NotImplementedError("Only IndexedPolyCurve is implemented")


def make_profile_from_geom(area: geo_su.ProfileDef) -> TopoDS_Shape:
    if isinstance(area, geo_su.ArbitraryProfileDef):
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
