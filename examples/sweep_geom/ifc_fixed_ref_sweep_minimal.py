import os

import ifcopenshell
import ifcopenshell.guid

import ada


# Replicates geometry 1:1 from files\\ifc_files\\fixed-reference-swept-area-solid.ifc
# Usage:
#   python examples\\ifc_fixed_ref_sweep_minimal.py
# Produces:
#   minimal_ifc_sweep.ifc (with AdvancedSweptSolid using IfcFixedReferenceSweptAreaSolid)


def dir3(f, xyz):
    return f.create_entity("IfcDirection", xyz)


def dir2(f, xy):
    return f.create_entity("IfcDirection", xy)


def pt3(f, xyz):
    return f.create_entity("IfcCartesianPoint", xyz)


def pt2(f, xy):
    return f.create_entity("IfcCartesianPoint", xy)


def axis2d(f, origin_xy, dir_xy):
    return f.create_entity("IfcAxis2Placement2D", Location=pt2(f, origin_xy), RefDirection=dir2(f, dir_xy))


def axis3d(f, origin_xyz=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0), refdir=(1.0, 0.0, 0.0)):
    return f.create_entity(
        "IfcAxis2Placement3D",
        Location=pt3(f, origin_xyz),
        Axis=dir3(f, axis),
        RefDirection=dir3(f, refdir),
    )


