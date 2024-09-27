from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
)
from OCC.Core.Geom import Geom_BSplineSurface
from OCC.Core.Geom2d import Geom2d_Line, Geom2d_TrimmedCurve
from OCC.Core.Geom2dAPI import Geom2dAPI_PointsToBSpline
from OCC.Core.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCC.Core.gp import gp_Ax3, gp_Dir, gp_Dir2d, gp_Lin2d, gp_Pln, gp_Pnt, gp_Pnt2d
from OCC.Core.TColgp import TColgp_Array1OfPnt2d, TColgp_Array2OfPnt
from OCC.Core.TColStd import (
    TColStd_Array1OfInteger,
    TColStd_Array1OfReal,
    TColStd_Array2OfReal,
)
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Shell

from ada.config import Config, logger
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.curves import PolyLoop
from ada.geom.surfaces import FaceBasedSurfaceModel
from ada.occ.geom.curves import (
    make_wire_from_circle,
    make_wire_from_curve,
    make_wire_from_edge_loop,
    make_wire_from_indexed_poly_curve_geom,
    make_wire_from_poly_loop,
)
from ada.occ.utils import point3d, transform_shape_to_pos


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


def make_bspline_surface_with_knots(
    advanced_face: geo_su.BSplineSurfaceWithKnots | geo_su.RationalBSplineSurfaceWithKnots,
) -> Geom_BSplineSurface:
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
        logger.debug("Control points:")
        for u in range(1, num_u + 1):
            for v in range(1, num_v + 1):
                logger.debug(
                    control_points.Value(u, v).X(), control_points.Value(u, v).Y(), control_points.Value(u, v).Z()
                )
        logger.debug("Knots in U direction:")
        for i in range(1, num_u_knots + 1):
            logger.debug(knots_u.Value(i))
        logger.debug("Multiplicities in U direction:")
        for i in range(1, num_u_knots + 1):
            logger.debug(multiplicities_u.Value(i))
        logger.debug("Knots in V direction:")
        for i in range(1, num_v_knots + 1):
            logger.debug(knots_v.Value(i))
        logger.debug("Multiplicities in V direction:")
        for i in range(1, num_v_knots + 1):
            logger.debug(multiplicities_v.Value(i))
    if type(advanced_face) is geo_su.RationalBSplineSurfaceWithKnots:
        # Define weights
        weights_data = advanced_face.weights_data
        num_weights = len(weights_data)
        # **Define weights**
        weights = TColStd_Array2OfReal(1, num_u, 1, num_v)
        # Fill weights grid
        for u in range(0, num_u):
            for v in range(0, num_v):
                weight = weights_data[u][v]
                weights.SetValue(u + 1, v + 1, weight)

        if Config().general_debug:
            logger.debug("Weights:")
            for i in range(1, num_weights + 1):
                logger.debug(weights.Value(i))

        # Create the B-Spline surface
        bspline_surface = Geom_BSplineSurface(
            control_points,  # Control points
            weights,  # Weights
            knots_u,  # Knots in U direction
            knots_v,  # Knots in V direction
            multiplicities_u,  # Multiplicities in U direction
            multiplicities_v,  # Multiplicities in V direction
            degree_u,  # Degree in U direction
            degree_v,  # Degree in V direction
            False,  # Is the surface periodic in U direction
            False,  # Is the surface periodic in V direction
        )
    else:
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
            False,  # Is the surface periodic in V direction
        )

    return bspline_surface


def update_edges_4corners(edges, builder, face_surface):
    # Create corresponding 2D curves in the parametric space (u-v space) of the B-Spline surface
    uv1 = gp_Pnt2d(0.0, 0.0)
    uv2 = gp_Pnt2d(0.0, 1.0)
    uv3 = gp_Pnt2d(1.0, 1.0)
    uv4 = gp_Pnt2d(1.0, 0.0)

    c2d_edges = [
        Geom2d_TrimmedCurve(Geom2d_Line(gp_Lin2d(uv1, gp_Dir2d(uv2.XY() - uv1.XY()))), 0.0, 1.0),
        Geom2d_TrimmedCurve(Geom2d_Line(gp_Lin2d(uv2, gp_Dir2d(uv3.XY() - uv2.XY()))), 0.0, 1.0),
        Geom2d_TrimmedCurve(Geom2d_Line(gp_Lin2d(uv3, gp_Dir2d(uv4.XY() - uv3.XY()))), 0.0, 1.0),
        Geom2d_TrimmedCurve(Geom2d_Line(gp_Lin2d(uv4, gp_Dir2d(uv1.XY() - uv4.XY()))), 0.0, 1.0),
    ]

    # Assign the 2D curves to the edges on the B-Spline surface using the correct signature
    identity_location = TopLoc_Location()  # No transformation (identity)
    for i, edge in enumerate(edges):
        builder.UpdateEdge(edge, c2d_edges[i], face_surface, identity_location, 1e-6)


