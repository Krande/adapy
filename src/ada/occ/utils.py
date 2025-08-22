from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Union

import numpy as np
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakePipe
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCC.Core.ChFi2d import ChFi2d_AnaFilletAlgo
from OCC.Core.GC import GC_MakeArcOfCircle
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.gp import (
    gp_Ax1,
    gp_Ax2,
    gp_Ax3,
    gp_Circ,
    gp_Dir,
    gp_Pln,
    gp_Pnt,
    gp_Trsf,
    gp_Vec,
)
from OCC.Core.TopoDS import (
    TopoDS_Edge,
    TopoDS_Face,
    TopoDS_Shape,
    TopoDS_Vertex,
    TopoDS_Wire,
)
from OCC.Extend.ShapeFactory import make_extrusion, make_face, make_wire
from OCC.Extend.TopologyUtils import TopologyExplorer

from ada.api.transforms import EquationOfPlane, Placement, Plane, Rotation
from ada.config import logger
from ada.core.utils import roundoff
from ada.core.vector_transforms import normal_to_points_in_plane
from ada.core.vector_utils import is_parallel, unit_vector, vector_length
from ada.fem.shapes import ElemType
from ada.geom.booleans import BoolOpEnum
from ada.geom.direction import Direction
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada import ArcSegment, Boolean, LineSegment, Part


def extract_shapes(step_path, scale, transform, rotate, include_shells=False):
    from OCC.Extend.DataExchange import read_step_file

    shapes = []

    cad_file_path = pathlib.Path(step_path)
    if cad_file_path.is_file():
        stp_data = read_step_file(str(cad_file_path), as_compound=False)
        if not isinstance(stp_data, list):
            stp_data = [stp_data]
        for sub_shape in stp_data:
            shapes += extract_subshapes(sub_shape, include_shells=include_shells)
    elif cad_file_path.is_dir():
        shapes += walk_shapes(cad_file_path)
    else:
        raise Exception(f'step_ref "{step_path}" does not represent neither file or folder found on system')

    shapes = [transform_shape(s, scale, transform, rotate) for s in shapes]
    return shapes


def transform_shape(
    shape: TopoDS_Shape, scale=None, transform: Placement | tuple | list = None, rotate: Rotation = None
) -> TopoDS_Shape:
    trsf = gp_Trsf()
    if scale is not None:
        trsf.SetScaleFactor(scale)
    if transform is not None:
        if type(transform) is Placement:
            tra = transform.origin
            trsf.SetTranslation(gp_Vec(tra[0], tra[1], tra[2]))
        elif type(transform) in (tuple, list):
            trsf.SetTranslation(gp_Vec(transform[0], transform[1], transform[2]))
        else:
            raise ValueError(f'Unrecognized transform input type "{type(transform)}"')
    if rotate is not None:
        pt = gp_Pnt(*rotate.origin)
        dire = gp_Dir(*rotate.vector)
        revolve_axis = gp_Ax1(pt, dire)
        trsf.SetRotation(revolve_axis, math.radians(rotate.angle))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def walk_shapes(dir_path):
    from OCC.Extend.DataExchange import read_step_file

    from ada.core.file_system import get_list_of_files

    shps = []
    for stp_file in get_list_of_files(dir_path, ".stp"):
        shps += extract_subshapes(read_step_file(stp_file))
    return shps


def extract_subshapes(shp_, include_shells=False):
    t = TopologyExplorer(shp_)
    result = list(t.solids())
    if include_shells:
        result += list(t.shells())
        # result += list(t.vertices())

    return result


def is_edges_ok(edge1, fillet, edge2):
    t1 = TopologyExplorer(edge1).number_of_vertices()
    t2 = TopologyExplorer(fillet).number_of_vertices()
    t3 = TopologyExplorer(edge2).number_of_vertices()

    if t1 == 0 or t2 == 0 or t3 == 0:
        return False
    else:
        return True


def make_wire_from_points(points):
    if type(points[0]) in (list, tuple):
        p1 = list(points[0])
        p2 = list(points[1])
    else:
        p1 = points[0].tolist()
        p2 = points[1].tolist()

    if len(p1) == 2:
        p1 += [0]
        p2 += [0]

    return make_wire([BRepBuilderAPI_MakeEdge(gp_Pnt(*p1), gp_Pnt(*p2)).Edge()])


