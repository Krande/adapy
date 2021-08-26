import logging
import math
import pathlib
from typing import Union

import numpy as np
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRep import BRep_Tool_Pnt
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepBndLib import brepbndlib_Add
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCC.Core.BRepFill import BRepFill_Filling
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakePipe
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.ChFi2d import ChFi2d_AnaFilletAlgo
from OCC.Core.GeomAbs import GeomAbs_C0

# Check to see if loading all this on the top affects speed negatively in usability terms
from OCC.Core.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec
from OCC.Core.Tesselator import ShapeTesselator
from OCC.Core.TopoDS import (
    TopoDS_Compound,
    TopoDS_Edge,
    TopoDS_Shape,
    TopoDS_Shell,
    TopoDS_Solid,
    TopoDS_Vertex,
    TopoDS_Wire,
)
from OCC.Extend.DataExchange import read_step_file
from OCC.Extend.ShapeFactory import make_wire
from OCC.Extend.TopologyUtils import TopologyExplorer

from ..core.utils import roundoff, unit_vector, vector_length


def extract_shapes(step_path, scale, transform, rotate):
    shapes = []

    cad_file_path = pathlib.Path(step_path)
    if cad_file_path.is_file():
        shapes += extract_subshapes(read_step_file(str(cad_file_path)))
    elif cad_file_path.is_dir():
        shapes += walk_shapes(cad_file_path)
    else:
        raise Exception(f'step_ref "{step_path}" does not represent neither file or folder found on system')

    shapes = [transform_shape(s, scale, transform, rotate) for s in shapes]
    return shapes


def transform_shape(shp_, scale, transform, rotate):
    trsf = gp_Trsf()
    if scale is not None:
        trsf.SetScaleFactor(scale)
    if transform is not None:
        trsf.SetTranslation(gp_Vec(transform[0], transform[1], transform[2]))
    if rotate is not None:
        pt = gp_Pnt(rotate[0][0], rotate[0][1], rotate[0][2])
        dire = gp_Dir(rotate[1][0], rotate[1][1], rotate[1][2])
        revolve_axis = gp_Ax1(pt, dire)
        trsf.SetRotation(revolve_axis, math.radians(rotate[2]))
    return BRepBuilderAPI_Transform(shp_, trsf, True).Shape()


def walk_shapes(dir_path):
    from ..core.utils import get_list_of_files

    shps = []
    for stp_file in get_list_of_files(dir_path, ".stp"):
        shps += extract_subshapes(read_step_file(stp_file))
    return shps


def extract_subshapes(shp_):
    s = []
    t = TopologyExplorer(shp_)
    for solid in t.solids():
        s.append(solid)
    return s


def occ_shape_to_faces(shape, quality=1.0, render_edges=False, parallel=True):
    """

    :param shape:
    :param quality:
    :param render_edges:
    :param parallel:
    :return:
    """
    # first, compute the tesselation
    tess = ShapeTesselator(shape)
    tess.Compute(compute_edges=render_edges, mesh_quality=quality, parallel=parallel)

    # get vertices and normals
    vertices_position = tess.GetVerticesPositionAsTuple()
    number_of_triangles = tess.ObjGetTriangleCount()
    number_of_vertices = len(vertices_position)

    # number of vertices should be a multiple of 3
    if number_of_vertices % 3 != 0:
        raise AssertionError("Wrong number of vertices")
    if number_of_triangles * 9 != number_of_vertices:
        raise AssertionError("Wrong number of triangles")

    # then we build the vertex and faces collections as numpy ndarrays
    np_vertices = np.array(vertices_position, dtype="float32").reshape(int(number_of_vertices / 3), 3)
    # Note: np_faces is just [0, 1, 2, 3, 4, 5, ...], thus arange is used
    np_faces = np.arange(np_vertices.shape[0], dtype="uint32")

    return np_vertices, np_faces


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


def get_boundingbox(shape: TopoDS_Shape, tol=1e-6, use_mesh=True):
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
        mesh.SetParallel(True)
        mesh.SetShape(shape)
        mesh.Perform()
        if not mesh.IsDone():
            raise AssertionError("Mesh not done.")
    brepbndlib_Add(shape, bbox, use_mesh)

    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    return xmin, ymin, zmin, xmax, ymax, zmax, xmax - xmin, ymax - ymin, zmax - zmin