def update_edges_uv_gen(edges, builder, face_surface):
    # Create corresponding 2D curves in the parametric space (u-v space) of the B-Spline surface
    # Generate c2d_edges dynamically
    c2d_edges = []
    identity_location = TopLoc_Location()  # No transformation (identity)
    for edge in edges:
        # Get the 3D curve of the edge
        edge_curve_handle, first, last = BRep_Tool.Curve(edge)
        # Sample points along the edge
        num_samples = 10
        parameters = [first + (last - first) * i / (num_samples - 1) for i in range(num_samples)]
        points_3d = [edge_curve_handle.Value(u) for u in parameters]
        # Project points onto the surface to get (u,v) parameters
        array_2d_points = TColgp_Array1OfPnt2d(1, num_samples)
        for i, pt in enumerate(points_3d):
            projector = GeomAPI_ProjectPointOnSurf(pt, face_surface)
            if projector.NbPoints() == 0:
                raise Exception("Failed to project point onto surface")
            u, v = projector.LowerDistanceParameters()
            array_2d_points.SetValue(i + 1, gp_Pnt2d(u, v))
        # Build a Geom2d_BSplineCurve from the (u,v) points
        interpolator = Geom2dAPI_PointsToBSpline(array_2d_points)
        c2d_edge = interpolator.Curve()
        c2d_edges.append(c2d_edge)
        # Now assign the 2D curve to the edge
        builder.UpdateEdge(edge, c2d_edge, face_surface, identity_location, 1e-6)


def create_wire_from_bounds(bounds, face_surface, builder: BRep_Builder):
    edges = []
    for edge_loop in bounds:
        for para_edge in edge_loop.bound.edge_list:
            occ_edge = BRepBuilderAPI_MakeEdge(point3d(para_edge.start), point3d(para_edge.end)).Edge()
            edges.append(occ_edge)

    update_edges_uv_gen(edges, builder, face_surface)

    # if len(edges) == 4:
    #     update_edges_4corners(edges, builder, face_surface)
    # else:
    #     update_edges_uv_gen(edges, builder, face_surface)

    wire_maker = BRepBuilderAPI_MakeWire()
    for edge in edges:
        wire_maker.Add(edge)

    return wire_maker.Wire()


def make_face_from_geom(advanced_face: geo_su.AdvancedFace) -> TopoDS_Shape:
    if type(advanced_face.face_surface) in (geo_su.BSplineSurfaceWithKnots, geo_su.RationalBSplineSurfaceWithKnots):
        face_surface = make_bspline_surface_with_knots(advanced_face.face_surface)
    else:
        raise NotImplementedError(
            f"Only BSplineSurfaceWithKnots is implemented, not {type(advanced_face.face_surface)}"
        )

    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)

    wire = create_wire_from_bounds(advanced_face.bounds, face_surface, builder)

    face = BRepBuilderAPI_MakeFace(face_surface, wire)
    if not face.IsDone():
        raise Exception("Failed to create face from B-Spline surface")

    # Create a face from the B-Spline surface with the boundary wire
    face = face.Face()

    # Optionally, update the face tolerance
    builder.UpdateFace(face, 1e-6)  # Set tolerance if needed

    # Create the shell manually and add the face
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    builder.Add(shell, face)

    # Set the shell as closed
    shell.Closed(True)

    return shell


def make_plane_from_geom(plane: geo_su.Plane) -> gp_Pln:
    location = plane.position.location
    axis = plane.position.axis
    ref_direction = plane.position.ref_direction

    # Define the origin point of the plane
    origin = gp_Pnt(*location)

    # Define the normal to the plane
    normal = gp_Dir(*axis)

    # Define the reference direction to orient the plane
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object using the origin, normal, and reference direction
    # The gp_Ax3 constructor with an origin, normal direction, and X-direction
    ax3 = gp_Ax3(origin, normal, ref_dir)

    # Create the plane using gp_Pln from the Ax3 object
    return gp_Pln(ax3)


def make_closed_shell_from_geom(shell: geo_su.ClosedShell) -> TopoDS_Shell:
    builder = BRep_Builder()
    occ_shell = TopoDS_Shell()
    builder.MakeShell(occ_shell)

    for cfs_face in shell.cfs_faces:
        if type(cfs_face) is geo_su.FaceSurface:
            face_surface = cfs_face.face_surface
            if type(face_surface) is geo_su.Plane:
                occ_face_surface = make_plane_from_geom(face_surface)
            else:
                raise NotImplementedError(f"Only Plane is implemented, not {type(face_surface)}")
        else:
            raise NotImplementedError(f"Only FaceSurface is implemented, not {type(cfs_face)}")

        wire = make_wire_from_edge_loop(cfs_face.bounds[0].bound)

        face = BRepBuilderAPI_MakeFace(occ_face_surface, wire)
        if not face.IsDone():
            raise Exception("Failed to create face from B-Spline surface")

        # Create a face from the B-Spline surface with the boundary wire
        face = face.Face()

        # Optionally, update the face tolerance
        builder.UpdateFace(face, 1e-6)  # Set tolerance if needed

        # Create the shell manually and add the face
        shell = TopoDS_Shell()
        builder.MakeShell(shell)
        builder.Add(shell, face)

        # Set the shell as closed
        shell.Closed(True)

    return occ_shell


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
