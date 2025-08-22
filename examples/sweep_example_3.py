"""
Self-contained IFC example: Fixed Reference Swept Area Solid using ifcopenshell 0.8.1

- Creates a minimal IFC4 project with units, contexts, a site and one proxy element.
- The element geometry is an IfcFixedReferenceSweptAreaSolid, built from:
  * a small triangular profile (matching the pythonocc example's 'fillet' triangle)
  * a 3D polyline directrix sampled from the pythonocc sweep_example data (sweep1_pts)
- No dependencies on adapy or pythonocc; only ifcopenshell and standard libs.

Usage:
  python examples\\sweep_example_3.py

Output:
  temp\\sweep_example_3.ifc

Tested schema: IFC4 (works with ifcopenshell 0.8.1)
"""
from __future__ import annotations

import os
from math import sqrt
from typing import Iterable, List, Sequence, Tuple

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


def axis3d(
    f,
    origin_xyz=(0.0, 0.0, 0.0),
    axis=(0.0, 0.0, 1.0),
    refdir=(1.0, 0.0, 0.0),
):
    return f.create_entity(
        "IfcAxis2Placement3D",
        Location=pt3(f, origin_xyz),
        Axis=dir3(f, axis),
        RefDirection=dir3(f, refdir),
    )


# ----------------------
# Data based on src\\ada\\param_models\\sweep_example.py
# ----------------------
wt = 8e-3  # profile thickness used for the small fillet triangle
# Triangular profile in local 2D coordinates (closed by repeating the first point)
FILLET_TRIANGLE_2D: List[Tuple[float, float]] = [(0.0, 0.0), (-wt, 0.0), (0.0, wt)]

# Select one of the pythonocc sweep paths as directrix (sweep1)
SWEEP1_PTS: List[Tuple[float, float, float]] = [
    (287.85, 99.917, 513.26),
    (287.85, 100.083, 513.26),
    (287.85, 100.08950561835023, 513.2587059520527),
    (287.85, 100.09502081528021, 513.2550208152801),
    (287.85, 100.09870595205274, 513.2495056183502),
    (287.85, 100.10000000000005, 513.2429999999999),
    (287.85, 100.1, 513.077),
    (287.85, 100.09870595205268, 513.0704943816498),
    (287.85, 100.09502081528017, 513.0649791847198),
    (287.85, 100.0895056183502, 513.0612940479473),
    (287.85, 100.083, 513.06),
    (287.85, 99.917, 513.06),
    (287.85, 99.91049438164977, 513.0612940479473),
    (287.85, 99.90497918471979, 513.0649791847198),
    (287.85, 99.90129404794726, 513.0704943816497),
    (287.85, 99.89999999999995, 513.077),
    (287.85, 99.9, 513.2429999999999),
    (287.85, 99.90129404794732, 513.2495056183501),
    (287.85, 99.90497918471983, 513.2550208152801),
    (287.85, 99.9104943816498, 513.2587059520527),
    (287.85, 99.917, 513.26),
]


# ----------------------
# IFC model construction
# ----------------------

def build_ifc_fixed_ref_sweep(output_path: str = "temp\\sweep_example_3.ifc") -> str:
    # Create IFC4 file (widely supported; ifcopenshell 0.8.1 works fine)
    f = ifcopenshell.file(schema="IFC4")

    # Units
    si_length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    si_area = f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    si_vol = f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    si_ang = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")
    units = f.create_entity("IfcUnitAssignment", Units=[si_length, si_area, si_vol, si_ang])

    # Project
    project = f.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        Name="Sweep Example 3 (Fixed Reference)",
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
    # Profile: IfcArbitraryClosedProfileDef with IfcPolyline (2D)
    # ----------------------
    # Ensure closure by repeating the first point
    poly2d_pts = FILLET_TRIANGLE_2D + [FILLET_TRIANGLE_2D[0]]
    profile_polyline = f.create_entity("IfcPolyline", Points=[pt2(f, p) for p in poly2d_pts])

    profile = f.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        ProfileName="TriangleFillet",
        OuterCurve=profile_polyline,
    )

    # Position of the swept area solid local coordinate system
    pos = axis3d(f, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

    # ----------------------
    # Directrix: 3D IfcPolyline from SWEEP1_PTS
    # ----------------------
    directrix = f.create_entity("IfcPolyline", Points=[pt3(f, p) for p in SWEEP1_PTS])

    # Fixed reference direction: keep profile's local +Y aligned with global +Z
    fixed_ref = dir3(f, (0.0, 0.0, 1.0))

    # The swept solid
    solid = f.create_entity(
        "IfcFixedReferenceSweptAreaSolid",
        SweptArea=profile,
        Position=pos,
        Directrix=directrix,
        FixedReference=fixed_ref,
        # StartParam / EndParam could be set to trim the length if desired
    )

    # Wrap in a body representation
    shape_rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )

    pds = f.create_entity("IfcProductDefinitionShape", Representations=[shape_rep])

    # Product (simple proxy) and placement
    local_placement = f.create_entity("IfcLocalPlacement", RelativePlacement=axis3d(f, (0.0, 0.0, 0.0)))
    proxy = f.create_entity(
        "IfcBuildingElementProxy",
        GlobalId=ifcopenshell.guid.new(),
        Name="FixedReferenceSweptSolid",
        ObjectPlacement=local_placement,
        Representation=pds,
    )

    # Spatial structure: Site under Project, and containment of the proxy
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


def main():
    out = build_ifc_fixed_ref_sweep()
    import ada
    a = ada.from_ifc(out)
    a.show(stream_from_ifc_store=True)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