def main():
    f = ifcopenshell.file(schema="IFC4X3_ADD2")

    # Units and project
    si_length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    si_area = f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    si_vol = f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    si_ang = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")
    units = f.create_entity("IfcUnitAssignment", Units=[si_length, si_area, si_vol, si_ang])

    project = f.create_entity(
        "IfcProject",
        GlobalId=ifcopenshell.guid.new(),
        Name="FixedRef Sweep Project",
        UnitsInContext=units,
    )

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

    # Profile from reference IFC: IfcDerivedProfileDef(IfcArbitraryClosedProfileDef of IfcIndexedPolyCurve)
    # Points 2D: (-4,0), (-5,-1), (5,-1), (4,0)
    ppl = f.create_entity(
        "IfcCartesianPointList2D",
        CoordList=[(-4.0, 0.0), (-5.0, -1.0), (5.0, -1.0), (4.0, 0.0)],
    )
    # Segment indices 1-based per IFC: (1,2), (2,3), (3,4), (4,1)
    seg12 = f.create_entity("IfcLineIndex", (1, 2))
    seg23 = f.create_entity("IfcLineIndex", (2, 3))
    seg34 = f.create_entity("IfcLineIndex", (3, 4))
    seg41 = f.create_entity("IfcLineIndex", (4, 1))
    polycurve = f.create_entity("IfcIndexedPolyCurve", Points=ppl, Segments=[seg12, seg23, seg34, seg41])

    base_profile = f.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        ProfileName="Simple Profile",
        OuterCurve=polycurve,
    )

    # Transform operator for derived profile: Axis1=(0,-1), LocalOrigin=(0,0)
    tr = f.create_entity(
        "IfcCartesianTransformationOperator2D",
        Axis1=dir2(f, (0.0, -1.0)),
        Axis2=None,
        LocalOrigin=pt2(f, (0.0, 0.0)),
        Scale=None,
    )
    profile = f.create_entity(
        "IfcDerivedProfileDef",
        ProfileType="AREA",
        ParentProfile=base_profile,
        Operator=tr,
        Label=None,
    )

    # Position for the swept area solid
    pos = axis3d(f, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

    # Build BaseCurve = IfcCompositeCurve with 4 segments (line, clothoid, circle, discontinuous line)
    # Segment 1: Line, placement at (0,0) dir (1,0), length 400
    seg1_pl = axis2d(f, (0.0, 0.0), (1.0, 0.0))
    line1 = f.create_entity(
        "IfcLine",
        Pnt=pt2(f, (0.0, 0.0)),
        Dir=f.create_entity("IfcVector", Orientation=dir2(f, (1.0, 0.0)), Magnitude=1.0),
    )
    cc_seg1 = f.create_entity(
        "IfcCurveSegment",
        Transition="CONTSAMEGRADIENTSAMECURVATURE",
        Placement=seg1_pl,
        SegmentStart=f.create_entity("IfcLengthMeasure", 0.0),
        SegmentLength=f.create_entity("IfcLengthMeasure", 400.0),
        ParentCurve=line1,
    )

    # Segment 2: Clothoid, placement at (400,0) dir (1,0), length 150, constant -273.861278752584
    seg2_pl = axis2d(f, (400.0, 0.0), (1.0, 0.0))
    clothoid = f.create_entity(
        "IfcClothoid", Position=axis2d(f, (0.0, 0.0), (1.0, 0.0)), ClothoidConstant=-273.861278752584
    )
    cc_seg2 = f.create_entity(
        "IfcCurveSegment",
        Transition="CONTSAMEGRADIENTSAMECURVATURE",
        Placement=seg2_pl,
        SegmentStart=f.create_entity("IfcLengthMeasure", 0.0),
        SegmentLength=f.create_entity("IfcLengthMeasure", 150.0),
        ParentCurve=clothoid,
    )

    # Segment 3: CircularArc, placement at (549.662851380011,-7.48795505445), dir (0.988771077936042,-0.149438132473604), length -400, radius 500.000000000002
    seg3_pl = axis2d(f, (549.662851380011, -7.48795505445), (0.988771077936042, -0.149438132473604))
    circle = f.create_entity("IfcCircle", Position=axis2d(f, (0.0, 0.0), (1.0, 0.0)), Radius=500.000000000002)
    cc_seg3 = f.create_entity(
        "IfcCurveSegment",
        Transition="CONTSAMEGRADIENTSAMECURVATURE",
        Placement=seg3_pl,
        SegmentStart=f.create_entity("IfcLengthMeasure", 0.0),
        SegmentLength=f.create_entity("IfcLengthMeasure", -400.0),
        ParentCurve=circle,
    )

    # Segment 4: Discontinuous line (as per reference), zero length, placement at (881.65153753789,-211.03194929054)
    seg4_pl = axis2d(f, (881.65153753789, -211.03194929054), (0.58168308946, -0.81341550478))
    line_d = f.create_entity(
        "IfcLine",
        Pnt=pt2(f, (0.0, 0.0)),
        Dir=f.create_entity("IfcVector", Orientation=dir2(f, (1.0, 0.0)), Magnitude=1.0),
    )
    cc_seg4 = f.create_entity(
        "IfcCurveSegment",
        Transition="DISCONTINUOUS",
        Placement=seg4_pl,
        SegmentStart=f.create_entity("IfcLengthMeasure", 0.0),
        SegmentLength=f.create_entity("IfcLengthMeasure", 0.0),
        ParentCurve=line_d,
    )

    base_curve = f.create_entity(
        "IfcCompositeCurve", Segments=[cc_seg1, cc_seg2, cc_seg3, cc_seg4], SelfIntersect=False
    )

    # Build Directrix = IfcGradientCurve with 4 segments and BaseCurve ref
    # GC Segment 1: Line, placement (0,150) dir ~ (0.9999995,-0.001), length 450.000218741065
    g1_pl = axis2d(f, (0.0, 150.0), (0.999999500000375, -0.000999999499995919))
    g_line1 = f.create_entity(
        "IfcLine",
        Pnt=pt2(f, (0.0, 0.0)),
        Dir=f.create_entity("IfcVector", Orientation=dir2(f, (1.0, 0.0)), Magnitude=1.0),
    )
    g_seg1 = f.create_entity(
        "IfcCurveSegment",
        Transition="CONTSAMEGRADIENTSAMECURVATURE",
        Placement=g1_pl,
        SegmentStart=f.create_entity("IfcLengthMeasure", 0.0),
        SegmentLength=f.create_entity("IfcLengthMeasure", 450.000218741065),
        ParentCurve=g_line1,
    )

    # GC Segment 2: Circle, placement (449.999993741124,149.55) dir ~ (0.9999995,-0.001), start 4.71138898071803, length 100.00001881, radius 69230.7996321627
    g2_pl = axis2d(f, (449.999993741124, 149.55), (0.999999500000375, -0.000999999499995919))
    g_circle = f.create_entity("IfcCircle", Position=axis2d(f, (0.0, 0.0), (1.0, 0.0)), Radius=69230.7996321627)
    g_seg2 = f.create_entity(
        "IfcCurveSegment",
        Transition="CONTSAMEGRADIENTSAMECURVATURE",
        Placement=g2_pl,
        SegmentStart=f.create_entity("IfcLengthMeasure", 4.71138898071803),
        SegmentLength=f.create_entity("IfcLengthMeasure", 100.00001881),
        ParentCurve=g_circle,
    )

    # GC Segment 3: Line, placement (550,149.522222225005) dir ~ (0.999999001234583,0.000444444400554072), length 400.000039506171
    g3_pl = axis2d(f, (550.0, 149.522222225005), (0.999999001234583, 0.000444444400554072))
    g_line3 = f.create_entity(
        "IfcLine",
        Pnt=pt2(f, (0.0, 0.0)),
        Dir=f.create_entity("IfcVector", Orientation=dir2(f, (1.0, 0.0)), Magnitude=1.0),
    )
    g_seg3 = f.create_entity(
        "IfcCurveSegment",
        Transition="CONTSAMEGRADIENTSAMECURVATURE",
        Placement=g3_pl,
        SegmentStart=f.create_entity("IfcLengthMeasure", 0.0),
        SegmentLength=f.create_entity("IfcLengthMeasure", 400.000039506171),
        ParentCurve=g_line3,
    )

    # GC Segment 4: Discontinuous line, zero length, placement (950,149.7)
    g4_pl = axis2d(f, (950.0, 149.7), (0.999999001234583, 0.000444444400554072))
    g_line4 = f.create_entity(
        "IfcLine",
        Pnt=pt2(f, (0.0, 0.0)),
        Dir=f.create_entity("IfcVector", Orientation=dir2(f, (1.0, 0.0)), Magnitude=1.0),
    )
    g_seg4 = f.create_entity(
        "IfcCurveSegment",
        Transition="DISCONTINUOUS",
        Placement=g4_pl,
        SegmentStart=f.create_entity("IfcLengthMeasure", 0.0),
        SegmentLength=f.create_entity("IfcLengthMeasure", 0.0),
        ParentCurve=g_line4,
    )

    directrix = f.create_entity(
        "IfcGradientCurve",
        Segments=[g_seg1, g_seg2, g_seg3, g_seg4],
        SelfIntersect=False,
        BaseCurve=base_curve,
        EndPoint=None,
    )

    fixed_ref = dir3(f, (0.0, 0.0, 1.0))

    solid = f.create_entity(
        "IfcFixedReferenceSweptAreaSolid",
        SweptArea=profile,
        Position=pos,
        Directrix=directrix,
        StartParam=f.create_entity("IfcLengthMeasure", 300.0),
        EndParam=f.create_entity("IfcLengthMeasure", 600.0),
        FixedReference=fixed_ref,
    )

    # Wrap in a shape representation and a simple proxy element
    shape_rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="AdvancedSweptSolid",
        Items=[solid],
    )

    pds = f.create_entity("IfcProductDefinitionShape", Representations=[shape_rep])

    # Placement for the product
    local_placement = f.create_entity(
        "IfcLocalPlacement",
        RelativePlacement=axis3d(f, (0.0, 0.0, 0.0)),
    )

    proxy = f.create_entity(
        "IfcBuildingElementProxy",
        GlobalId=ifcopenshell.guid.new(),
        Name="FixedRefSweptSolid",
        ObjectPlacement=local_placement,
        Representation=pds,
    )

    # Assign project and containment
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

    os.makedirs("../temp", exist_ok=True)
    out = "temp/minimal_ifc_sweep.ifc"
    f.write(out)
    a = ada.from_ifc(out)
    a.show(stream_from_ifc_store=True)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
