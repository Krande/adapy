"""Regression gate: the streaming STEP reader must keep covering every canonical
geometry/topology entity type ("no geometry left behind"). If a type is dropped from
the builder registry this fails loudly.
"""

from __future__ import annotations

from ada.cadit.step.read import stream_reader as sr

# Canonical STEP (AP203/214/242) geometry + topology entity types adapy imports natively.
CANONICAL = {
    # points / placement
    "CARTESIAN_POINT", "DIRECTION", "VECTOR", "POINT_ON_CURVE", "POINT_ON_SURFACE",
    "AXIS1_PLACEMENT", "AXIS2_PLACEMENT_2D", "AXIS2_PLACEMENT_3D",
    # curves
    "LINE", "CIRCLE", "ELLIPSE", "PARABOLA", "HYPERBOLA", "POLYLINE",
    "B_SPLINE_CURVE_WITH_KNOTS", "BEZIER_CURVE", "UNIFORM_CURVE", "QUASI_UNIFORM_CURVE",
    "TRIMMED_CURVE", "COMPOSITE_CURVE", "COMPOSITE_CURVE_SEGMENT", "PCURVE",
    "SURFACE_CURVE", "SEAM_CURVE", "INTERSECTION_CURVE", "OFFSET_CURVE_3D", "CURVE_REPLICA",
    # surfaces
    "PLANE", "CYLINDRICAL_SURFACE", "CONICAL_SURFACE", "SPHERICAL_SURFACE", "TOROIDAL_SURFACE",
    "B_SPLINE_SURFACE_WITH_KNOTS", "BEZIER_SURFACE", "UNIFORM_SURFACE", "QUASI_UNIFORM_SURFACE",
    "SURFACE_OF_REVOLUTION", "SURFACE_OF_LINEAR_EXTRUSION", "OFFSET_SURFACE",
    "RECTANGULAR_TRIMMED_SURFACE", "CURVE_BOUNDED_SURFACE", "RECTANGULAR_COMPOSITE_SURFACE",
    "SURFACE_REPLICA",
    # topology
    "VERTEX_POINT", "EDGE_CURVE", "ORIENTED_EDGE", "EDGE_LOOP", "VERTEX_LOOP", "POLY_LOOP",
    "FACE_BOUND", "FACE_OUTER_BOUND", "ADVANCED_FACE", "FACE_SURFACE", "CLOSED_SHELL",
    "OPEN_SHELL", "ORIENTED_CLOSED_SHELL", "CONNECTED_FACE_SET", "SUBFACE", "SUBEDGE",
    # solids / models
    "MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS", "FACETED_BREP", "SHELL_BASED_SURFACE_MODEL",
    "MANIFOLD_SURFACE_SHAPE_REPRESENTATION", "GEOMETRIC_CURVE_SET", "GEOMETRIC_SET",
    "EXTRUDED_AREA_SOLID", "REVOLVED_AREA_SOLID", "CSG_SOLID", "BOOLEAN_RESULT",
    "BLOCK", "RIGHT_CIRCULAR_CYLINDER", "RIGHT_CIRCULAR_CONE", "SPHERE", "TORUS",
    # AP242 tessellated
    "TRIANGULATED_FACE_SET", "TRIANGULATED_SURFACE_SET", "TESSELLATED_SHELL",
    "TESSELLATED_SOLID", "COMPLEX_TRIANGULATED_FACE_SET",
}

# rational variants are reached through the complex-entity path, not a top-level key
_VIA_COMPLEX = {"RATIONAL_B_SPLINE_CURVE", "RATIONAL_B_SPLINE_SURFACE"}


def test_full_canonical_step_geometry_coverage():
    handled = set(sr._BUILDERS) | set(sr._ROOT_BUILDERS) | _VIA_COMPLEX
    missing = sorted(CANONICAL - handled)
    assert not missing, f"STEP reader lost coverage of: {missing}"
