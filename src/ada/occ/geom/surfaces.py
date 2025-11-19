from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_REVERSED
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepTools import BRepTools_WireExplorer
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
)
from OCC.Core.Geom import (
    Geom_BSplineSurface,
    Geom_ConicalSurface,
    Geom_CylindricalSurface,
    Geom_SphericalSurface,
    Geom_ToroidalSurface,
)
from OCC.Core.Geom2d import Geom2d_Line, Geom2d_TrimmedCurve
from OCC.Core.Geom2dAPI import Geom2dAPI_PointsToBSpline
from OCC.Core.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCC.Core.gp import (
    gp_Ax3,
    gp_Cone,
    gp_Cylinder,
    gp_Dir,
    gp_Dir2d,
    gp_Lin2d,
    gp_Pln,
    gp_Pnt,
    gp_Pnt2d,
    gp_Sphere,
    gp_Torus,
)
from OCC.Core.TColgp import TColgp_Array1OfPnt2d, TColgp_Array2OfPnt
from OCC.Core.TColStd import (
    TColStd_Array1OfInteger,
    TColStd_Array1OfReal,
    TColStd_Array2OfReal,
)
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Shape, TopoDS_Shell
from OCC.Core.ShapeFix import ShapeFix_Face

from ada.config import Config, logger
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.curves import PolyLoop
from ada.geom.surfaces import FaceBasedSurfaceModel
from ada.occ.geom.curves import (
    make_wire_from_circle,
    make_wire_from_curve,
    make_wire_from_edge_loop,
    make_wire_from_face_bound,
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
    identity_location = TopLoc_Location()  # No transformation (identity)
    for edge in edges:
        try:
            # Get the 3D curve of the edge
            edge_curve_handle, first, last = BRep_Tool.Curve(edge)
            if edge_curve_handle is None:
                continue

            # Sample points along the edge
            num_samples = 30
            parameters = [first + (last - first) * i / (num_samples - 1) for i in range(num_samples)]
            points_3d = [edge_curve_handle.Value(u) for u in parameters]
            # Project points onto the surface to get (u,v) parameters
            array_2d_points = TColgp_Array1OfPnt2d(1, num_samples)
            for i, pt in enumerate(points_3d):
                projector = GeomAPI_ProjectPointOnSurf(pt, face_surface)
                if projector.NbPoints() == 0:
                    # Try with extended search if default fails, or just log/raise
                    # For now, raise to be caught by outer loop/logger
                    raise Exception("Failed to project point onto surface")
                u, v = projector.LowerDistanceParameters()
                array_2d_points.SetValue(i + 1, gp_Pnt2d(u, v))
            # Build a Geom2d_BSplineCurve from the (u,v) points
            # 3rd param is Approx_ChordLength (1) or Approx_Centripetal (2) or Approx_IsoParametric (3)
            # We use default (Approx_ChordLength) which is usually fine for ordered points
            interpolator = Geom2dAPI_PointsToBSpline(array_2d_points)
            if not interpolator.IsDone():
                logger.warning(f"Failed to create 2D BSpline for edge {edge}")
                continue
            
            c2d_edge = interpolator.Curve()
            # Now assign the 2D curve to the edge
            builder.UpdateEdge(edge, c2d_edge, face_surface, identity_location, 1e-6)
        except Exception as ex:
             logger.warning(f"Error updating edge p-curve: {ex}")


def is_wire_cw(wire, face_surface):
    # Calculate signed area of the polygon formed by edge endpoints in UV space
    area = 0.0
    exp = BRepTools_WireExplorer(wire, face_surface) if isinstance(face_surface, TopoDS_Face) else BRepTools_WireExplorer(wire)
    # WireExplorer iterates edges in wire order
    while exp.More():
        edge = exp.Current()
        # Get p-curve
        curve, first, last = BRep_Tool.CurveOnSurface(edge, face_surface, TopLoc_Location())
        if curve:
            # Note: WireExplorer handles orientation. If edge is REVERSED in wire, 
            # it still returns the edge with REVERSED orientation.
            # However, we need UV coords following the LOOP direction.
            
            p1 = curve.Value(first)
            p2 = curve.Value(last)
            
            # Handle orientation
            if edge.Orientation() == TopAbs_REVERSED:
                # Wire goes Last -> First
                uv_start = p2
                uv_end = p1
            else:
                # Wire goes First -> Last
                uv_start = p1
                uv_end = p2
                
            # Integration term (Shoelace formula)
            # Sum (x2 - x1) * (y2 + y1)
            area += (uv_end.X() - uv_start.X()) * (uv_end.Y() + uv_start.Y())
            
        exp.Next()
        
    # Area > 0 means CW (for standard UV coords where U=Right, V=Up)
    # Area < 0 means CCW
    logger.warning(f"Wire signed area: {area}")
    return area > 0


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


def make_face_from_geom(advanced_face: geo_su.AdvancedFace) -> TopoDS_Face:
    """Create an OCC face from an AdvancedFace with arbitrary supported surface types and bounds.

    Supports Plane, CylindricalSurface, ConicalSurface, SphericalSurface, ToroidalSurface,
    BSplineSurfaceWithKnots and RationalBSplineSurfaceWithKnots.
    """
    # Build the OCC surface from the adapy face surface
    face_surface = make_surface_from_geom(advanced_face.face_surface)

    # Build outer and (optional) inner wires from the face bounds
    if not advanced_face.bounds:
        raise ValueError("AdvancedFace must have at least one bound")

    outer_wire = make_wire_from_face_bound(advanced_face.bounds[0])

    if isinstance(face_surface, Geom_BSplineSurface):
         builder = BRep_Builder()
         # Extract edges from the wire and update them with p-curves
         wire_edges = []
         exp = TopExp_Explorer(outer_wire, TopAbs_EDGE)
         while exp.More():
             wire_edges.append(exp.Current())
             exp.Next()
         update_edges_uv_gen(wire_edges, builder, face_surface)
         
         if is_wire_cw(outer_wire, face_surface):
             logger.info("Reversing CW wire to CCW for B-Spline surface")
             outer_wire = outer_wire.Reversed()

    face_maker = BRepBuilderAPI_MakeFace(face_surface, outer_wire)

    # Add inner wires (holes) if present
    if len(advanced_face.bounds) > 1:
        for inner_fb in advanced_face.bounds[1:]:
            try:
                inner_wire = make_wire_from_face_bound(inner_fb)
                if isinstance(face_surface, Geom_BSplineSurface):
                     # Extract edges from the inner wire and update them with p-curves
                     wire_edges = []
                     exp = TopExp_Explorer(inner_wire, TopAbs_EDGE)
                     while exp.More():
                         wire_edges.append(exp.Current())
                         exp.Next()
                     update_edges_uv_gen(wire_edges, builder, face_surface)
                face_maker.Add(inner_wire)
            except Exception as ex:
                logger.warning(f"Skipping inner bound due to error creating wire: {ex}")

    if not face_maker.IsDone():
        raise Exception(f"Failed to create face from surface type {type(advanced_face.face_surface)}")

    face = face_maker.Face()

    # Fix the face (p-curves, orientation, etc.) using ShapeFix
    if isinstance(face_surface, Geom_BSplineSurface):
        fixer = ShapeFix_Face(face)
        fixer.Perform()
        # Explicitly run wire fixes
        wire_fixer = fixer.FixWireTool()
        wire_fixer.FixConnected()
        wire_fixer.FixClosed()
        
        face = fixer.Face()

    # Update the face tolerance
    builder = BRep_Builder()
    builder.UpdateFace(face, 1e-3)

    return face


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


def make_cylindrical_surface_from_geom(cylinder: geo_su.CylindricalSurface) -> Geom_CylindricalSurface:
    location = cylinder.position.location
    axis = cylinder.position.axis
    ref_direction = cylinder.position.ref_direction

    # Define the origin point of the cylinder
    origin = gp_Pnt(*location)

    # Define the axis direction
    axis_dir = gp_Dir(*axis)

    # Define the reference direction
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object
    ax3 = gp_Ax3(origin, axis_dir, ref_dir)

    # Create the cylindrical surface
    return Geom_CylindricalSurface(gp_Cylinder(ax3, cylinder.radius))


def make_conical_surface_from_geom(cone: geo_su.ConicalSurface) -> Geom_ConicalSurface:
    location = cone.position.location
    axis = cone.position.axis
    ref_direction = cone.position.ref_direction

    # Define the origin point of the cone
    origin = gp_Pnt(*location)

    # Define the axis direction
    axis_dir = gp_Dir(*axis)

    # Define the reference direction
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object
    ax3 = gp_Ax3(origin, axis_dir, ref_dir)

    # Create the conical surface
    return Geom_ConicalSurface(gp_Cone(ax3, cone.semi_angle, cone.radius))


def make_spherical_surface_from_geom(sphere: geo_su.SphericalSurface) -> Geom_SphericalSurface:
    location = sphere.position.location
    axis = sphere.position.axis
    ref_direction = sphere.position.ref_direction

    # Define the origin point of the sphere
    origin = gp_Pnt(*location)

    # Define the axis direction
    axis_dir = gp_Dir(*axis)

    # Define the reference direction
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object
    ax3 = gp_Ax3(origin, axis_dir, ref_dir)

    # Create the spherical surface
    return Geom_SphericalSurface(gp_Sphere(ax3, sphere.radius))


def make_toroidal_surface_from_geom(torus: geo_su.ToroidalSurface) -> Geom_ToroidalSurface:
    location = torus.position.location
    axis = torus.position.axis
    ref_direction = torus.position.ref_direction

    # Define the origin point of the torus
    origin = gp_Pnt(*location)

    # Define the axis direction
    axis_dir = gp_Dir(*axis)

    # Define the reference direction
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object
    ax3 = gp_Ax3(origin, axis_dir, ref_dir)

    # Create the toroidal surface
    return Geom_ToroidalSurface(gp_Torus(ax3, torus.major_radius, torus.minor_radius))


def make_surface_from_geom(face_surface):
    """
    Create an OCC surface from an adapy surface geometry definition.

    Args:
        face_surface: Any supported adapy surface type

    Returns:
        OCC surface object (gp_Pln, Geom_CylindricalSurface, etc.)
    """
    if type(face_surface) is geo_su.Plane:
        return make_plane_from_geom(face_surface)
    elif type(face_surface) is geo_su.CylindricalSurface:
        return make_cylindrical_surface_from_geom(face_surface)
    elif type(face_surface) is geo_su.ConicalSurface:
        return make_conical_surface_from_geom(face_surface)
    elif type(face_surface) is geo_su.SphericalSurface:
        return make_spherical_surface_from_geom(face_surface)
    elif type(face_surface) is geo_su.ToroidalSurface:
        return make_toroidal_surface_from_geom(face_surface)
    elif type(face_surface) in (geo_su.BSplineSurfaceWithKnots, geo_su.RationalBSplineSurfaceWithKnots):
        return make_bspline_surface_with_knots(face_surface)
    else:
        raise NotImplementedError(f"Surface type {type(face_surface)} is not implemented")


def make_closed_shell_from_geom(shell: geo_su.ClosedShell) -> TopoDS_Shell:
    builder = BRep_Builder()
    occ_shell = TopoDS_Shell()
    builder.MakeShell(occ_shell)

    for cfs_face in shell.cfs_faces:
        # Handle AdvancedFace
        if type(cfs_face) is geo_su.AdvancedFace:
            try:
                # Create the surface from the face_surface
                occ_face_surface = make_surface_from_geom(cfs_face.face_surface)

                # Create wire from the face bounds (use first bound as outer)
                if len(cfs_face.bounds) > 0:
                    wire = make_wire_from_edge_loop(cfs_face.bounds[0].bound)
                else:
                    logger.warning("AdvancedFace without bounds encountered; skipping face")
                    continue

                # Create the face
                face_maker = BRepBuilderAPI_MakeFace(occ_face_surface, wire)
                if not face_maker.IsDone():
                    logger.warning(
                        f"Failed to create face from surface type {type(cfs_face.face_surface)}; skipping face"
                    )
                    continue

                face = face_maker.Face()

                # Update the face tolerance
                builder.UpdateFace(face, 1e-6)

                # Add the face to the shell
                builder.Add(occ_shell, face)
            except Exception as ex:
                logger.warning(f"Skipping AdvancedFace due to error during wire/face creation: {ex}")
                continue

        # Handle FaceSurface (legacy support)
        elif type(cfs_face) is geo_su.FaceSurface:
            face_surface = cfs_face.face_surface
            if type(face_surface) is geo_su.Plane:
                occ_face_surface = make_plane_from_geom(face_surface)
            else:
                raise NotImplementedError(f"Only Plane is implemented for FaceSurface, not {type(face_surface)}")

            try:
                wire = make_wire_from_edge_loop(cfs_face.bounds[0].bound)

                face_maker = BRepBuilderAPI_MakeFace(occ_face_surface, wire)
                if not face_maker.IsDone():
                    logger.warning("Failed to create face from surface; skipping face")
                    continue

                face = face_maker.Face()

                # Update the face tolerance
                builder.UpdateFace(face, 1e-6)

                # Add the face to the shell
                builder.Add(occ_shell, face)
            except Exception as ex:
                logger.warning(f"Skipping FaceSurface due to error during wire/face creation: {ex}")
                continue
        else:
            raise NotImplementedError(
                f"Face type {type(cfs_face)} is not implemented (supported: AdvancedFace, FaceSurface)"
            )

    # Set the shell as closed
    occ_shell.Closed(True)

    return occ_shell


def make_face_from_curve(outer_curve: geo_cu.CURVE_GEOM_TYPES):
    if isinstance(outer_curve, geo_cu.IndexedPolyCurve):
        return make_face_from_indexed_poly_curve_geom(outer_curve)
    elif isinstance(outer_curve, geo_cu.Circle):
        return make_face_from_circle(outer_curve)
    else:
        raise NotImplementedError("Only IndexedPolyCurve is implemented")


def make_profile_from_geom(area: geo_su.ProfileDef) -> TopoDS_Shape | TopoDS_Face:
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
