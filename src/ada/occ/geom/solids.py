import math

import OCC.Core.BRepPrimAPI as occBrep
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_RoundCorner, BRepBuilderAPI_Transform
from OCC.Core.BRepOffsetAPI import (
    BRepOffsetAPI_MakePipeShell,
    BRepOffsetAPI_ThruSections,
)
from OCC.Core.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Solid
from OCC.Extend.TopologyUtils import TopologyExplorer

import ada.geom.solids as geo_so
from ada.config import logger
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.occ.geom.curves import make_wire_from_curve
from ada.occ.geom.surfaces import make_profile_from_geom
from ada.occ.utils import transform_shape_to_pos


def make_box_from_geom(box: geo_so.Box) -> TopoDS_Shape:
    axis1 = box.position.axis
    axis2 = box.position.ref_direction
    vec1 = gp_Dir(0, 0, 1) if axis1 is None else gp_Dir(*axis1)
    vec2 = gp_Dir(0, 1, 0) if axis2 is None else gp_Dir(*axis2)

    box_maker = occBrep.BRepPrimAPI_MakeBox(
        gp_Ax2(
            gp_Pnt(*box.position.location),
            vec1,
            vec2,
        ),
        box.x_length,
        box.y_length,
        box.z_length,
    )
    return box_maker.Shape()


def make_sphere_from_geom(sphere: geo_so.Sphere) -> TopoDS_Shape:
    return occBrep.BRepPrimAPI_MakeSphere(gp_Pnt(*sphere.center), sphere.radius).Shape()


def make_rectangular_pyramid_from_geom(rp: geo_so.RectangularPyramid) -> TopoDS_Shape:
    """Build an IfcRectangularPyramid: a rectangular base in the local XY plane with the apex
    centred above it at the given height. Built from its 4 triangular sides + base, sewn into a
    solid, then placed."""
    from OCC.Core.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_MakeSolid,
        BRepBuilderAPI_Sewing,
    )

    x, y, z = rp.x_length, rp.y_length, rp.z_length
    base = [gp_Pnt(0, 0, 0), gp_Pnt(x, 0, 0), gp_Pnt(x, y, 0), gp_Pnt(0, y, 0)]
    apex = gp_Pnt(x / 2.0, y / 2.0, z)

    sewing = BRepBuilderAPI_Sewing(1e-7)
    base_poly = BRepBuilderAPI_MakePolygon(base[0], base[1], base[2], base[3], True)
    sewing.Add(BRepBuilderAPI_MakeFace(base_poly.Wire(), True).Face())
    for i in range(4):
        tri = BRepBuilderAPI_MakePolygon(base[i], base[(i + 1) % 4], apex, True)
        sewing.Add(BRepBuilderAPI_MakeFace(tri.Wire(), True).Face())
    sewing.Perform()

    solid = BRepBuilderAPI_MakeSolid(sewing.SewedShape()).Solid()
    return transform_shape_to_pos(solid, rp.position.location, rp.position.axis, rp.position.ref_direction)


def make_cylinder_from_geom(cylinder: geo_so.Cylinder) -> TopoDS_Shape:
    axis = cylinder.position.axis
    vec = gp_Dir(0, 0, 1) if axis is None else gp_Dir(*axis)
    place = gp_Ax2(gp_Pnt(*cylinder.position.location), vec)
    cylinder_maker = occBrep.BRepPrimAPI_MakeCylinder(place, cylinder.radius, cylinder.height)
    return cylinder_maker.Shape()


def make_cone_from_geom(cone: geo_so.Cone) -> TopoDS_Shape:
    axis = cone.position.axis
    vec = gp_Dir(0, 0, 1) if axis is None else gp_Dir(*axis)
    cone_maker = occBrep.BRepPrimAPI_MakeCone(
        gp_Ax2(gp_Pnt(*cone.position.location), vec), cone.bottom_radius, 0, cone.height
    )
    return cone_maker.Shape()


def make_extruded_area_shape_tapered_from_geom(eas: geo_so.ExtrudedAreaSolidTapered):
    o = Point(0, 0, 0)
    z = Direction(0, 0, 1)
    p2 = o + eas.depth * z

    profile1 = make_profile_from_geom(eas.swept_area)
    _profile2 = make_profile_from_geom(eas.end_swept_area)
    profile2 = transform_shape_to_pos(_profile2, p2, z, Direction(1, 0, 0))

    wire1 = list(TopologyExplorer(profile1).wires())[0]
    wire2 = list(TopologyExplorer(profile2).wires())[0]
    ts = BRepOffsetAPI_ThruSections(True)
    ts.AddWire(wire1)
    ts.AddWire(wire2)
    ts.Build()
    shape = ts.Shape()
    return transform_shape_to_pos(shape, eas.position.location, eas.position.axis, eas.position.ref_direction)