def get_boundingbox(shape: TopoDS_Shape, tol=1e-6, use_mesh=True) -> tuple[tuple, tuple]:
    """

    :param shape: TopoDS_Shape or a subclass such as TopoDS_Face the shape to compute the bounding box from
    :param tol: tolerance of the computed boundingbox
    :param use_mesh: a flag that tells whether or not the shape has first to be meshed before the bbox computation.
                     This produces more accurate results
    :return: return the bounding box of the TopoDS_Shape `shape`
    """

    bbox = Bnd_Box()
    bbox.SetGap(tol)
    if use_mesh:
        mesh = BRepMesh_IncrementalMesh()
        mesh.SetParallelDefault(True)
        mesh.SetShape(shape)
        mesh.Perform()
        if not mesh.IsDone():
            raise AssertionError("Mesh not done.")
    brepbndlib.Add(shape, bbox, use_mesh)

    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


def face_to_wires(face):
    topo_exp = TopologyExplorer(face)
    wires = list()
    for w in topo_exp.wires_from_face(face):
        wires.append(w)
    return wires


def make_fillet(edge1, edge2, bend_radius):
    f = ChFi2d_AnaFilletAlgo()

    points1 = get_points_from_occ_shape(edge1)
    points2 = get_points_from_occ_shape(edge2)
    normal = normal_to_points_in_plane([np.array(x) for x in points1] + [np.array(x) for x in points2])
    plane_normal = gp_Dir(gp_Vec(normal[0], normal[1], normal[2]))

    t = TopologyExplorer(edge1)
    apt = None
    for v in t.vertices():
        apt = BRep_Tool.Pnt(v)

    f.Init(edge1, edge2, gp_Pln(apt, plane_normal))
    f.Perform(bend_radius)
    fillet2d = f.Result(edge1, edge2)
    if is_edges_ok(edge1, fillet2d, edge2) is False:
        raise ValueError("Unsuccessful filleting of edges")

    return edge1, edge2, fillet2d


def get_midpoint_of_arc(edge):
    res = divide_edge_by_nr_of_points(edge, 3)
    return res[1][1].X(), res[1][1].Y(), res[1][1].Z()


def divide_edge_by_nr_of_points(edg, n_pts):
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GCPnts import GCPnts_UniformAbscissa

    """returns a nested list of parameters and points on the edge
    at the requested interval [(param, gp_Pnt),...]
    """
    curve_adapt = BRepAdaptor_Curve(edg)
    _lbound, _ubound = curve_adapt.FirstParameter(), curve_adapt.LastParameter()

    if n_pts <= 1:
        # minimally two points or a Standard_ConstructionError is raised
        raise AssertionError("minimally 2 points required")

    npts = GCPnts_UniformAbscissa(curve_adapt, n_pts, _lbound, _ubound)
    if npts.IsDone():
        tmp = []
        for i in range(1, npts.NbPoints() + 1):
            param = npts.Parameter(i)
            pnt = curve_adapt.Value(param)
            tmp.append((param, pnt))
        return tmp


def get_points_from_occ_shape(occ_shape: TopoDS_Shape | TopoDS_Vertex | TopoDS_Edge | TopoDS_Face):
    t = TopologyExplorer(occ_shape)
    points = []
    for v in t.vertices():
        apt = BRep_Tool.Pnt(v)
        points.append((apt.X(), apt.Y(), apt.Z()))
    return points


def get_face_normal(a_face: TopoDS_Face) -> tuple[Point, Direction] | tuple[None, None]:
    """Based on core_geometry_face_recognition_from_stepfile.py in pythonocc-demos"""
    surf = BRepAdaptor_Surface(a_face, True)
    surf_type = surf.GetType()
    if surf_type != GeomAbs_Plane:
        return None, None

    gp_pln = surf.Plane()
    location = gp_pln.Location().XYZ().Coord()  # a point of the plane
    normal = gp_pln.Axis().Direction()  # the plane normal
    return Point(*location), Direction(normal.X(), normal.Y(), normal.Z())


@dataclass
class TopoDSFaceDebug:
    face: TopoDS_Face
    point: Point
    normal: Direction


def get_face_debug_params(face: TopoDS_Face) -> TopoDSFaceDebug:
    point, normal = get_face_normal(face)

    return TopoDSFaceDebug(face, point, normal)


