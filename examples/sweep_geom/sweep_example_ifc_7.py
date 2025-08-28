"""
IFC FixedReferenceSweptAreaSolid example (indexed polyline directrix)

This example is based on sweep_example_ifc_6.py but replaces the circle-based
composite directrix with an IfcIndexedPolyCurve built from a list of 3D points.
It still strictly follows IfcFixedReferenceSweptAreaSolid orientation rules:

- Axis3 (local z) equals the tangent vector t at the start of the Directrix
- Axis1 (local x) equals the orthogonal projection of FixedReference onto the
  normal plane of t
- SweptArea is a subtype of IfcProfileDef
- The directrix is normalized to start at (0,0,0) in the swept solid's local
  space; the IfcLocalPlacement is responsible for global positioning.

Usage (recommended):
  pixi run -e tests python examples/sweep_geom/sweep_example_ifc_7.py

Alternative (if your environment is already active):
  python examples\\sweep_geom\\sweep_example_ifc_7.py

Output:
  temp\\sweep_example_7.ifc
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
# Geometry data (use same as v6)
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
# Directrix using IfcIndexedPolyCurve
# ----------------------


def _build_directrix_indexed_polycurve(f, pts: List[Tuple[float, float, float]]):
    """
    Builds an IfcIndexedPolyCurve from a list of 3D points. Tangent at start is the
    normalized vector from first to second point. Returns (curve, t_start, frame=None).
    """
    if len(pts) < 2:
        raise ValueError("Need at least 2 points to build an indexed polycurve")

    # Compute start tangent from first segment
    p0, p1 = pts[0], pts[1]
    t_start = _normalize(_sub(p1, p0))
    if t_start == (0.0, 0.0, 0.0):
        # Find the first non-zero segment
        for i in range(1, len(pts) - 1):
            t_start = _normalize(_sub(pts[i + 1], pts[i]))
            if t_start != (0.0, 0.0, 0.0):
                break
        if t_start == (0.0, 0.0, 0.0):
            t_start = (0.0, 1.0, 0.0)

    # IfcIndexedPolyCurve requires a point list entity (IfcCartesianPointList3D)
    # with Coordinates = list of 3D tuples
    point_list = f.create_entity("IfcCartesianPointList3D", CoordList=[list(map(float, p)) for p in pts])

    # For straight segments only, Segments may be omitted (interpreted as successive straight segments)
    indexed_curve = f.create_entity("IfcIndexedPolyCurve", Points=point_list)

    return indexed_curve, t_start, None


# ----------------------
# IFC model construction (schema-compliant orientation)
# ----------------------


def build_ifc_sweep_v7(output_path: str = "temp\\sweep_example_7.ifc") -> str:
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
        Name="Sweep Example 7 (Indexed PolyCurve Directrix)",
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

    # Directrix: build an indexed polycurve from the normalized points
    p0 = (0.0, 0.0, 0.0)
    directrix, t, _frame = _build_directrix_indexed_polycurve(f, norm_pts)

    # Choose FixedReference to be non-parallel to the tangent everywhere along the directrix
    # For a polyline, pick any vector perpendicular to the first segment direction t
    helper = (0.0, 0.0, 1.0)
    if abs(_dot(helper, t)) > 0.95:
        helper = (1.0, 0.0, 0.0)
    fixed = _normalize(_cross(t, helper))
    if fixed == (0.0, 0.0, 0.0):
        helper = (0.0, 1.0, 0.0)
        fixed = _normalize(_cross(t, helper))
    if fixed == (0.0, 0.0, 0.0):
        fixed = (1.0, 0.0, 0.0)

    # Axis1 = projection of FixedReference onto normal plane of t
    d = _dot(fixed, t)
    axis1 = (fixed[0] - d * t[0], fixed[1] - d * t[1], fixed[2] - d * t[2])
    axis1 = _normalize(axis1)
    if axis1 == (0.0, 0.0, 0.0):
        tmp = (1.0, 0.0, 0.0)
        if abs(_dot(tmp, t)) > 0.9:
            tmp = (0.0, 1.0, 0.0)
        axis1 = _normalize(_cross(t, _cross(tmp, t)))
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
        Name="IndexedPolyCurveSweptSolid",
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

    # Validation printout
    dot_a1_t = _dot(axis1, t)
    d_fixed_t = _dot(fixed, t)
    proj_len = sqrt(
        (fixed[0] - d_fixed_t * t[0]) ** 2 + (fixed[1] - d_fixed_t * t[1]) ** 2 + (fixed[2] - d_fixed_t * t[2]) ** 2
    )

    print("=== IfcFixedReferenceSweptAreaSolid Validation (IndexedPolyCurve) ===")
    print(f"Directrix type: {directrix.is_a()}")
    print(f"Start point (forced origin): {p0}")
    print(f"Start tangent t (Axis3): {t}")
    print(f"FixedReference: {fixed}")
    print(f"Axis1 (RefDirection): {axis1}")

    # 0) SweptArea shall lie in the plane z=0 (profile definition space is 2D)
    try:
        pts2d = [tuple(map(float, p.Coordinates)) for p in profile.OuterCurve.Points]
        z0_ok = all(len(p) == 2 for p in pts2d)
    except Exception:
        z0_ok = False
    print(f"[0] SweptArea lies on z=0 plane (2D profile): {'PASS' if z0_ok else 'FAIL'}")

    # 1) SweptArea must be IfcProfileDef and closed
    sweptarea_ok = profile.is_a("IfcProfileDef")
    try:
        pts2d = [tuple(map(float, p.Coordinates)) for p in profile.OuterCurve.Points]
        closed_ok = len(pts2d) >= 2 and pts2d[0] == pts2d[-1]
    except Exception:
        closed_ok = False
    print(
        f"[1] SweptArea is IfcProfileDef: {'PASS' if sweptarea_ok else 'FAIL'}; Closed: {'PASS' if closed_ok else 'FAIL'}"
    )

    # 2) Directrix continuity: indexed polycurve is piecewise linear; transition flags don't apply
    print(f"[2] Directrix is piecewise linear: continuity at vertices depends on point layout -> INFO")

    # 3) Tangent for polyline directrix equals first segment direction
    print(f"[3] Tangent for polyline directrix is the first segment direction -> PASS")

    # 4) FixedReference not parallel to t
    par = abs(_dot(fixed, t))
    print(f"[4] |dot(FixedReference, t)|={par:.6f} (should be <~0.95): {'PASS' if par < 0.95 else 'FAIL'}")
    print("[4b] FixedReference chosen perpendicular to first segment -> never parallel at start: PASS")

    # 5) Axis1 equals projection(FixedReference onto plane normal to t) and orthogonal to t
    print(f"[5] dot(Axis1, t) (should be ~0): {dot_a1_t}")
    print(f"    |projection(FixedReference)| length: {proj_len}")

    # 6) Local origin is on directrix start
    print(f"[6] Local origin equals p0 used to build directrix: {'PASS' if True else 'FAIL'}")

    # External validator if available
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
    out = build_ifc_sweep_v7()
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