def is_occ_shape(shp):
    """

    :param shp:
    :return:
    """
    if type(shp) in [
        TopoDS_Shell,
        TopoDS_Vertex,
        TopoDS_Solid,
        TopoDS_Wire,
        TopoDS_Shape,
        TopoDS_Compound,
    ]:
        return True
    else:
        return False


def face_to_wires(face):
    topo_exp = TopologyExplorer(face)
    wires = list()
    for w in topo_exp.wires_from_face(face):
        wires.append(w)
    return wires


def make_fillet(edge1, edge2, bend_radius):
    from ..core.utils import normal_to_points_in_plane

    f = ChFi2d_AnaFilletAlgo()

    points1 = get_points_from_edge(edge1)
    points2 = get_points_from_edge(edge2)
    normal = normal_to_points_in_plane([np.array(x) for x in points1] + [np.array(x) for x in points2])
    plane_normal = gp_Dir(gp_Vec(normal[0], normal[1], normal[2]))

    t = TopologyExplorer(edge1)
    apt = None
    for v in t.vertices():
        apt = BRep_Tool_Pnt(v)

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


def get_points_from_edge(edge):
    texp1 = TopologyExplorer(edge)
    points = []
    for v in texp1.vertices():
        apt = BRep_Tool_Pnt(v)
        points.append((apt.X(), apt.Y(), apt.Z()))
    return points


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


def make_n_sided(edges: [TopoDS_Edge]):
    """
    builds an n-sided patch, respecting the constraints defined by *edges*
    and *points*
    a simplified call to the BRepFill_Filling class
    its simplified in the sense that to all constraining edges and points
    the same level of *continuity* will be applied
    *continuity* represents:
    GeomAbs_C0 : the surface has to pass by 3D representation of the edge
    GeomAbs_G1 : the surface has to pass by 3D representation of the edge
    and to respect tangency with the given face
    GeomAbs_G2 : the surface has to pass by 3D representation of the edge
    and to respect tangency and curvature with the given face.
    NOTE: it is not required to set constraining points.
    just leave the tuple or list empty

    :param edges: the constraining edges
    :return: TopoDS_Face
    """

    n_sided = BRepFill_Filling()
    for edg in edges:
        n_sided.Add(edg, GeomAbs_C0)
    n_sided.Build()
    face = n_sided.Face()
    return face


def make_face_w_cutout(face, wire_cutout):
    """

    :param face:
    :param wire_cutout:
    :return:
    """
    wire_cutout.Reverse()
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace

    return BRepBuilderAPI_MakeFace(face, wire_cutout).Face()


def make_circle(p, vec, r):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    circle_origin = gp_Ax2(gp_Pnt(p[0], p[1], p[2]), gp_Dir(vec[0], vec[1], vec[2]))
    circle = gp_Circ(circle_origin, r)

    return BRepBuilderAPI_MakeEdge(circle).Edge()


def make_box(origin_pnt, dx, dy, dz, sf=1.0):
    """
    The variable origin_pnt can be a dict with the format of {'X': XXX, 'Y': YYY , 'Z': ZZZ}, ADA Node object or
    a simple list, dx, dy and dz are floats.

    The origin_pnt represents the bottom corner of the box whereas dx, dy and dz are distances from that bottom
    corner point describing the entire volume.

    :param origin_pnt:
    :param dx:
    :param dy:
    :param dz:
    :param sf: Scale Factor
    :type dx: float
    :type dy: float
    :type dz: float

    """
    from ada import Node

    if type(origin_pnt) is Node:
        assert isinstance(origin_pnt, Node)
        aPnt1 = gp_Pnt(float(origin_pnt.x) * sf, float(origin_pnt.y) * sf, float(origin_pnt.z) * sf)
    elif type(origin_pnt) == dict:
        aPnt1 = gp_Pnt(
            float(origin_pnt["X"]) * sf,
            float(origin_pnt["Y"]) * sf,
            float(origin_pnt["Z"]) * sf,
        )
    elif type(origin_pnt) == list or type(origin_pnt) == tuple or type(origin_pnt) is np.ndarray:
        origin_pnt = [roundoff(x * sf) for x in list(origin_pnt)]
        aPnt1 = gp_Pnt(float(origin_pnt[0]), float(origin_pnt[1]), float(origin_pnt[2]))
    else:
        raise ValueError(f"Unknown input format {origin_pnt}")

    my_box = BRepPrimAPI_MakeBox(aPnt1, dx * sf, dy * sf, dz * sf).Shape()
    return my_box