def iter_faces_with_normal(shape, normal, point_in_plane: Iterable | Point = None):
    normal = Direction(*normal)
    eop = None
    if point_in_plane is not None:
        eop = EquationOfPlane(point_in_plane, normal)

    t = TopologyExplorer(shape)
    for face in t.faces():
        point, n = get_face_normal(face)
        if n is None:
            continue
        if not is_parallel(n, normal):
            continue
        if eop is None:
            yield face
            continue

        dist = eop.calc_distance_to_point(point)
        if dist == 0.0:
            yield face


def make_closed_polygon(*args):
    poly = BRepBuilderAPI_MakePolygon()
    for pt in args:
        if isinstance(pt, list) or isinstance(pt, tuple):
            for i in pt:
                poly.Add(i)
        else:
            poly.Add(pt)
    poly.Build()
    poly.Close()
    result = poly.Wire()
    return result


def make_face_w_cutout(face: TopoDS_Face, wire_cutout: TopoDS_Wire) -> TopoDS_Face:
    wire_cutout.Reverse()
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace

    return BRepBuilderAPI_MakeFace(face, wire_cutout).Face()


def make_circle(p, vec, r):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    circle_origin = gp_Ax2(gp_Pnt(p[0], p[1], p[2]), gp_Dir(vec[0], vec[1], vec[2]))
    circle = gp_Circ(circle_origin, r)

    return BRepBuilderAPI_MakeEdge(circle).Edge()


def make_box_by_points(p1, p2, scale=1.0):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt

    if isinstance(p1, list) or isinstance(p1, tuple) or isinstance(p1, np.ndarray):
        deltas = [roundoff((p2_ - p1_) * scale) for p1_, p2_ in zip(p1, p2)]
        p1_in = [roundoff(x * scale) for x in p1]

    else:
        raise ValueError("Unknown input format {type(p1)}")

    dx = deltas[0]
    dy = deltas[1]
    dz = deltas[2]

    gp = gp_Pnt(p1_in[0], p1_in[1], p1_in[2])

    return BRepPrimAPI_MakeBox(gp, dx, dy, dz).Shape()


def make_cylinder(p, vec, h, r, t=None):
    """

    :param p:
    :param vec:
    :param h:
    :param r:
    :param t: Wall thickness (if applicable). Will make a
    :return:
    """
    cylinder_origin = gp_Ax2(gp_Pnt(p[0], p[1], p[2]), gp_Dir(vec[0], vec[1], vec[2]))
    cylinder = BRepPrimAPI_MakeCylinder(cylinder_origin, r, h).Shape()
    if t is not None:
        cutout = BRepPrimAPI_MakeCylinder(cylinder_origin, r - t, h).Shape()
        return BRepAlgoAPI_Cut(cylinder, cutout).Shape()
    else:
        return cylinder


def make_cylinder_from_points(p1, p2, r, t=None):
    vec = unit_vector(np.array(p2) - np.array(p1))
    l = vector_length(np.array(p2) - np.array(p1))
    return make_cylinder(p1.astype(float), vec.astype(float), l, r, t)


def make_sphere(pnt, radius):
    """
    Create a sphere using coordinates (x,y,z) and radius.

    :param pnt: Point
    :param radius: Radius
    """
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCC.Core.gp import gp_Pnt

    aPnt1 = gp_Pnt(float(pnt[0]), float(pnt[1]), float(pnt[2]))
    Sphere = BRepPrimAPI_MakeSphere(aPnt1, radius).Shape()
    return Sphere