def make_extruded_area_shape_from_geom(eas: geo_so.ExtrudedAreaSolid) -> TopoDS_Shape | TopoDS_Solid:
    profile = make_profile_from_geom(eas.swept_area)

    # Build direction is always Z
    vec = Direction(0, 0, 1) * eas.depth
    eas_shape = occBrep.BRepPrimAPI_MakePrism(profile, gp_Vec(*vec)).Shape()

    # Transform to correct position before returning
    return transform_shape_to_pos(eas_shape, eas.position.location, eas.position.axis, eas.position.ref_direction)


def make_revolved_area_shape_from_geom(ras: geo_so.RevolvedAreaSolid) -> TopoDS_Shape | TopoDS_Solid:
    profile = make_profile_from_geom(ras.swept_area)

    # Transform 2d profile to position before revolving the shape
    profile = transform_shape_to_pos(profile, ras.position.location, ras.position.axis, ras.position.ref_direction)

    rev_axis = gp_Ax1(gp_Pnt(*ras.axis.location), gp_Dir(*ras.axis.axis))
    ras_shape = occBrep.BRepPrimAPI_MakeRevol(profile, rev_axis, math.radians(ras.angle)).Shape()

    return ras_shape


def make_faceted_brep_from_geom(brep: geo_so.FacetedBrep) -> TopoDS_Shape | TopoDS_Solid:
    """Build a faceted B-rep: the outer closed shell becomes a solid, and each inner void shell
    is made solid and cut out."""
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeSolid

    from ada.occ.geom.surfaces import make_closed_shell_from_geom

    solid = BRepBuilderAPI_MakeSolid(make_closed_shell_from_geom(brep.outer)).Solid()
    for void in brep.voids:
        void_solid = BRepBuilderAPI_MakeSolid(make_closed_shell_from_geom(void)).Solid()
        solid = BRepAlgoAPI_Cut(solid, void_solid).Shape()
    return solid


def make_swept_disk_solid_from_geom(sds: geo_so.SweptDiskSolid) -> TopoDS_Shape | TopoDS_Solid:
    """Sweep a circular (or annular) disk along the directrix — the pipe/rod primitive.

    The disk profile is placed at the spine start, normal to the start tangent; PipeShell
    keeps it perpendicular along the spine. An inner radius is swept the same way and cut out
    to leave a tube."""
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCC.Core.BRepTools import BRepTools_WireExplorer
    from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec

    spine = make_wire_from_curve(sds.directrix)

    # Start point + tangent of the spine (a circle is rotationally symmetric, so the
    # tangent's sign is irrelevant — only the plane it defines matters).
    first_edge = BRepTools_WireExplorer(spine).Current()
    curve, f0, _ = BRep_Tool.Curve(first_edge)
    p0 = gp_Pnt()
    d0 = gp_Vec()
    curve.D1(f0, p0, d0)
    disk_axis = gp_Ax2(p0, gp_Dir(d0))

    def _disk_wire(r: float):
        edge = BRepBuilderAPI_MakeEdge(gp_Circ(disk_axis, r)).Edge()
        return BRepBuilderAPI_MakeWire(edge).Wire()

    def _sweep(profile_wire):
        pipe_builder = BRepOffsetAPI_MakePipeShell(spine)
        pipe_builder.SetTransitionMode(BRepBuilderAPI_RoundCorner)
        pipe_builder.Add(profile_wire, True, False)
        pipe_builder.Build()
        pipe_builder.MakeSolid()
        return pipe_builder.Shape()

    solid = _sweep(_disk_wire(sds.radius))
    if sds.inner_radius:
        solid = BRepAlgoAPI_Cut(solid, _sweep(_disk_wire(sds.inner_radius))).Shape()
    return solid


def make_fixed_reference_swept_area_shape_from_geom(frs: geo_so.FixedReferenceSweptAreaSolid) -> TopoDS_Solid:
    spine = make_wire_from_curve(frs.directrix)

    profile_face = make_profile_from_geom(frs.swept_area)

    # Extract the outer wire from the profile face
    profile_wire = list(TopologyExplorer(profile_face).wires())[0]

    # Use PipeShell for better handling of 90-degree bends
    pipe_builder = BRepOffsetAPI_MakePipeShell(spine)

    # Set frenet frame algorithm for better orientation around bends
    # BRepBuilderAPI_RoundCorner
    # BRepBuilderAPI_RightCorner
    pipe_builder.SetTransitionMode(BRepBuilderAPI_RoundCorner)

    # Add the wire profile (not the face)
    pipe_builder.Add(profile_wire, True, False)  # with contact and correction

    pipe_builder.Build()
    try:
        pipe_builder.MakeSolid()
    except RuntimeError as e:
        # An open/non-closable sweep (e.g. a sampled alignment spine) still
        # yields a valid shell — renderable and exportable — so degrade to it
        # rather than dropping the body.
        logger.warning(f"FixedReferenceSweptAreaSolid: MakeSolid failed ({e}); exporting the swept shell")
    swept_solid = pipe_builder.Shape()

    location = frs.position.location.tolist()

    # Then translate to final position
    trsf_to_pos = gp_Trsf()
    trsf_to_pos.SetTranslation(gp_Vec(*location))
    transformed_solid = BRepBuilderAPI_Transform(swept_solid, trsf_to_pos, True, True).Shape()
    return transformed_solid