def make_box_by_points(p1, p2, scale=1.0):
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt

    if type(p1) == list or type(p1) == tuple or type(p1) is np.ndarray:
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
    return make_cylinder(p1, vec, l, r, t)


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


def make_edge(p1, p2):
    """

    :param p1:
    :param p2:
    :type p1: tuple
    :type p2: tuple

    :return:
    :rtype: OCC.Core.TopoDS.TopoDS_Edge
    """
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.gp import gp_Pnt

    p1 = gp_Pnt(*[float(x) for x in p1[:3]])
    p2 = gp_Pnt(*[float(x) for x in p2[:3]])
    res = BRepBuilderAPI_MakeEdge(p1, p2).Edge()

    if res.IsNull():
        logging.debug("Edge creation returned None")

    return res


def make_ori_vector(name, origin, csys, pnt_r=0.2, cyl_l: Union[float, list, tuple] = 0.3, cyl_r=0.2, units="m"):
    """
    Visualize a local coordinate system with a sphere and 3 cylinders representing origin and.

    :param name:
    :param origin:
    :param csys: Coordinate system
    :param pnt_r:
    :param cyl_l:
    :type cyl_l: Union[float, list, tuple]
    :param cyl_r:
    :param units:
    :return:
    """
    from ada import Part, PrimCyl, PrimSphere

    origin = np.array(origin)
    o_shape = PrimSphere(name + "_origin", origin, pnt_r, units=units, metadata=dict(origin=origin))

    if type(cyl_l) in (list, tuple):
        cyl_l_x, cyl_l_y, cyl_l_z = cyl_l
    else:
        cyl_l_x, cyl_l_y, cyl_l_z = cyl_l, cyl_l, cyl_l

    x_vec_shape = PrimCyl(
        name + "_X",
        origin,
        origin + np.array(csys[0]) * cyl_l_x,
        cyl_r,
        units=units,
        colour="BLUE",
    )

    y_vec_shape = PrimCyl(
        name + "_Y",
        origin,
        origin + np.array(csys[1]) * cyl_l_y,
        cyl_r,
        units=units,
        colour="GREEN",
    )

    z_vec_shape = PrimCyl(
        name + "_Z",
        origin,
        origin + np.array(csys[2]) * cyl_l_z,
        cyl_r,
        units=units,
        colour="RED",
    )
    return Part(name, units=units) / (o_shape, x_vec_shape, y_vec_shape, z_vec_shape)


def visualize_elem_ori(elem):
    """

    :param elem:
    :type elem: ada.fem.Elem
    :return: ada.Shape
    """
    origin = (elem.nodes[-1].p + elem.nodes[0].p) / 2
    return make_ori_vector(
        f"elem{elem.id}_ori",
        origin,
        elem.fem_sec.csys,
        pnt_r=0.2,
        cyl_r=0.05,
        cyl_l=1.0,
        units=elem.fem_sec.section.units,
    )


def visualize_load(load, units="m", pnt_r=0.2, cyl_r=0.05, cyl_l_norm=1.5):
    """

    :param load:
    :param units:
    :param pnt_r:
    :param cyl_r:
    :param cyl_l_norm:
    :type load: ada.fem.Load
    :return:
    :rtype: ada.Part
    """
    from ada.core.constants import X, Y, Z

    csys = load.csys if load.csys is not None else [X, Y, Z]
    forces = np.array(load.forces[:3])
    forces_normalized = tuple(cyl_l_norm * (forces / max(abs(forces))))

    origin = load.fem_set.members[0].p

    return make_ori_vector(
        f"F_{load.name}_ori",
        origin,
        csys,
        pnt_r=pnt_r,
        cyl_r=cyl_r,
        cyl_l=forces_normalized,
        units=units,
    )


def get_edge_points(edge):
    from OCC.Core.BRep import BRep_Tool_Pnt
    from OCC.Extend.TopologyUtils import TopologyExplorer

    t = TopologyExplorer(edge)
    points = []
    for v in t.vertices():
        apt = BRep_Tool_Pnt(v)
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
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCC.Core.gp import gp_Trsf

    alpha = gp_Trsf()
    alpha.SetRotation(revolve_axis, np.radians(rotation))
    brep_trns = BRepBuilderAPI_Transform(shape, alpha, False)
    shp = brep_trns.Shape()
    return shp