def make_revolved_cylinder(pnt, height, revolve_angle, rotation, wall_thick):
    """
    This method demonstrates how to create a revolved shape from a drawn closed edge.
    It currently creates a hollow cylinder

    adapted from algotopia.com's opencascade_basic tutorial:
    http://www.algotopia.com/contents/opencascade/opencascade_basic

    :param pnt:
    :param height:
    :param revolve_angle:
    :param rotation:
    :param wall_thick:
    :type pnt: dict
    :type height: float
    :type revolve_angle: float
    :type rotation: float
    :type wall_thick: float
    """
    from OCC.Core.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt

    face_inner_radius = pnt["X"] + (17.0 - wall_thick / 2) * 1000
    face_outer_radius = pnt["X"] + (17.0 + wall_thick / 2) * 1000

    # point to create an edge from
    edg_points = [
        gp_Pnt(face_inner_radius, pnt["Y"], pnt["Z"]),
        gp_Pnt(face_inner_radius, pnt["Y"], pnt["Z"] + height),
        gp_Pnt(face_outer_radius, pnt["Y"], pnt["Z"] + height),
        gp_Pnt(face_outer_radius, pnt["Y"], pnt["Z"]),
        gp_Pnt(face_inner_radius, pnt["Y"], pnt["Z"]),
    ]

    # aggregate edges in wire
    hexwire = BRepBuilderAPI_MakeWire()

    for i in range(len(edg_points) - 1):
        hexedge = BRepBuilderAPI_MakeEdge(edg_points[i], edg_points[i + 1]).Edge()
        hexwire.Add(hexedge)

    hexwire_wire = hexwire.Wire()
    # face from wire
    hexface = BRepBuilderAPI_MakeFace(hexwire_wire).Face()
    revolve_axis = gp_Ax1(gp_Pnt(pnt["X"], pnt["Y"], pnt["Z"]), gp_Dir(0, 0, 1))
    # create revolved shape
    revolved_shape_ = BRepPrimAPI_MakeRevol(hexface, revolve_axis, np.radians(float(revolve_angle))).Shape()
    revolved_shape_ = rotate_shp_3_axis(revolved_shape_, revolve_axis, rotation)

    return revolved_shape_


def point3d(point) -> gp_Pnt:
    if len(point) == 3:
        return gp_Pnt(point[0], point[1], point[2])
    elif len(point) == 2:
        return gp_Pnt(point[0], point[1], 0)
    else:
        raise ValueError(f"Point {point} has {len(point)} dimensions, expected 2 or 3")


def make_edge(p1, p2) -> TopoDS_Edge:
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge

    res = BRepBuilderAPI_MakeEdge(point3d(p1), point3d(p2)).Edge()

    if res.IsNull():
        logger.debug("Edge creation returned None")

    return res


def make_ori_vector(
    name,
    origin,
    csys: list[list[float, float, float]] | Placement,
    pnt_r=0.02,
    cyl_l: Union[float, list, tuple] = 0.3,
    cyl_r=0.02,
    units="m",
    colors=("white", "BLUE", "GREEN", "RED"),
) -> "Part":
    """Visualize a local coordinate system with a sphere (origin) and 3 cylinders (axes)"""
    from ada import Part, PrimCyl, PrimSphere

    origin = np.array(origin)
    o_shape = PrimSphere(name + "_origin", origin, pnt_r, units=units, metadata=dict(origin=origin), color=colors[0])

    if type(cyl_l) in (list, tuple):
        cyl_l_x, cyl_l_y, cyl_l_z = cyl_l
    else:
        cyl_l_x, cyl_l_y, cyl_l_z = cyl_l, cyl_l, cyl_l

    xlen = np.array(csys[0]) * cyl_l_x
    ylen = np.array(csys[1]) * cyl_l_y
    zlen = np.array(csys[2]) * cyl_l_z

    if any(vector_length(x) == 0 for x in [xlen, ylen, zlen]):
        raise ValueError(f"Check your csys input. One of your directional vectors is a zero-vector {csys}")

    x_vec_shape = PrimCyl(
        name + "_X",
        origin,
        origin + xlen,
        cyl_r,
        units=units,
        color=colors[1],
    )

    y_vec_shape = PrimCyl(
        name + "_Y",
        origin,
        origin + ylen,
        cyl_r,
        units=units,
        color=colors[2],
    )

    z_vec_shape = PrimCyl(
        name + "_Z",
        origin,
        origin + zlen,
        cyl_r,
        units=units,
        color=colors[3],
    )
    return Part(name, units=units) / (o_shape, x_vec_shape, y_vec_shape, z_vec_shape)


def make_eq_plane_object(name, eq_plane: EquationOfPlane, p_dist=1, plane: Plane = None, colour="white") -> Part:
    from ada import Plate

    if plane is None:
        plane = Plane.XY

    csys = eq_plane.get_lcsys()
    points = eq_plane.get_points_in_lcsys_plane(p_dist=p_dist, plane=plane)
    ori_vec_model = make_ori_vector(name=name, origin=eq_plane.point_in_plane, csys=csys)

    ori_vec_model.add_plate(Plate("Surface", points, 0.001, use3dnodes=True, color=colour, opacity=0.3))
    return ori_vec_model


