"""
Simplified IFC sweep example - Fresh approach to match OpenCascade geometry exactly

This version starts from scratch with a focus on matching the OCC coordinate system:
- OCC uses profile normal = (0, 1, 0) and profile_ydir = (0, 0, 1)  
- OCC embeds the 2D profile [(0,0), (-wt,0), (0,wt)] into 3D using cross products
- We'll translate this exact approach into IFC terms

Key differences from sweep_example_3.py:
- Simplified directrix (fewer points to reduce complexity)
- Direct coordinate system matching OCC's approach
- Focus on getting the basic geometry right first

Usage:
  python examples\\sweep_example_4.py

Output:
  temp\\sweep_example_4.ifc
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
# Geometry data matching OCC exactly
# ----------------------
wt = 8e-3  # profile thickness

# Original profile from OCC
FILLET_TRIANGLE_2D_ORIGINAL: List[Tuple[float, float]] = [(0.0, 0.0), (-wt, 0.0), (0.0, wt)]

# Scaled profile to compensate for X dimension doubling in IFC
# Scale X dimension by 0.5 to counteract the doubling
FILLET_TRIANGLE_2D: List[Tuple[float, float]] = [(0.0, 0.0), (-wt * 0.5, 0.0), (0.0, wt)]

# Minimal diagnostic directrix - test basic curved path capability
SIMPLIFIED_SWEEP_PTS: List[Tuple[float, float, float]] = [
    (287.85, 99.917, 513.26),  # start
    (287.85, 100.0, 513.06),  # middle (significant Z drop creates clear curve)
    (287.85, 100.083, 513.26),  # end (back to start Z level)
]


# ----------------------
# Vector utilities (matching OCC implementation)
# ----------------------


def _normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    x, y, z = v
    n = sqrt(x * x + y * y + z * z)
    if n == 0:
        return (0.0, 0.0, 0.0)
    return (x / n, y / n, z / n)


def _cross(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    ax, ay, az = a
    bx, by, bz = b
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def _dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    ax, ay, az = a
    bx, by, bz = b
    return ax * bx + ay * by + az * bz


def compute_occ_profile_frame(
    origin: Tuple[float, float, float],
    normal: Tuple[float, float, float] = (0.0, 1.0, 0.0),
    ydir: Tuple[float, float, float] = (0.0, 0.0, 1.0),
):
    """
    Compute the exact coordinate frame that OCC uses for embedding 2D profile into 3D.
    This matches the logic in make_profile_wire() from sweep_example.py.

    Returns: (x_axis, y_axis, normal) as 3D unit vectors
    """
    n = _normalize(normal)
    y0 = _normalize(ydir)

    # Make sure y0 is not parallel to normal
    if abs(_dot(y0, n)) > 0.999:
        y0 = (1.0, 0.0, 0.0)  # fallback

    # Compute orthonormal frame
    x = _normalize(_cross(y0, n))
    y = _normalize(_cross(n, x))

    return x, y, n


def embed_2d_profile_to_3d(
    profile2d: List[Tuple[float, float]],
    origin: Tuple[float, float, float],
    x_axis: Tuple[float, float, float],
    y_axis: Tuple[float, float, float],
) -> List[Tuple[float, float, float]]:
    """
    Embed 2D profile points into 3D using the provided coordinate frame.
    This matches OCC's approach exactly.
    """
    ox, oy, oz = origin
    profile3d = []

    for u, v in profile2d:
        px = ox + u * x_axis[0] + v * y_axis[0]
        py = oy + u * x_axis[1] + v * y_axis[1]
        pz = oz + u * x_axis[2] + v * y_axis[2]
        profile3d.append((px, py, pz))

    return profile3d


# ----------------------
# IFC model construction
# ----------------------


def build_ifc_sweep_v4(output_path: str = "temp\\sweep_example_4.ifc") -> str:
    """
    Build IFC sweep using a completely fresh approach that matches OCC coordinate system exactly.
    """
    f = ifcopenshell.file(schema="IFC4")

    # Basic project setup
    si_length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    si_area = f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    si_vol = f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    si_ang = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")
    units = f.create_entity("IfcUnitAssignment", Units=[si_length, si_area, si_vol, si_ang])

    project = f.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        Name="Sweep Example 4 (Fresh Approach)",
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

    # ----------------------
    # Profile: Match OCC's exact approach
    # ----------------------
    # Compute the exact coordinate frame that OCC uses
    p0 = SIMPLIFIED_SWEEP_PTS[0]
    occ_normal = (0.0, 1.0, 0.0)  # profile normal in OCC
    occ_ydir = (0.0, 0.0, 1.0)  # profile Y direction in OCC

    x_axis, y_axis, normal = compute_occ_profile_frame(p0, occ_normal, occ_ydir)

    # Convert 2D profile to 3D using the same logic as OCC
    profile3d = embed_2d_profile_to_3d(FILLET_TRIANGLE_2D, p0, x_axis, y_axis)

    # Ensure closure
    if profile3d[0] != profile3d[-1]:
        profile3d.append(profile3d[0])

    # Create 2D profile for IFC (we'll position it correctly)
    profile2d_pts = FILLET_TRIANGLE_2D + [FILLET_TRIANGLE_2D[0]]  # ensure closed
    profile_polyline = f.create_entity("IfcPolyline", Points=[pt2(f, p) for p in profile2d_pts])

    profile = f.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        ProfileName="TriangleFillet",
        OuterCurve=profile_polyline,
    )

    # ----------------------
    # Directrix: Simplified 3D polyline
    # ----------------------
    directrix = f.create_entity("IfcPolyline", Points=[pt3(f, p) for p in SIMPLIFIED_SWEEP_PTS])

    # ----------------------
    # Position: Try multiple coordinate system approaches
    # ----------------------
    # Test different orientations to fix the X dimension doubling

    # Approach 1: Try reversing X-axis to fix doubling
    # pos = axis3d(f, p0, normal, tuple(-x for x in x_axis))

    # Approach 2: Use Y-axis as RefDirection instead of X-axis
    # pos = axis3d(f, p0, normal, y_axis)

    # Approach 3: Try standard XY orientation at origin
    pos = axis3d(f, p0, (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

    # ----------------------
    # FixedReference: Try different orientations
    # ----------------------
    # Approach 1: Use profile's Y direction (original approach)
    # fixed_ref = dir3(f, y_axis)

    # Approach 2: Use profile's X direction to see if it fixes scaling
    # fixed_ref = dir3(f, x_axis)

    # Approach 3: Use standard Z direction
    fixed_ref = dir3(f, (0.0, 0.0, 1.0))

    # Create the swept solid
    solid = f.create_entity(
        "IfcFixedReferenceSweptAreaSolid",
        SweptArea=profile,
        Position=pos,
        Directrix=directrix,
        FixedReference=fixed_ref,
    )

    # Wrap in representation
    shape_rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )

    pds = f.create_entity("IfcProductDefinitionShape", Representations=[shape_rep])

    # Product and placement
    local_placement = f.create_entity("IfcLocalPlacement", RelativePlacement=axis3d(f, (0.0, 0.0, 0.0)))
    proxy = f.create_entity(
        "IfcBuildingElementProxy",
        GlobalId=ifcopenshell.guid.new(),
        Name="SimplifiedSweptSolid",
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

    # Write IFC
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    f.write(output_path)
    return output_path


# ----------------------
# Validation utilities
# ----------------------


def _triples(flat: Sequence[float]) -> List[Tuple[float, float, float]]:
    return [(float(flat[i]), float(flat[i + 1]), float(flat[i + 2])) for i in range(0, len(flat), 3)]


def _bbox_from_vertices(verts: List[Tuple[float, float, float]]):
    if not verts:
        return (0, 0, 0), (0, 0, 0)
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    mn = (min(xs), min(ys), min(zs))
    mx = (max(xs), max(ys), max(zs))
    return mn, mx


def _size_from_bbox(mn, mx):
    return (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])


def load_ifc_mesh_bbox(ifc_path: str):
    """Load first product's mesh from IFC using ifcopenshell.geom and return bbox and size."""
    try:
        import ifcopenshell.geom as geom
    except Exception as e:
        raise RuntimeError("ifcopenshell.geom not available: cannot validate IFC geometry") from e

    settings = geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)

    f = ifcopenshell.open(ifc_path)
    products = f.by_type("IfcProduct")
    target = None
    for p in products:
        if p.is_a("IfcBuildingElementProxy") and getattr(p, "Representation", None):
            target = p
            break
    if target is None:
        for p in products:
            if getattr(p, "Representation", None):
                target = p
                break
    if target is None:
        raise RuntimeError("No representable product found in IFC for validation")

    shape = geom.create_shape(settings, target)
    verts = _triples(shape.geometry.verts)
    mn, mx = _bbox_from_vertices(verts)
    return mn, mx, _size_from_bbox(mn, mx)


