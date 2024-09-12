from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeShell
from OCC.Core.Geom import Geom_BSplineSurface, Geom_Surface
from OCC.Core.TColStd import TColStd_Array1OfReal, TColStd_Array1OfInteger
from OCC.Core.TColgp import TColgp_Array2OfPnt
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, TopoDS_Shell
from OCC.Core.gp import gp_Pnt

from ada.config import Config
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.curves import PolyLoop
from ada.geom.surfaces import FaceBasedSurfaceModel
from ada.occ.geom.curves import (
    make_wire_from_circle,
    make_wire_from_curve,
    make_wire_from_indexed_poly_curve_geom,
    make_wire_from_poly_loop, make_wire_from_face_bound,
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


def make_bspline_surface_with_knots(advanced_face: geo_su.BSplineSurfaceWithKnots) -> Geom_BSplineSurface:
    # Define control points
    num_u = advanced_face.get_num_u_control_points()
    num_v = advanced_face.get_num_v_control_points()
    control_points = TColgp_Array2OfPnt(1, num_u, 1, num_v)

    # Fill control points grid
    for u in range(0, num_u):
        for v in range(0, num_v):
            val = advanced_face.control_points_list[u][v]
            control_points.SetValue(u + 1, v + 1, gp_Pnt(val.x, val.y, val.z))

    # Set degrees (order = degree + 1)
    degree_u = advanced_face.u_degree
    degree_v = advanced_face.v_degree

    num_u_knots = len(advanced_face.u_knots)
    num_v_knots = len(advanced_face.v_knots)

    # Define knots for U direction
    knots_u = TColStd_Array1OfReal(1, num_u_knots)
    for i, knot in enumerate(advanced_face.u_knots, start=1):
        knots_u.SetValue(i, knot)

    # Define multiplicities for U direction
    multiplicities_u = TColStd_Array1OfInteger(1, num_u_knots)
    for i, mult in enumerate(advanced_face.u_multiplicities, start=1):
        multiplicities_u.SetValue(i, mult)

    # Define knots for V direction
    knots_v = TColStd_Array1OfReal(1, num_v_knots)
    for i, knot in enumerate(advanced_face.v_knots, start=1):
        knots_v.SetValue(i, knot)

    # Define multiplicities for V direction
    multiplicities_v = TColStd_Array1OfInteger(1, num_v_knots)
    for i, mult in enumerate(advanced_face.v_multiplicities, start=1):
        multiplicities_v.SetValue(i, mult)
    if Config().general_debug:
        # print the contents of each array
        print("Control points:")
        for u in range(1, num_u + 1):
            for v in range(1, num_v + 1):
                print(control_points.Value(u, v).X(), control_points.Value(u, v).Y(), control_points.Value(u, v).Z())
        print("Knots in U direction:")
        for i in range(1, num_u_knots + 1):
            print(knots_u.Value(i))
        print("Multiplicities in U direction:")
        for i in range(1, num_u_knots + 1):
            print(multiplicities_u.Value(i))
        print("Knots in V direction:")
        for i in range(1, num_v_knots + 1):
            print(knots_v.Value(i))
        print("Multiplicities in V direction:")
        for i in range(1, num_v_knots + 1):
            print(multiplicities_v.Value(i))

    # Create the B-Spline surface
    bspline_surface = Geom_BSplineSurface(
        control_points,  # Control points
        knots_u,  # Knots in U direction
        knots_v,  # Knots in V direction
        multiplicities_u,  # Multiplicities in U direction
        multiplicities_v,  # Multiplicities in V direction
        degree_u,  # Degree in U direction
        degree_v,  # Degree in V direction
        False,  # Is the surface periodic in U direction
        False  # Is the surface periodic in V direction
    )

    return bspline_surface


def make_advanced_face_from_geom(advanced_face: geo_su.AdvancedFace) -> TopoDS_Shape:
    if type(advanced_face.face_surface) is geo_su.BSplineSurfaceWithKnots:
        face_surface = make_bspline_surface_with_knots(advanced_face.face_surface)
    else:
        raise NotImplementedError("Only BSplineSurfaceWithKnots is implemented")
    wires = []
    for edge_loop in advanced_face.bounds:
        edge_loop_wire = make_wire_from_face_bound(edge_loop)
        wires.append(edge_loop_wire)

    # Wrap the B-Spline surface inside a Geom_Surface handle
    face = BRepBuilderAPI_MakeFace(face_surface, 1e-6)
    if face.IsDone():
        face = face.Face()
        shell = TopoDS_Shell()
        builder = BRep_Builder()
        builder.MakeShell(shell)
        builder.UpdateFace(face)
        builder.Add(shell, face)
        shell_maker = BRepBuilderAPI_MakeShell(face_surface)
        shell_maker.Build()
        shell = shell_maker.Shell()
    else:
        raise Exception("Failed to create face from B-Spline surface")

    return shell


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