def get_edge_points(edge):
    from OCC.Extend.TopologyUtils import TopologyExplorer

    t = TopologyExplorer(edge)
    points = []
    for v in t.vertices():
        apt = BRep_Tool.Pnt(v)
        points.append((apt.X(), apt.Y(), apt.Z()))
    return points


def rotate_shp_3_axis(shape, revolve_axis, rotation):
    """
    Rotate a shape around a pre-defined rotation axis gp_Ax1.

    @param rotation : rotation in degrees around (gp_Ax1)
    @param shape : shape in question
    @param revolve_axis : rotation axis gp_Ax1
    @return : the rotated shape.
    """
    alpha = gp_Trsf()
    alpha.SetRotation(revolve_axis, np.deg2rad(rotation))
    brep_trns = BRepBuilderAPI_Transform(shape, alpha, False)
    shp = brep_trns.Shape()
    return shp


def compute_minimal_distance_between_shapes(shp1, shp2) -> BRepExtrema_DistShapeShape:
    """Compute the minimal distance between 2 shapes"""

    dss = BRepExtrema_DistShapeShape()
    dss.LoadS1(shp1)
    dss.LoadS2(shp2)
    dss.Perform()

    assert dss.IsDone()

    logger.info("Minimal distance between shapes: ", dss.Value())

    return dss


def make_circular_sec_wire(point: gp_Pnt, direction: gp_Dir, radius) -> TopoDS_Wire:
    circle = gp_Circ(gp_Ax2(point, direction), radius)
    profile_edge = BRepBuilderAPI_MakeEdge(circle).Edge()
    return BRepBuilderAPI_MakeWire(profile_edge).Wire()


def make_circular_sec_face(point: gp_Pnt, direction: gp_Dir, radius) -> TopoDS_Face:
    profile_wire = make_circular_sec_wire(point, direction, radius)
    return BRepBuilderAPI_MakeFace(profile_wire).Face()


def sweep_pipe(edge, xvec, r, wt, geom_repr=ElemType.SOLID):
    if geom_repr not in [ElemType.SOLID, ElemType.SHELL]:
        raise ValueError("Sweeping pipe must be either 'solid' or 'shell'")

    t = TopologyExplorer(edge)
    points = [v for v in t.vertices()]
    point = BRep_Tool.Pnt(points[0])
    # x, y, z = point.X(), point.Y(), point.Z()
    direction = gp_Dir(*unit_vector(xvec).astype(float).tolist())

    # pipe
    makeWire = BRepBuilderAPI_MakeWire()
    makeWire.Add(edge)
    makeWire.Build()
    wire = makeWire.Wire()
    try:
        if geom_repr == ElemType.SOLID:
            i = make_circular_sec_face(point, direction, r - wt)
            elbow_i = BRepOffsetAPI_MakePipe(wire, i).Shape()
            o = make_circular_sec_face(point, direction, r)
            elbow_o = BRepOffsetAPI_MakePipe(wire, o).Shape()
        else:
            elbow_i = None
            o = make_circular_sec_wire(point, direction, r)
            elbow_o = BRepOffsetAPI_MakePipe(wire, o).Shape()
    except RuntimeError as e:
        logger.error(f'Pipe sweep failed: "{e}"')
        return wire
    if geom_repr == ElemType.SOLID:
        boolean_result = BRepAlgoAPI_Cut(elbow_o, elbow_i).Shape()
        if boolean_result.IsNull():
            logger.debug("Boolean returns None")
    else:
        boolean_result = elbow_o

    return boolean_result


def sweep_geom(sweep_wire: TopoDS_Wire, wire_face: TopoDS_Wire):
    return BRepOffsetAPI_MakePipe(sweep_wire, wire_face).Shape()


def apply_booleans(geom: TopoDS_Shape, booleans: list[Boolean]) -> TopoDS_Shape:
    for boolean in booleans:
        if boolean.bool_op == BoolOpEnum.DIFFERENCE:
            geom = BRepAlgoAPI_Cut(geom, boolean.primitive.solid_occ()).Shape()
        elif boolean.bool_op == BoolOpEnum.UNION:
            geom = BRepAlgoAPI_Fuse(geom, boolean.primitive.solid_occ()).Shape()
        elif boolean.bool_op == BoolOpEnum.INTERSECTION:
            geom = BRepAlgoAPI_Common(geom, boolean.primitive.solid_occ()).Shape()
        else:
            raise NotImplementedError(f"Boolean operation {boolean.bool_op} not implemented")

    return geom