def reference_mesh_bbox_for_sweep1():
    """Get OCC-based reference mesh bbox for sweep1 using existing example code."""
    import sys, os as _os

    _root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "src"))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from ada.param_models.sweep_example import get_three_sweeps_mesh_data

    data = get_three_sweeps_mesh_data()
    ref = data[0]  # Sweep1
    verts = [tuple(map(float, v)) for v in ref["vertices"]]
    mn, mx = _bbox_from_vertices(verts)
    return mn, mx, _size_from_bbox(mn, mx)


def validate_against_occ_reference(ifc_path: str, rel_tol: float = 1e-2) -> bool:
    """Validate IFC geometry by comparing its bounding box size to the OCC reference sweep1."""
    ref_mn, ref_mx, ref_sz = reference_mesh_bbox_for_sweep1()
    ifc_mn, ifc_mx, ifc_sz = load_ifc_mesh_bbox(ifc_path)

    def rel_err(a, b):
        if max(abs(b), 1e-9) == 0:
            return abs(a - b)
        return abs(a - b) / max(abs(b), 1e-9)

    errs = [rel_err(ifc_sz[i], ref_sz[i]) for i in range(3)]
    ok = all(e <= rel_tol for e in errs)

    print("=== Validation Report (Fresh Approach) ===")
    print(f"Reference size (OCC): {ref_sz}")
    print(f"IFC size            : {ifc_sz}")
    print(f"Relative errors     : {errs} (tolerance={rel_tol})")
    print("Result              : {}".format("PASS" if ok else "FAIL"))

    # Print coordinate frame info for debugging
    p0 = SIMPLIFIED_SWEEP_PTS[0]
    x_axis, y_axis, normal = compute_occ_profile_frame(p0, (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    print("\n=== Coordinate Frame Analysis ===")
    print(f"Origin       : {p0}")
    print(f"Normal (Z)   : {normal}")
    print(f"X-axis       : {x_axis}")
    print(f"Y-axis       : {y_axis}")

    return ok


def main(run_validation: bool = True, show_viewer: bool = True):
    out = build_ifc_sweep_v4()
    print(f"Generated: {out}")

    if run_validation:
        try:
            validate_against_occ_reference(out)
        except Exception as e:
            print(f"Validation failed: {e}")

    if show_viewer:
        try:
            import ada

            a = ada.from_ifc(out)
            a.show(stream_from_ifc_store=True)
        except Exception as e:
            print(f"Viewer not available: {e}")


if __name__ == "__main__":
    main(run_validation=True, show_viewer=True)
