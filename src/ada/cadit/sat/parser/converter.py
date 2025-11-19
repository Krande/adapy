"""
ACIS to ADA Converter

Converts parsed ACIS entities to adapy's internal geometry representations
based on the STEP standard.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import ada.geom.curves as geo_cu
import ada.geom.surfaces as geo_su
from ada import Direction, Point
from ada.cadit.sat.parser.acis_entities import (
    AcisBody,
    AcisCoedge,
    AcisConeSurface,
    AcisCylinderSurface,
    AcisEllipseCurve,
    AcisFace,
    AcisIntcurveCurve,
    AcisLoop,
    AcisLump,
    AcisPlaneSurface,
    AcisPoint,
    AcisShell,
    AcisSphereSurface,
    AcisSplineSurface,
    AcisStraightCurve,
    AcisTorusSurface,
    AcisVertex,
)
from ada.cadit.sat.parser.parser import AcisSatParser
from ada.config import logger
from ada.geom.curve_utils import calculate_multiplicities
from ada.geom.curves import KnotType
from ada.geom.placement import Axis2Placement3D


class AcisToAdaConverter:
    """
    Converter for transforming ACIS entities to adapy geometry representations.

    This converter uses the parsed ACIS entities and transforms them into
    the internal geometry classes used by adapy, which are based on the STEP standard.
    """

    def __init__(self, parser: AcisSatParser):
        """
        Initialize converter with a parsed SAT file.

        Args:
            parser: AcisSatParser instance with parsed entities
        """
        self.parser = parser
        self.entities = parser.entities
        self._converted_cache: Dict[int, any] = {}

    def convert_all_faces(self) -> List[Tuple[str, geo_su.SURFACE_GEOM_TYPES]]:
        """
        Convert all faces in the ACIS model to adapy geometry.

        Returns:
            List of (face_name, geometry) tuples
        """
        results = []
        faces = self.parser.get_faces()

        logger.info(f"Converting {len(faces)} faces from ACIS to adapy geometry")

        for face in faces:
            try:
                name = self._get_face_name(face)
                geometry = self.convert_face(face)
                if geometry:
                    results.append((name, geometry))
            except Exception as e:
                import traceback

                trace = traceback.format_exc()
                logger.warning(f"Failed to convert face {face.index}: {e}")

        return results

    def convert_face(self, face: AcisFace) -> Optional[geo_su.SURFACE_GEOM_TYPES]:
        """
        Convert an ACIS face to adapy surface geometry.

        Args:
            face: AcisFace entity

        Returns:
            AdvancedFace, ClosedShell, or other surface geometry
        """
        # Get the surface geometry
        surface = self.convert_surface(face.surface_ref)
        if not surface:
            return None

        # Get the face bounds (loops)
        bounds = self.convert_face_bounds(face)
        if not bounds:
            logger.warning(f"Face {face.index} has no bounds")
            return None

        # Create AdvancedFace for all surface types
        # AdvancedFace supports Plane, CylindricalSurface, ConicalSurface, SphericalSurface,
        # ToroidalSurface, and BSpline surfaces
        return geo_su.AdvancedFace(bounds=bounds, face_surface=surface, same_sense=True)

    def convert_face_bounds(self, face: AcisFace) -> List[geo_su.FaceBound]:
        """
        Convert face loops to FaceBound objects.

        Args:
            face: AcisFace entity

        Returns:
            List of FaceBound objects
        """
        bounds = []
        visited_loops = set()

        # Start with the first loop
        loop_ref = face.loop_ref
        if not loop_ref:
            return bounds

        # Process all loops in the chain
        while loop_ref and loop_ref not in visited_loops:
            visited_loops.add(loop_ref)
            loop = self.entities.get(loop_ref)
            if not loop:
                break

            # Check if it's an AcisLoop instance
            if not isinstance(loop, AcisLoop):
                logger.warning(f"Expected AcisLoop but got {type(loop)}")
                break

            # Convert the loop to edge list
            edges = self.convert_loop_to_edges(loop)
            if edges:
                edge_loop = geo_cu.EdgeLoop(edges)
                # First loop is outer boundary (orientation=True), rest are holes (orientation=False)
                orientation = len(bounds) == 0
                bounds.append(geo_su.FaceBound(bound=edge_loop, orientation=orientation))

            # Move to next loop in chain
            loop_ref = loop.next_loop_ref if hasattr(loop, "next_loop_ref") else None

        return bounds

    def convert_loop_to_edges(self, loop: AcisLoop) -> List[geo_cu.OrientedEdge]:
        """
        Convert an ACIS loop to a list of oriented edges.

        Args:
            loop: AcisLoop entity

        Returns:
            List of OrientedEdge objects
        """
        edges = []

        # Start with the first coedge
        coedge_ref = loop.coedge_ref
        if not coedge_ref:
            return edges

        first_coedge_ref = coedge_ref
        visited = set()

        # Follow the coedge chain
        while coedge_ref and coedge_ref not in visited:
            visited.add(coedge_ref)
            coedge = self.entities.get(coedge_ref)
            if not coedge:
                break

            # Convert the coedge to an oriented edge
            oriented_edge = self.convert_coedge(coedge)
            if oriented_edge:
                edges.append(oriented_edge)

            # Move to next coedge
            coedge_ref = coedge.next_coedge_ref

            # Check if we've completed the loop
            if coedge_ref == first_coedge_ref:
                break

        return edges

    def convert_coedge(self, coedge: AcisCoedge) -> Optional[geo_cu.OrientedEdge]:
        """
        Convert an ACIS coedge to an OrientedEdge.

        Args:
            coedge: AcisCoedge entity

        Returns:
            OrientedEdge object
        """
        # Get the underlying edge
        edge = self.entities.get(coedge.edge_ref)
        if not edge:
            return None

        # Get the vertices
        start_vertex = self.entities.get(edge.start_vertex_ref)
        end_vertex = self.entities.get(edge.end_vertex_ref)

        if start_vertex is None or end_vertex is None:
            return None

        # Check if vertices are AcisVertex instances
        if not isinstance(start_vertex, AcisVertex) or not isinstance(end_vertex, AcisVertex):
            return None

        # Convert vertices to points
        p1 = self.convert_vertex(start_vertex)
        p2 = self.convert_vertex(end_vertex)

        if p1 is None or p2 is None:
            return None

        # Flip points if coedge is reversed
        if coedge.sense.value == "reversed":
            p1, p2 = p2, p1

        # Get the curve geometry
        curve = self.convert_curve(edge.curve_ref)
        if not curve:
            # Fallback to straight line
            curve = geo_cu.Line(p1, Direction(p2 - p1))

        # Create edge curve
        edge_curve = geo_cu.EdgeCurve(start=p1, end=p2, edge_geometry=curve, same_sense=True)

        # Determine orientation
        orientation = edge.sense.value == "forward"

        return geo_cu.OrientedEdge(start=p1, end=p2, edge_element=edge_curve, orientation=orientation)

    def convert_vertex(self, vertex: AcisVertex) -> Optional[Point]:
        """
        Convert an ACIS vertex to a Point.

        Args:
            vertex: AcisVertex entity

        Returns:
            Point object
        """
        if not vertex.point_ref:
            return None

        point = self.entities.get(vertex.point_ref)
        if not isinstance(point, AcisPoint):
            return None

        return Point(point.x, point.y, point.z)

    def convert_curve(self, curve_ref: Optional[int]) -> Optional[geo_cu.CURVE_GEOM_TYPES]:
        """
        Convert an ACIS curve to adapy curve geometry.

        Args:
            curve_ref: Reference to curve entity

        Returns:
            Curve geometry object (Line, Circle, BSpline, etc.)
        """
        if not curve_ref:
            return None

        curve = self.entities.get(curve_ref)
        if not curve:
            return None

        if isinstance(curve, AcisStraightCurve):
            return self.convert_straight_curve(curve)
        elif isinstance(curve, AcisEllipseCurve):
            return self.convert_ellipse_curve(curve)
        elif isinstance(curve, AcisIntcurveCurve):
            return self.convert_intcurve_curve(curve)
        else:
            logger.warning(f"Unsupported curve type: {curve.entity_type}")
            return None

    def convert_straight_curve(self, curve: AcisStraightCurve) -> geo_cu.Line:
        """Convert ACIS straight curve to Line."""
        pnt = Point(*curve.origin)
        dir = Direction(*curve.direction)
        return geo_cu.Line(pnt, dir)

    def convert_ellipse_curve(self, curve: AcisEllipseCurve) -> geo_cu.Circle | geo_cu.Ellipse:
        """Convert ACIS ellipse curve to Circle or Ellipse."""
        center = Point(*curve.center)
        normal = Direction(*curve.normal).get_normalized()
        major_axis_vec = Direction(*curve.major_axis)

        semi_axis1 = major_axis_vec.get_length()
        semi_axis2 = semi_axis1 * curve.radius_ratio

        ref_direction = major_axis_vec.get_normalized()

        position = Axis2Placement3D(location=center, axis=normal, ref_direction=ref_direction)

        # If radius_ratio is 1.0, it's a circle
        if abs(curve.radius_ratio - 1.0) < 1e-9:
            return geo_cu.Circle(position, semi_axis1)
        else:
            return geo_cu.Ellipse(position, semi_axis1, semi_axis2)

    def convert_intcurve_curve(self, curve: AcisIntcurveCurve) -> Optional[geo_cu.BSplineCurveWithKnots]:
        """Convert ACIS intcurve (B-spline) to BSplineCurveWithKnots."""
        if not curve.spline_data:
            return None

        spline_data = curve.spline_data

        # Validate and convert control points to Point objects
        control_points = []
        for cp in spline_data.control_points:
            # Ensure cp is a list/array with at least 3 elements
            if not isinstance(cp, (list, tuple)) or len(cp) < 3:
                logger.warning(
                    f"Invalid control point format: {cp} for curve idx {curve.index}. Expected list with at least 3 coordinates."
                )
                return None
            control_points.append(Point(cp[0], cp[1], cp[2]))

        if not control_points:
            return None

        # Calculate multiplicities from knots
        # For ACIS format, multiplicities are already provided
        knot_multiplicities = [int(m) for m in spline_data.knot_multiplicities]

        # ACIS Fix: For open B-splines, ACIS may store end multiplicities as `degree`
        # but OCC/STEP requires them to be `degree + 1` for clamped curves.
        # Check if this is an open curve that needs adjustment.
        n_poles = len(control_points)
        degree = spline_data.degree
        sum_mults = sum(knot_multiplicities)
        expected_sum = n_poles + degree + 1

        if sum_mults != expected_sum and len(knot_multiplicities) >= 2:
            # Likely needs adjustment for open curve
            # Increase first and last multiplicity by 1
            logger.debug(
                f"Adjusting ACIS knot multiplicities from {knot_multiplicities} "
                f"(sum={sum_mults}) to match OCC convention (expected sum={expected_sum})"
            )
            knot_multiplicities = knot_multiplicities.copy()
            knot_multiplicities[0] += 1
            knot_multiplicities[-1] += 1
            logger.debug(f"Adjusted multiplicities: {knot_multiplicities} (sum={sum(knot_multiplicities)})")

        # Determine if it's rational
        is_rational = any(isinstance(cp, (list, tuple)) and len(cp) > 3 for cp in spline_data.control_points)

        if is_rational:
            weights = [
                cp[3] if isinstance(cp, (list, tuple)) and len(cp) > 3 else 1.0 for cp in spline_data.control_points
            ]
            return geo_cu.RationalBSplineCurveWithKnots(
                degree=spline_data.degree,
                control_points_list=control_points,
                curve_form=geo_cu.BSplineCurveFormEnum.UNSPECIFIED,
                closed_curve=False,
                self_intersect=False,
                knot_multiplicities=knot_multiplicities,
                knots=spline_data.knots,
                knot_spec=KnotType.UNSPECIFIED,
                weights_data=weights,
            )
        else:
            return geo_cu.BSplineCurveWithKnots(
                degree=spline_data.degree,
                control_points_list=control_points,
                curve_form=geo_cu.BSplineCurveFormEnum.UNSPECIFIED,
                closed_curve=False,
                self_intersect=False,
                knot_multiplicities=knot_multiplicities,
                knots=spline_data.knots,
                knot_spec=KnotType.UNSPECIFIED,
            )

    def convert_surface(self, surface_ref: Optional[int]) -> Optional[geo_su.SURFACE_GEOM_TYPES]:
        """
        Convert an ACIS surface to adapy surface geometry.

        Args:
            surface_ref: Reference to surface entity

        Returns:
            Surface geometry object (Plane, BSplineSurface, etc.)
        """
        if not surface_ref:
            return None

        surface = self.entities.get(surface_ref)
        if not surface:
            return None

        if isinstance(surface, AcisPlaneSurface):
            return self.convert_plane_surface(surface)
        elif isinstance(surface, AcisSplineSurface):
            return self.convert_spline_surface(surface)
        elif isinstance(surface, AcisCylinderSurface):
            return self.convert_cylinder_surface(surface)
        elif isinstance(surface, AcisConeSurface):
            return self.convert_cone_surface(surface)
        elif isinstance(surface, AcisSphereSurface):
            return self.convert_sphere_surface(surface)
        elif isinstance(surface, AcisTorusSurface):
            return self.convert_torus_surface(surface)
        else:
            logger.warning(f"Unsupported surface type: {surface.entity_type}")
            return None

    def convert_plane_surface(self, surface: AcisPlaneSurface) -> geo_su.Plane:
        """Convert ACIS plane surface to Plane."""
        origin = Point(*surface.origin)
        normal = Direction(*surface.normal).get_normalized()
        u_direction = Direction(*surface.u_direction).get_normalized()

        position = Axis2Placement3D(location=origin, axis=normal, ref_direction=u_direction)

        return geo_su.Plane(position=position)

    def convert_spline_surface(
        self, surface: AcisSplineSurface
    ) -> Optional[geo_su.BSplineSurfaceWithKnots | geo_su.RationalBSplineSurfaceWithKnots]:
        """Convert ACIS spline surface to BSplineSurfaceWithKnots."""
        if not surface.spline_data:
            return None

        spline_data = surface.spline_data

        # Convert control points to Point objects
        control_points_list = []
        for u_row in spline_data.control_points:
            row = []
            for v_point in u_row:
                if len(v_point) >= 3:
                    row.append(Point(*v_point[:3]))
            if row:
                control_points_list.append(row)

        if not control_points_list:
            return None

        # Calculate multiplicities
        num_u_points = len(control_points_list)
        num_v_points = len(control_points_list[0]) if control_points_list else 0

        u_mult, v_mult = calculate_multiplicities(
            spline_data.u_degree,
            spline_data.v_degree,
            spline_data.u_knots,
            spline_data.v_knots,
            num_u_points,
            num_v_points,
        )

        # Determine if it's rational
        is_rational = any(len(v_point) > 3 for u_row in spline_data.control_points for v_point in u_row)

        if is_rational:
            # Extract weights
            weights_data = []
            for u_row in spline_data.control_points:
                weights_row = []
                for v_point in u_row:
                    weights_row.append(v_point[3] if len(v_point) > 3 else 1.0)
                weights_data.append(weights_row)

            return geo_su.RationalBSplineSurfaceWithKnots(
                u_degree=spline_data.u_degree,
                v_degree=spline_data.v_degree,
                control_points_list=control_points_list,
                surface_form=geo_su.BSplineSurfaceForm.UNSPECIFIED,
                u_closed=False,
                v_closed=False,
                self_intersect=False,
                u_multiplicities=u_mult,
                v_multiplicities=v_mult,
                u_knots=spline_data.u_knots,
                v_knots=spline_data.v_knots,
                knot_spec=KnotType.UNSPECIFIED,
                weights_data=weights_data,
            )
        else:
            return geo_su.BSplineSurfaceWithKnots(
                u_degree=spline_data.u_degree,
                v_degree=spline_data.v_degree,
                control_points_list=control_points_list,
                surface_form=geo_su.BSplineSurfaceForm.UNSPECIFIED,
                u_closed=False,
                v_closed=False,
                self_intersect=False,
                u_multiplicities=u_mult,
                v_multiplicities=v_mult,
                u_knots=spline_data.u_knots,
                v_knots=spline_data.v_knots,
                knot_spec=KnotType.UNSPECIFIED,
            )

    def convert_cylinder_surface(self, surface: AcisCylinderSurface) -> geo_su.CylindricalSurface:
        """
        Convert ACIS cylinder surface to adapy CylindricalSurface.

        Args:
            surface: AcisCylinderSurface entity

        Returns:
            CylindricalSurface object
        """
        origin = Point(*surface.origin)
        axis = Direction(*surface.axis).get_normalized()
        major_axis = Direction(*surface.major_axis).get_normalized()

        position = Axis2Placement3D(location=origin, axis=axis, ref_direction=major_axis)

        return geo_su.CylindricalSurface(position=position, radius=surface.radius)

    def convert_cone_surface(self, surface: AcisConeSurface) -> geo_su.ConicalSurface:
        """
        Convert ACIS cone surface to adapy ConicalSurface.

        Args:
            surface: AcisConeSurface entity

        Returns:
            ConicalSurface object
        """
        import math

        origin = Point(*surface.origin)
        axis = Direction(*surface.axis).get_normalized()
        major_axis = Direction(*surface.major_axis).get_normalized()

        position = Axis2Placement3D(location=origin, axis=axis, ref_direction=major_axis)

        # Calculate semi-angle from sine and cosine
        # ACIS stores sine_angle and cosine_angle
        semi_angle = math.atan2(surface.sine_angle, surface.cosine_angle)

        # Calculate radius at the apex (base radius)
        # For ACIS, the major_axis length gives the radius at the origin
        radius = Direction(*surface.major_axis).get_length()

        return geo_su.ConicalSurface(position=position, radius=radius, semi_angle=semi_angle)

    def convert_sphere_surface(self, surface: AcisSphereSurface) -> geo_su.SphericalSurface:
        """
        Convert ACIS sphere surface to adapy SphericalSurface.

        Args:
            surface: AcisSphereSurface entity

        Returns:
            SphericalSurface object
        """
        center = Point(*surface.center)
        pole = Direction(*surface.pole).get_normalized()
        equator = Direction(*surface.equator).get_normalized()

        position = Axis2Placement3D(location=center, axis=pole, ref_direction=equator)

        return geo_su.SphericalSurface(position=position, radius=surface.radius)

    def convert_torus_surface(self, surface: AcisTorusSurface) -> geo_su.ToroidalSurface:
        """
        Convert ACIS torus surface to adapy ToroidalSurface.

        Args:
            surface: AcisTorusSurface entity

        Returns:
            ToroidalSurface object
        """
        center = Point(*surface.center)
        axis = Direction(*surface.axis).get_normalized()
        major_axis = Direction(*surface.major_axis).get_normalized()

        position = Axis2Placement3D(location=center, axis=axis, ref_direction=major_axis)

        return geo_su.ToroidalSurface(
            position=position, major_radius=surface.major_radius, minor_radius=surface.minor_radius
        )

    def convert_all_bodies(self) -> List[Tuple[str, List[geo_su.SURFACE_GEOM_TYPES]]]:
        """
        Convert all bodies in the ACIS model to adapy geometry organized by body.

        Returns:
            List of (body_name, [geometries]) tuples
        """
        results = []
        bodies = self.parser.get_bodies()

        logger.info(f"Converting {len(bodies)} bodies from ACIS to adapy geometry")

        for body in bodies:
            try:
                name = self._get_body_name(body)
                geometries = self.convert_body(body)
                if geometries:
                    results.append((name, geometries))
            except Exception as e:
                logger.warning(f"Failed to convert body {body.index}: {e}")

        return results

    def convert_body(self, body: AcisBody) -> List[geo_su.SURFACE_GEOM_TYPES]:
        """
        Convert an ACIS body to a list of adapy geometries.

        Args:
            body: AcisBody entity

        Returns:
            List of surface geometries
        """
        geometries = []

        # Process all lumps in the body
        lump_ref = body.lump_ref
        while lump_ref:
            lump = self.entities.get(lump_ref)
            if not lump or not isinstance(lump, AcisLump):
                break

            # Process all shells in the lump
            shell_ref = lump.shell_ref
            while shell_ref:
                shell = self.entities.get(shell_ref)
                if not shell or not isinstance(shell, AcisShell):
                    break

                # Convert shell to geometry
                shell_geom = self.convert_shell(shell)
                if shell_geom:
                    geometries.append(shell_geom)

                # Move to next shell
                shell_ref = shell.next_shell_ref if hasattr(shell, "next_shell_ref") else None

            # Move to next lump
            lump_ref = lump.next_lump_ref if hasattr(lump, "next_lump_ref") else None

        return geometries

    def convert_shell(self, shell: AcisShell) -> Optional[geo_su.ClosedShell | geo_su.OpenShell]:
        """
        Convert an ACIS shell to adapy ClosedShell or OpenShell.

        Args:
            shell: AcisShell entity

        Returns:
            ClosedShell or OpenShell geometry
        """
        faces = []
        visited_faces = set()

        # Collect all faces in the shell
        face_ref = shell.face_ref
        while face_ref and face_ref not in visited_faces:
            visited_faces.add(face_ref)
            face = self.entities.get(face_ref)
            if not face or not isinstance(face, AcisFace):
                break

            # Convert face
            face_geom = self.convert_face(face)
            if face_geom:
                faces.append(face_geom)

            # Move to next face
            face_ref = face.next_face_ref if hasattr(face, "next_face_ref") else None

        if not faces:
            return None

        # Assume closed shell for now (could check shell properties for open/closed)
        return geo_su.ClosedShell(cfs_faces=faces)

    def _get_face_name(self, face: AcisFace) -> str:
        """
        Get the name of a face from its attributes.

        Args:
            face: AcisFace entity

        Returns:
            Face name string
        """
        # Try to get name from attributes
        attrib_ref = face.attrib_ref
        while attrib_ref:
            attrib = self.entities.get(attrib_ref)
            if not attrib:
                break

            # Check if it's a name attribute
            if hasattr(attrib, "name") and attrib.name:
                return attrib.name

            # Try next attribute in chain
            if hasattr(attrib, "next_attrib_ref"):
                attrib_ref = attrib.next_attrib_ref
            else:
                break

        # Fallback to face index
        return f"face_{face.index}"

    def _get_body_name(self, body: AcisBody) -> str:
        """
        Get the name of a body from its attributes.

        Args:
            body: AcisBody entity

        Returns:
            Body name string
        """
        # Try to get name from attributes (similar to face naming)
        # For now, use body index
        return f"body_{body.index}"