def segments_to_edges(segments) -> list[TopoDS_Edge]:
    from ada.api.curves import ArcSegment

    edges = []
    for seg in segments:
        if isinstance(seg, ArcSegment):
            a_arc_of_circle = GC_MakeArcOfCircle(
                point3d(seg.p1),
                point3d(seg.midpoint),
                point3d(seg.p2),
            )
            edges.append(BRepBuilderAPI_MakeEdge(a_arc_of_circle.Value()).Edge())
        else:
            edge = make_edge(seg.p1, seg.p2)
            edges.append(edge)

    return edges


def extrude_closed_wire(wire: TopoDS_Wire, origin, normal, height) -> TopoDS_Shape:
    """Extrude a closed wire into a solid"""
    p1 = origin + normal * height
    starting_point = gp_Pnt(origin[0], origin[1], origin[2])
    end_point = gp_Pnt(*p1.tolist())
    vec = gp_Vec(starting_point, end_point)

    solid = make_extrusion(make_face(wire), height, vec)

    return solid


def make_revolve_solid(face: TopoDS_Face, axis, angle, origin) -> TopoDS_Shape:
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeRevol

    revolve_axis = gp_Ax1(gp_Pnt(origin[0], origin[1], origin[2]), gp_Dir(axis[0], axis[1], axis[2]))
    revolved_shape = BRepPrimAPI_MakeRevol(face, revolve_axis, np.deg2rad(angle)).Shape()
    return revolved_shape


def transform_shape_to_pos(shape: TopoDS_Shape, location: Point, axis: Direction, ref_dir: Direction) -> TopoDS_Shape:
    # Create a transformation to move the extruded area solid to the correct position
    trsf_rot = gp_Trsf()

    # Rotate the extruded area solid around 0,0,0
    ax_global = gp_Ax3(gp_Pnt(*Point(0, 0, 0)), gp_Dir(*Direction(0, 0, 1)), gp_Dir(*Direction(1, 0, 0)))
    ax_local = gp_Ax3(gp_Pnt(*Point(0, 0, 0)), gp_Dir(*axis), gp_Dir(*ref_dir))
    trsf_rot.SetTransformation(ax_local, ax_global)
    shape1 = BRepBuilderAPI_Transform(shape, trsf_rot, True).Shape()

    # Translate the extruded area solid
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(*location))

    return BRepBuilderAPI_Transform(shape1, trsf, True).Shape()


def make_edges_and_fillet_from_3points_using_occ(start, center, end, radius):
    edge1 = make_edge(start[:3], center[:3])
    edge2 = make_edge(center[:3], end[:3])
    ed1, ed2, fillet = make_fillet(edge1, edge2, radius)
    return ed1, ed2, fillet


def make_arc_segment_using_occ(start, center, end, radius) -> list[LineSegment, ArcSegment, LineSegment]:
    from ada import ArcSegment, LineSegment

    if not isinstance(start, Point):
        start = Point(*start)
    if not isinstance(center, Point):
        center = Point(*center)
    if not isinstance(end, Point):
        end = Point(*end)

    dim = start.dim
    ed1, ed2, fillet = make_edges_and_fillet_from_3points_using_occ(start, center, end, radius)

    ed1_p = [x[:dim] for x in get_edge_points(ed1)]
    ed2_p = [x[:dim] for x in get_edge_points(ed2)]
    fil_p = [x[:dim] for x in get_edge_points(fillet)]
    midpoint = get_midpoint_of_arc(fillet)[:dim]
    l1 = LineSegment(*ed1_p, edge_geom=ed1)
    arc = ArcSegment(fil_p[0], fil_p[1], midpoint, radius, edge_geom=fillet)
    l2 = LineSegment(*ed2_p, edge_geom=ed2)

    return [l1, arc, l2]


def from_pointer(pointer: int) -> TopoDS_Shape:
    from OCC.Core.TopoDS import TopoDS_Shape

    return TopoDS_Shape(pointer)
