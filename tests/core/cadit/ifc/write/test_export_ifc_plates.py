import ada
from ada.cadit.ifc.utils import create_local_placement
from ada.cadit.ifc.write.geom.points import cpt
from ada.cadit.ifc.write.write_ifc import IfcWriter
from ada.core.guid import create_guid


def test_export_ifc_plate(plate1):
    _ = (ada.Assembly() / (ada.Part("MyPart") / plate1)).to_ifc(file_obj_only=True)


def test_export_rational_bspline_wknots(tmp_path, example_files):
    a = ada.Assembly()

    ctrl_pts = []
    for line in ctrl_pts_file.read_text().splitlines():
        subp = []
        for p in line.split(";"):
            pt = []
            for x in p.split(","):
                xf = float(x)
                pt.append(xf)
            points = cpt(a.ifc_store.f, pt)
            subp.append(points)
        ctrl_pts.append(tuple(subp))
    knotvector_u = (0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0)
    knotvector_v = (0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0)

    ifc_shape = a.ifc_store.f.add(
        a.ifc_store.f.create_entity(
            "IfcBSplineSurfaceWithKnots",
            UDegree=3,
            VDegree=3,
            ControlPointsList=tuple(ctrl_pts),
            SurfaceForm="UNSPECIFIED",
            UClosed=False,
            VClosed=True,
            SelfIntersect=False,
            UMultiplicities=(4, 1, 1, 4),
            VMultiplicities=(4, 1, 1, 4),
            UKnots=knotvector_u,
            VKnots=knotvector_v,
            KnotSpec="UNIFORM_KNOTS",
        )
    )
    part = a.add_part(ada.Part("MyPart"))
    a.ifc_store.sync()
    shape_placement = create_local_placement(a.ifc_store.f)
    representations = []
    orient_edges = []
    for i in range(4):
        pt = a.ifc_store.f.create_entity("IfcCartesianPoint", (i, 0, 0))

        orient_edges.append(a.ifc_store.f.create_entity("IfcOrientedEdge", (i + 1, i + 2, None, True)))

    ifc_edge_loop = a.ifc_store.f.create_entity("IfcEdgeLoop", orient_edges)
    ifc_outer_bound = a.ifc_store.f.create_entity("IfcFaceOuterBound", [ifc_edge_loop])
    ifc_advanced_face = a.ifc_store.f.create_entity("IfcAdvancedFace", (ifc_outer_bound,), ifc_shape)
    ifc_closed_shell = a.ifc_store.f.create_entity("IfcClosedShell", [ifc_advanced_face])
    advanced_brep = a.ifc_store.f.create_entity("IfcAdvancedBrep", ifc_closed_shell)
    body = a.ifc_store.f.create_entity(
        "IfcShapeRepresentation", a.ifc_store.get_context("Body"), "Body", "SolidModel", [advanced_brep]
    )
    representations.append(body)
    product_shape = a.ifc_store.f.create_entity("IfcProductDefinitionShape", None, None, representations)
    ifc_elem = a.ifc_store.f.create_entity(
        "IfcBuildingElementProxy",
        GlobalId=create_guid(),
        OwnerHistory=a.ifc_store.owner_history,
        Name="MyShape",
        ObjectType=None,
        ObjectPlacement=shape_placement,
        Representation=product_shape,
    )
    writer = IfcWriter(a.ifc_store)
    writer.add_related_elements_to_spatial_container([ifc_elem], part.guid)

    export_to = tmp_path / "curved_plate.ifc"
    a.to_ifc(export_to, file_obj_only=False, validate=True)
    print(f"Exported to {export_to}")
