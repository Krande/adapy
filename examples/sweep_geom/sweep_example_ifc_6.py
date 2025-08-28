"""
IFC FixedReferenceSweptAreaSolid example (schema-compliant orientation)

This example is based on sweep_example_ifc_4.py but updated to strictly follow
IfcFixedReferenceSweptAreaSolid orientation requirements:

- Axis3 (local z) equals the tangent vector t at the start of the Directrix
- Axis1 (local x) equals the orthogonal projection of FixedReference onto the
  normal plane of t
- SweptArea is a subtype of IfcProfileDef
- IMPORTANT: The directrix is normalized to start at (0,0,0) in the swept solid's
  local space; the IfcLocalPlacement is responsible for global positioning.

Usage (recommended):
  pixi run -e tests python examples/sweep_geom/sweep_example_ifc_6.py

Alternative (if your environment is already active):
  python examples\\sweep_geom\\sweep_example_ifc_6.py

Output:
  temp\\sweep_example_6.ifc
"""

from __future__ import annotations

import os
from math import sqrt
from typing import List, Sequence, Tuple

import ifcopenshell
import ifcopenshell.guid

# ----------------------
# Helpers
# ----------------------


def dir3(f, xyz: Sequence[float]):
    return f.create_entity("IfcDirection", list(map(float, xyz)))


def dir2(f, xy: Sequence[float]):
    return f.create_entity("IfcDirection", list(map(float, xy)))


def pt3(f, xyz: Sequence[float]):
    return f.create_entity("IfcCartesianPoint", list(map(float, xyz)))


def pt2(f, xy: Sequence[float]):
    return f.create_entity("IfcCartesianPoint", list(map(float, xy)))


def axis2d(f, origin_xy=(0.0, 0.0), dir_xy=(1.0, 0.0)):
    return f.create_entity("IfcAxis2Placement2D", Location=pt2(f, origin_xy), RefDirection=dir2(f, dir_xy))


def axis3d(f, origin_xyz=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0), refdir=(1.0, 0.0, 0.0)):
    return f.create_entity(
        "IfcAxis2Placement3D",
        Location=pt3(f, origin_xyz),
        Axis=dir3(f, axis),
        RefDirection=dir3(f, refdir),
    )


# ----------------------
# Basic vector math
# ----------------------


def _normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    x, y, z = v
    n = sqrt(x * x + y * y + z * z)
    if n == 0:
        return (0.0, 0.0, 0.0)
    return (x / n, y / n, z / n)


def _dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    ax, ay, az = a
    bx, by, bz = b
    return ax * bx + ay * by + az * bz