def compute_minimal_distance_between_shapes(shp1, shp2):
    """
    compute the minimal distance between 2 shapes

    :rtype: OCC.Core.BRepExtrema.BRepExtrema_DistShapeShape
    """
    from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape

    dss = BRepExtrema_DistShapeShape()
    dss.LoadS1(shp1)
    dss.LoadS2(shp2)
    dss.Perform()

    assert dss.IsDone()

    logging.info("Minimal distance between shapes: ", dss.Value())

    return dss


def make_sec_face(point, direction, radius):

    circle = gp_Circ(gp_Ax2(point, direction), radius)
    profile_edge = BRepBuilderAPI_MakeEdge(circle).Edge()
    profile_wire = BRepBuilderAPI_MakeWire(profile_edge).Wire()
    profile_face = BRepBuilderAPI_MakeFace(profile_wire).Face()
    return profile_face


def sweep_pipe(edge, xvec, r, wt):
    t = TopologyExplorer(edge)
    points = [v for v in t.vertices()]
    point = BRep_Tool_Pnt(points[0])
    # x, y, z = point.X(), point.Y(), point.Z()
    direction = gp_Dir(*unit_vector(xvec).astype(float).tolist())
    o = make_sec_face(point, direction, r)
    i = make_sec_face(point, direction, r - wt)

    # pipe
    makeWire = BRepBuilderAPI_MakeWire()
    makeWire.Add(edge)
    makeWire.Build()
    wire = makeWire.Wire()
    try:
        elbow_o = BRepOffsetAPI_MakePipe(wire, o).Shape()
        elbow_i = BRepOffsetAPI_MakePipe(wire, i).Shape()
    except RuntimeError as e:
        logging.error(f'Elbow creation failed: "{e}"')
        return wire

    boolean_result = BRepAlgoAPI_Cut(elbow_o, elbow_i).Shape()
    if boolean_result.IsNull():
        logging.debug("Boolean returns None")
    return boolean_result


def build_polycurve_occ(local_points, input_2d_coords=False, tol=1e-3):
    """

    :param local_points:
    :param input_2d_coords:
    :return: List of segments
    """
    from ada import ArcSegment, LineSegment

    if input_2d_coords:
        local_points = [(x[0], x[1], 0.0) if len(x) == 2 else (x[0], x[1], 0.0, x[2]) for x in local_points]

    edges = []
    pzip = list(zip(local_points[:-1], local_points[1:]))
    segs = [[p1, p2] for p1, p2 in pzip]
    segs += [segs[0]]
    segzip = list(zip(segs[:-1], segs[1:]))
    seg_list = []
    for i, (seg1, seg2) in enumerate(segzip):
        p11, p12 = seg1
        p21, p22 = seg2

        if i == 0:
            edge1 = make_edge(p11[:3], p12[:3])
        else:
            edge1 = edges[-1]
        if i == len(segzip) - 1:
            endp = seg_list[0].midpoint if type(seg_list[0]) is ArcSegment else seg_list[0].p2
            edge2 = make_edge(seg_list[0].p1, endp)
        else:
            edge2 = make_edge(p21[:3], p22[:3])

        if len(p21) > 3:
            r = p21[-1]

            tseg1 = get_edge_points(edge1)
            tseg2 = get_edge_points(edge2)

            l1_start = tseg1[0]
            l2_end = tseg2[1]

            ed1, ed2, fillet = make_fillet(edge1, edge2, r)

            seg1 = get_edge_points(ed1)
            seg2 = get_edge_points(ed2)
            arc_start = seg1[1]
            arc_end = seg2[0]
            midpoint = get_midpoint_of_arc(fillet)

            if i == 0:
                edges.append(ed1)
                seg_list.append(LineSegment(p1=l1_start, p2=arc_start))

            seg_list[-1].p2 = arc_start
            edges.append(fillet)

            seg_list.append(ArcSegment(p1=arc_start, p2=arc_end, midpoint=midpoint))
            if i == len(segzip) - 1:
                seg_list[0].p1 = arc_end
                edges[0] = ed2
            else:
                edges.append(ed2)
                seg_list.append(LineSegment(p1=arc_end, p2=l2_end))
        else:
            if i == 0:
                edges.append(edge1)
                seg_list.append(LineSegment(p1=p11, p2=p12))
            if i < len(segzip) - 1:
                edges.append(edge2)
                seg_list.append(LineSegment(p1=p21, p2=p22))
    return seg_list