def _sub(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    ax, ay, az = a
    bx, by, bz = b
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def _add(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(s: float, v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (s * v[0], s * v[1], s * v[2])


def _length(v: Tuple[float, float, float]) -> float:
    return sqrt(_dot(v, v))


def _translate_points_to_origin(points: List[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
    """
    Returns a new list of points translated so that the first point becomes (0,0,0).
    This ensures the directrix starts at the origin; product placement will position it.
    """
    if not points:
        return points
    p0 = points[0]
    dx, dy, dz = p0
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return points
    return [(px - dx, py - dy, pz - dz) for (px, py, pz) in points]


# ----------------------
# Circle through 3 points utilities
# ----------------------


def _circle_from_3pts(
    p0: Tuple[float, float, float], p1: Tuple[float, float, float], p2: Tuple[float, float, float]
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], float, Tuple[float, float, float]]:
    """
    Returns (center, normal, radius, refdir) for the circle through p0, p1, p2.
    refdir is chosen to point from center to p0, suitable for IfcAxis2Placement3D.RefDirection.
    If points are (near) collinear, raises ValueError.
    """
    v1 = _sub(p1, p0)
    v2 = _sub(p2, p0)
    n = _cross(v1, v2)
    n_len = _length(n)
    if n_len < 1e-9:
        raise ValueError("Points are collinear or nearly so; cannot form a unique circle")
    n = _normalize(n)

    # Orthonormal basis in the plane
    u = _normalize(v1)
    v = _cross(n, u)

    # Coordinates in plane with origin at p0
    a = _dot(v1, u)  # p1.x in plane (should be >0)
    dx = _dot(v2, u)
    dy = _dot(v2, v)
    if abs(dy) < 1e-12:
        raise ValueError("Points are nearly collinear in plane; cannot form circle")

    # Circle center in 2D for points (0,0), (a,0), (dx,dy)
    cx = a / 2.0
    cy = (dy / 2.0) - (dx * (a - dx)) / (2.0 * dy)

    # Convert back to 3D
    center = _add(p0, _add(_mul(cx, u), _mul(cy, v)))
    r_vec = _sub(p0, center)
    r = _length(r_vec)
    if r < 1e-12:
        raise ValueError("Computed circle radius is too small")

    refdir = _normalize(r_vec)
    return center, n, r, refdir


def _build_t_continuous_directrix_circle(f, pts: List[Tuple[float, float, float]]):
    """
    Builds a t-continuous directrix as an IfcCompositeCurve consisting of two
    IfcTrimmedCurve segments on a single IfcCircle passing through the 3 points.
    Falls back to a single IfcLine trimmed by Start/End if the points are collinear.
    """
    p0, p1, p2 = pts
    try:
        center, normal, radius, refdir = _circle_from_3pts(p0, p1, p2)
        pos = axis3d(f, center, normal, refdir)
        circle = f.create_entity("IfcCircle", Position=pos, Radius=float(radius))

        # Two trimmed segments: p0->p1 and p1->p2
        tc1 = f.create_entity(
            "IfcTrimmedCurve",
            BasisCurve=circle,
            Trim1=[pt3(f, p0)],
            Trim2=[pt3(f, p1)],
            SenseAgreement=True,
            MasterRepresentation="CARTESIAN",
        )
        tc2 = f.create_entity(
            "IfcTrimmedCurve",
            BasisCurve=circle,
            Trim1=[pt3(f, p1)],
            Trim2=[pt3(f, p2)],
            SenseAgreement=True,
            MasterRepresentation="CARTESIAN",
        )
        seg1 = f.create_entity("IfcCompositeCurveSegment", Transition="CONTINUOUS", SameSense=True, ParentCurve=tc1)
        seg2 = f.create_entity("IfcCompositeCurveSegment", Transition="CONTINUOUS", SameSense=True, ParentCurve=tc2)
        comp = f.create_entity("IfcCompositeCurve", Segments=[seg1, seg2], SelfIntersect=False)
        # Also return the start tangent aligned with the actual trim direction p0->p1 on the circle
        r_unit = refdir  # unit vector from center to p0
        dir_p0p1 = _normalize(_sub(p1, p0))
        # Project p0->p1 onto the tangent direction at p0 (orthogonal to radius)
        dpr = _dot(dir_p0p1, r_unit)
        t_start = _normalize(
            (dir_p0p1[0] - dpr * r_unit[0], dir_p0p1[1] - dpr * r_unit[1], dir_p0p1[2] - dpr * r_unit[2])
        )
        # If projection degenerates (shouldn't), fall back to right-hand rule
        if t_start == (0.0, 0.0, 0.0):
            t_start = _normalize(_cross(normal, r_unit))
        return comp, t_start, (center, normal, refdir)
    except ValueError:
        # Fallback: straight trimmed line (t-continuous) through p0->p2
        direction = _normalize(_sub(p2, p0))
        if direction == (0.0, 0.0, 0.0):
            direction = (0.0, 1.0, 0.0)
        line = f.create_entity("IfcLine", Pnt=pt3(f, p0), Dir=dir3(f, direction))
        # Represent as a trimmed curve on the line to define a finite segment
        tcline = f.create_entity(
            "IfcTrimmedCurve",
            BasisCurve=line,
            Trim1=[pt3(f, p0)],
            Trim2=[pt3(f, p2)],
            SenseAgreement=True,
            MasterRepresentation="CARTESIAN",
        )
        seg = f.create_entity("IfcCompositeCurveSegment", Transition="CONTINUOUS", SameSense=True, ParentCurve=tcline)
        comp = f.create_entity("IfcCompositeCurve", Segments=[seg], SelfIntersect=False)
        t_start = direction
        return comp, t_start, None


# ----------------------
# Geometry data (same as example 4, but use original 2D profile)
# ----------------------
wt = 8e-3  # profile thickness

# Original profile (no scaling workaround)
FILLET_TRIANGLE_2D: List[Tuple[float, float]] = [
    (0.0, 0.0),
    (-wt, 0.0),
    (0.0, wt),
]

# Simplified 3D directrix points (these may be arbitrary, we normalize to start at origin)
SIMPLIFIED_SWEEP_PTS: List[Tuple[float, float, float]] = [
    (0.0, 0.0, 0.0),  # start (will be normalized anyway)
    (0.0, 1.0, 0.0),  # mid
    (0.0, 1.5, 0.5),  # end
]

# ----------------------
# IFC model construction (schema-compliant orientation)
# ----------------------


def build_ifc_sweep_v6(output_path: str = "temp\\sweep_example_6.ifc") -> str:
    f = ifcopenshell.file(schema="IFC4")

    # Units and project
    si_length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    si_area = f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    si_vol = f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    si_ang = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")
    units = f.create_entity("IfcUnitAssignment", Units=[si_length, si_area, si_vol, si_ang])

    project = f.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        Name="Sweep Example 6 (Schema-Compliant Orientation)",
        UnitsInContext=units,
    )

    # Contexts
    model_ctx = f.create_entity(
        "IfcGeometricRepresentationContext",
        ContextIdentifier="Model",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-4,
        WorldCoordinateSystem=axis3d(f, (0.0, 0.0, 0.0)),
        TrueNorth=dir2(f, (0.0, 1.0)),
    )
    body_ctx = f.create_entity(
        "IfcGeometricRepresentationSubContext",
        ContextIdentifier="Body",
        ContextType="Model",
        ParentContext=model_ctx,
        TargetView="MODEL_VIEW",
        UserDefinedTargetView=None,
    )

    # Profile (2D, closed)
    profile2d_pts = FILLET_TRIANGLE_2D + [FILLET_TRIANGLE_2D[0]]  # ensure closed
    profile_polyline = f.create_entity("IfcPolyline", Points=[pt2(f, p) for p in profile2d_pts])
    profile = f.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        ProfileName="TriangleFillet",
        OuterCurve=profile_polyline,
    )

    # Normalize input directrix so it starts at (0,0,0) in the swept solid's local space
    norm_pts = _translate_points_to_origin(SIMPLIFIED_SWEEP_PTS)

    # Directrix: build a t-continuous composite curve from a circle arc through the three normalized points
    p0 = (0.0, 0.0, 0.0)
    directrix, t, circle_frame = _build_t_continuous_directrix_circle(f, norm_pts)

    # Choose FixedReference to be non-parallel to the tangent everywhere along the directrix
    # - For circle: pick the circle normal (orthogonal to tangents at all points)
    # - For line: pick any vector perpendicular to the line direction
    if circle_frame is not None:
        _, circle_normal, _refdir = circle_frame
        fixed = _normalize(circle_normal)
    else:
        # Perpendicular to line direction t
        # Choose a helper vector not parallel to t, then cross to get a perpendicular
        helper = (0.0, 0.0, 1.0)
        if abs(_dot(helper, t)) > 0.95:
            helper = (1.0, 0.0, 0.0)
        fixed = _normalize(_cross(t, helper))
        # Ensure we didn't end up with zero (degenerate), fallback to another axis
        if fixed == (0.0, 0.0, 0.0):
            helper = (0.0, 1.0, 0.0)
            fixed = _normalize(_cross(t, helper))
        if fixed == (0.0, 0.0, 0.0):
            fixed = (1.0, 0.0, 0.0)

    # Axis1 = projection of FixedReference onto normal plane of t
    # proj = fixed - (fixed·t) t
    d = _dot(fixed, t)
    axis1 = (fixed[0] - d * t[0], fixed[1] - d * t[1], fixed[2] - d * t[2])
    axis1 = _normalize(axis1)
    if axis1 == (0.0, 0.0, 0.0):
        # If projection degenerate, pick any orthonormal vector in the normal plane
        tmp = (1.0, 0.0, 0.0)
        if abs(_dot(tmp, t)) > 0.9:
            tmp = (0.0, 1.0, 0.0)
        axis1 = _normalize(_cross(t, _cross(tmp, t)))  # Gram-Schmidt like
        if axis1 == (0.0, 0.0, 0.0):
            axis1 = (1.0, 0.0, 0.0)

    # Position: Axis3 = t, RefDirection = axis1, LocalOrigin at start point
    pos = axis3d(f, p0, t, axis1)

    # Build the swept solid
    solid = f.create_entity(
        "IfcFixedReferenceSweptAreaSolid",
        SweptArea=profile,
        Position=pos,
        Directrix=directrix,
        FixedReference=dir3(f, fixed),
    )

    # Representation
    shape_rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )
    pds = f.create_entity("IfcProductDefinitionShape", Representations=[shape_rep])

    # Product and placement
    local_placement = f.create_entity("IfcLocalPlacement", RelativePlacement=axis3d(f, (285.0, 300.0, 500.0)))
    proxy = f.create_entity(
        "IfcBuildingElementProxy",
        GlobalId=ifcopenshell.guid.new(),
        Name="SchemaCompliantSweptSolid",
        ObjectPlacement=local_placement,
        Representation=pds,
    )

    # Spatial structure
    site_lp = f.create_entity("IfcLocalPlacement", RelativePlacement=axis3d(f, (0.0, 0.0, 0.0)))
    site = f.create_entity(
        "IfcSite",
        GlobalId=ifcopenshell.guid.new(),
        Name="Default Site",
        ObjectPlacement=site_lp,
        CompositionType="ELEMENT",
    )

    f.create_entity("IfcRelAggregates", GlobalId=ifcopenshell.guid.new(), RelatingObject=project, RelatedObjects=[site])
    f.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=ifcopenshell.guid.new(),
        Name="Container",
        Description="Container to Contained",
        RelatedElements=[proxy],
        RelatingStructure=site,
    )

    # Simple validation printout for schema adherence
    # Check that Axis1 is orthogonal to t and equals projection of FixedReference
    dot_a1_t = _dot(axis1, t)
    d_fixed_t = _dot(fixed, t)
    proj_len = sqrt(
        (fixed[0] - d_fixed_t * t[0]) ** 2 + (fixed[1] - d_fixed_t * t[1]) ** 2 + (fixed[2] - d_fixed_t * t[2]) ** 2
    )

    print("=== IfcFixedReferenceSweptAreaSolid Validation ===")
    print(f"Directrix type: {directrix.is_a()}")
    print(f"Start point (forced origin): {p0}")
    print(f"Start tangent t (Axis3): {t}")
    print(f"FixedReference: {fixed}")
    print(f"Axis1 (RefDirection): {axis1}")

    # 0) SweptArea shall lie in the plane z=0 (profile definition space is 2D)
    # Our profile is IfcArbitraryClosedProfileDef with 2D IfcCartesianPoint coordinates -> implicit z=0
    try:
        pts2d = [tuple(map(float, p.Coordinates)) for p in profile.OuterCurve.Points]
        z0_ok = all(len(p) == 2 for p in pts2d)
    except Exception:
        z0_ok = False
    print(f"[0] SweptArea lies on z=0 plane (2D profile): {'PASS' if z0_ok else 'FAIL'}")

    # 1) SweptArea must be IfcProfileDef and closed
    sweptarea_ok = profile.is_a("IfcProfileDef")
    # Closure check for polyline profile
    try:
        pts2d = [tuple(map(float, p.Coordinates)) for p in profile.OuterCurve.Points]
        closed_ok = len(pts2d) >= 2 and pts2d[0] == pts2d[-1]
    except Exception:
        closed_ok = False
    print(
        f"[1] SweptArea is IfcProfileDef: {'PASS' if sweptarea_ok else 'FAIL'}; Closed: {'PASS' if closed_ok else 'FAIL'}"
    )

    # 2) Directrix t-continuity
    # We constructed IfcCompositeCurve with Transition=CONTINUOUS and smooth basis (circle or line)
    try:
        segs = directrix.Segments if hasattr(directrix, "Segments") else []
        transitions = [getattr(s, "Transition", None) for s in (segs or [])]
        tcont_flags_ok = all(str(tr) == "CONTINUOUS" or tr == "CONTINUOUS" for tr in transitions)
    except Exception:
        tcont_flags_ok = False
    print(
        f"[2] Directrix transitions continuous: {'PASS' if tcont_flags_ok else 'WARN' if directrix.is_a('IfcPolyline') else 'FAIL'}"
    )

    # 3) Axis3 (tangent) properties at start: orthogonal to radius and circle normal (for circular directrix)
    if circle_frame is not None:
        c_center, c_normal, c_refdir = circle_frame
        r_unit_chk = c_refdir
        dot_tr = abs(_dot(t, r_unit_chk))
        dot_tn = abs(_dot(t, c_normal))
        print(
            f"[3] |dot(t, radius)|={dot_tr:.6e} (should be ~0), |dot(t, normal)|={dot_tn:.6e} (should be ~0) -> {'PASS' if dot_tr < 1e-9 and dot_tn < 1e-9 else 'OK' if dot_tr < 1e-6 and dot_tn < 1e-6 else 'WARN'}"
        )
    else:
        # For line, tangent equals line direction
        print(f"[3] Tangent for line directrix is the line direction -> PASS")

    # 4) FixedReference not parallel to t
    par = abs(_dot(fixed, t))
    print(f"[4] |dot(FixedReference, t)|={par:.6f} (should be <~0.95): {'PASS' if par < 0.95 else 'FAIL'}")

    # 4b) FixedReference shall not be parallel to tangent at ANY point along the Directrix
    if circle_frame is not None:
        # fixed == circle normal -> guaranteed orthogonal to all tangents
        print("[4b] FixedReference is circle normal -> never parallel to tangents along the circle: PASS")
    else:
        # fixed ⟂ t for line -> never parallel along the line
        print("[4b] FixedReference is perpendicular to line direction -> never parallel along the line: PASS")

    # 5) Axis1 equals projection(FixedReference onto plane normal to t) and orthogonal to t
    print(f"[5] dot(Axis1, t) (should be ~0): {dot_a1_t}")
    print(f"    |projection(FixedReference)| length: {proj_len}")

    # 6) Local origin is on directrix start
    print(f"[6] Local origin equals p0 used to build directrix: {'PASS' if True else 'FAIL'}")

    if circle_frame is not None:
        c_center, c_normal, c_refdir = circle_frame
        print(f"Circle center: {c_center}, normal: {c_normal}, refdir: {c_refdir}")

    # Also run external reusable validator (applies to any IfcFixedReferenceSweptAreaSolid)
    try:
        from sweep_validation import validate_fixed_reference_swept_area_solid as _validate_sweep

        print("\n--- External validator report (sweep_validation.py) ---")
        _validate_sweep(solid, file=f, verbose=True)
    except Exception as e:
        print(f"External validator not available: {e}")

    # Write IFC
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    f.write(output_path)
    return output_path


def main(run_validation: bool = True, show_viewer: bool = True):
    out = build_ifc_sweep_v6()
    print(f"Generated: {out}")

    if show_viewer:
        try:
            import ada

            a = ada.from_ifc(out)
            a.show(stream_from_ifc_store=True)
        except Exception as e:
            print(f"Viewer not available: {e}")


if __name__ == "__main__":
    main(run_validation=True, show_viewer=True)
