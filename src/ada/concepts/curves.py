import numpy as np

from .points import Node


class CurveRevolve:
    def __init__(
        self,
        curve_type,
        p1,
        p2,
        radius=None,
        rot_axis=None,
        point_on=None,
        rot_origin=None,
        parent=None,
    ):
        self._p1 = p1
        self._p2 = p2
        self._type = curve_type
        self._radius = radius
        self._rot_axis = rot_axis
        self._parent = parent
        self._point_on = point_on
        self._rot_origin = rot_origin

        if self._point_on is not None:
            from ada.core.constants import O, X, Y, Z
            from ada.core.curve_utils import calc_arc_radius_center_from_3points
            from ada.core.utils import global_2_local_nodes, local_2_global_nodes

            p1, p2 = self.p1, self.p2

            csys0 = [X, Y, Z]
            res = global_2_local_nodes(csys0, O, [p1, self._point_on, p2])
            lcenter, radius = calc_arc_radius_center_from_3points(res[0][:2], res[1][:2], res[2][:2])
            if True in np.isnan(lcenter) or np.isnan(radius):
                raise ValueError("Curve is not valid. Please check your input")
            res2 = local_2_global_nodes([lcenter], O, X, Z)
            center = res2[0]
            self._radius = radius
            self._rot_origin = center

    def edit(self, parent=None):
        if parent is not None:
            self._parent = parent

    @property
    def type(self):
        return self._type

    @property
    def p1(self):
        return self._p1

    @property
    def p2(self):
        return self._p2

    @property
    def radius(self):
        return self._radius

    @property
    def point_on(self):
        return self._point_on

    @property
    def rot_axis(self):
        return self._rot_axis

    @property
    def rot_origin(self):
        return np.array(self._rot_origin)

    @property
    def parent(self):
        """

        :return:
        :rtype: ada.Beam
        """
        return self._parent


class CurvePoly:
    """
    TODO: Simplify this class.

    :param points3d:
    :param points2d: Input of points
    :param origin: Origin of Polycurve (only applicable if using points2D)
    :param normal: Local Normal direction (only applicable if using points2D)
    :param xdir: Local X-Direction (only applicable if using points2D)
    :param flip_normal:
    """

    def __init__(
        self,
        points2d=None,
        origin=None,
        normal=None,
        xdir=None,
        points3d=None,
        flip_normal=False,
        tol=1e-3,
        is_closed=True,
        parent=None,
        debug=False,
    ):
        self._tol = tol
        self._parent = parent
        self._is_closed = is_closed
        self._debug = debug

        from ada.core.utils import (
            clockwise,
            global_2_local_nodes,
            local_2_global_nodes,
            normal_to_points_in_plane,
            unit_vector,
        )

        if points2d is None and points3d is None:
            raise ValueError("Either points2d or points3d must be set")

        if points2d is not None:
            if origin is None or normal is None or xdir is None:
                raise ValueError("You must supply origin, xdir and normal when passing in 2d points")
            points2d_no_r = [n[:2] for n in points2d]
            points3d = local_2_global_nodes(points2d_no_r, origin, xdir, normal)
            for i, p in enumerate(points2d):
                if len(p) == 3:
                    points3d[i] = (
                        points3d[i][0],
                        points3d[i][1],
                        points3d[i][2],
                        p[-1],
                    )
                else:
                    points3d[i] = tuple(points3d[i].tolist())
            self._xdir = xdir
            self._normal = np.array(normal)
            self._origin = np.array(origin).astype(float)
            self._ydir = np.cross(self._normal, self._xdir)
        else:
            self._normal = normal_to_points_in_plane([np.array(x[:3]) for x in points3d])
            self._origin = np.array(points3d[0][:3]).astype(float)
            self._xdir = unit_vector(np.array(points3d[1][:3]) - np.array(points3d[0][:3]))
            self._ydir = np.cross(self._normal, self._xdir)
            csys = [self._xdir, self._ydir]
            points2d = global_2_local_nodes(csys, self._origin, [np.array(x[:3]) for x in points3d])
            points3d = [x.p if type(x) is Node else x for x in points3d]
            for i, p in enumerate(points3d):
                if len(p) == 4:
                    points2d[i] = (points2d[i][0], points2d[i][1], p[-1])
                else:
                    points2d[i] = (points2d[i][0], points2d[i][1])

        if clockwise(points2d) is False:
            if is_closed:
                points2d = [points2d[0]] + [p for p in reversed(points2d[1:])]
                points3d = [points3d[0]] + [p for p in reversed(points3d[1:])]
            else:
                points2d = [p for p in reversed(points2d)]
                points3d = [p for p in reversed(points3d)]

        self._points3d = points3d
        self._points2d = points2d

        if flip_normal:
            self._normal *= -1

        self._seg_list = None
        self._seg_index = None
        self._face = None
        self._wire = None
        self._edges = None
        self._seg_global_points = None
        self._nodes = None
        self._ifc_elem = None
        self._local2d_to_polycurve(points2d, tol)

    def _generate_ifc_elem(self):
        a = self.parent.parent.get_assembly()
        f = a.ifc_file

        ifc_segments = []
        for seg_ind in self.seg_index:
            if len(seg_ind) == 2:
                ifc_segments.append(f.createIfcLineIndex(seg_ind))
            elif len(seg_ind) == 3:
                ifc_segments.append(f.createIfcArcIndex(seg_ind))
            else:
                raise ValueError("Unrecognized number of values")

        # TODO: Investigate using 2DLists instead is it could reduce complexity?
        # ifc_point_list = ifcfile.createIfcCartesianPointList2D(points)
        points = [tuple(x.astype(float).tolist()) for x in self.seg_global_points]
        ifc_point_list = f.createIfcCartesianPointList3D(points)
        segindex = f.createIfcIndexedPolyCurve(ifc_point_list, ifc_segments)
        return segindex

    def _segments_2_edges(self, segments):
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCC.Core.GC import GC_MakeArcOfCircle
        from OCC.Core.gp import gp_Pnt

        from ada.occ.utils import make_edge

        edges = []
        for seg in segments:
            if type(seg) is ArcSegment:
                aArcOfCircle = GC_MakeArcOfCircle(
                    gp_Pnt(*list(seg.p1)),
                    gp_Pnt(*list(seg.midpoint)),
                    gp_Pnt(*list(seg.p2)),
                )
                aEdge2 = BRepBuilderAPI_MakeEdge(aArcOfCircle.Value()).Edge()
                edges.append(aEdge2)
            else:
                edge = make_edge(seg.p1, seg.p2)
                edges.append(edge)

        return edges

    def _local2d_to_polycurve(self, local_points2d, tol=1e-3):
        """

        :param local_points2d:
        :param tol:
        :return:
        """
        from ada.core.curve_utils import build_polycurve, segments_to_indexed_lists
        from ada.core.utils import local_2_global_nodes

        debug_name = self._parent.name if self._parent is not None else "PolyCurveDebugging"

        seg_list = build_polycurve(local_points2d, tol, self._debug, debug_name)

        # # Convert from local to global coordinates
        for i, seg in enumerate(seg_list):
            if type(seg) is ArcSegment:
                lpoints = [seg.p1, seg.p2, seg.midpoint]
                gp = local_2_global_nodes(lpoints, self.origin, self.xdir, self.normal)
                seg.p1 = gp[0]
                seg.p2 = gp[1]
                seg.midpoint = gp[2]
            else:
                lpoints = [seg.p1, seg.p2]
                gp = local_2_global_nodes(lpoints, self.origin, self.xdir, self.normal)
                seg.p1 = gp[0]
                seg.p2 = gp[1]

        self._seg_list = seg_list
        self._seg_global_points, self._seg_index = segments_to_indexed_lists(seg_list)
        self._nodes = [Node(p) if len(p) == 3 else Node(p[:3], r=p[3]) for p in self._points3d]

    def make_extruded_solid(self, height):
        """

        :param height:
        :return:
        """
        from OCC.Core.gp import gp_Pnt, gp_Vec
        from OCC.Extend.ShapeFactory import make_extrusion, make_face

        p1 = self.origin + self.normal * height
        olist = self.origin
        starting_point = gp_Pnt(olist[0], olist[1], olist[2])
        end_point = gp_Pnt(*p1.tolist())
        vec = gp_Vec(starting_point, end_point)

        solid = make_extrusion(make_face(self.wire), height, vec)

        return solid

    def make_revolve_solid(self, axis, angle, origin):
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeRevol
        from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt

        revolve_axis = gp_Ax1(gp_Pnt(origin[0], origin[1], origin[2]), gp_Dir(axis[0], axis[1], axis[2]))
        face = self.face
        revolved_shape_ = BRepPrimAPI_MakeRevol(face, revolve_axis, np.deg2rad(angle)).Shape()
        return revolved_shape_

    def make_shell(self):
        from OCC.Core.BRepFill import BRepFill_Filling
        from OCC.Core.GeomAbs import GeomAbs_C0

        n_sided = BRepFill_Filling()
        for edg in self.edges:
            n_sided.Add(edg, GeomAbs_C0)
        n_sided.Build()
        face = n_sided.Face()
        return face

    def calc_bbox(self, thick):
        """
        Calculate the Bounding Box of the plate

        :return: Bounding Box of the plate
        :rtype: tuple
        """
        xs = []
        ys = []
        zs = []

        for pt in self.nodes:
            xs.append(pt.x)
            ys.append(pt.y)
            zs.append(pt.z)

        bbox_min = np.array([min(xs), min(ys), min(zs)]).astype(np.float64)
        bbox_max = np.array([max(xs), max(ys), max(zs)]).astype(np.float64)
        n = self.normal.astype(np.float64)

        pv = np.nonzero(n)[0]
        matr = {0: "X", 1: "Y", 2: "Z"}
        orient = matr[pv[0]]
        if orient == "X" or orient == "Y":
            delta_vec = abs(n * thick / 2.0)
            bbox_min -= delta_vec
            bbox_max += delta_vec
        elif orient == "Z":
            delta_vec = abs(n * thick).astype(np.float64)
            bbox_min -= delta_vec

        else:
            raise ValueError(f"Error in {orient}")

        return tuple([(x, y) for x, y in zip(list(bbox_min), list(bbox_max))])

    def scale(self, scale_factor, tol):
        self._origin = np.array([x * scale_factor for x in self.origin])
        self._points2d = [tuple([x * scale_factor for x in p]) for p in self._points2d]
        self._points3d = [tuple([x * scale_factor for x in p]) for p in self._points3d]
        self._local2d_to_polycurve(self.points2d, tol=tol)

    @property
    def origin(self):
        return self._origin

    @origin.setter
    def origin(self, value):
        from ada.core.utils import local_2_global_nodes

        self._origin = value
        points2d_no_r = [n[:2] for n in self.points2d]
        points3d = local_2_global_nodes(points2d_no_r, self._origin, self.xdir, self.normal)
        for i, p in enumerate(self.points2d):
            if len(p) == 3:
                points3d[i] = (points3d[i][0], points3d[i][1], points3d[i][2], p[-1])
            else:
                points3d[i] = tuple(points3d[i].tolist())
        self._points3d = points3d
        self._local2d_to_polycurve(self.points2d, tol=self._tol)

    @property
    def seg_global_points(self):
        return self._seg_global_points

    @property
    def points2d(self):
        return self._points2d

    @property
    def points3d(self):
        return self._points3d

    @property
    def nodes(self):
        return self._nodes

    @property
    def normal(self):
        return self._normal

    @property
    def xdir(self):
        return self._xdir

    @property
    def ydir(self):
        return self._ydir

    @property
    def edges(self):
        # if self._edges is None:
        #     self._edges = self._segments_2_edges(self.seg_list)
        return self._segments_2_edges(self.seg_list)

    @property
    def wire(self):
        from OCC.Extend.ShapeFactory import make_wire

        # if self._wire is None:
        #     self._wire = make_wire(self.edges)
        return make_wire(self.edges)

    @property
    def face(self):
        # if self._face is None:
        #     self._face = self.make_shell()
        return self.make_shell()

    @property
    def seg_index(self):
        return self._seg_index

    @property
    def seg_list(self):
        return self._seg_list

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_elem()
        return self._ifc_elem


class LineSegment:
    def __init__(self, p1, p2, edge_geom=None):
        self._p1 = p1
        self._p2 = p2
        self._edge_geom = edge_geom

    @property
    def p1(self):
        if type(self._p1) is not np.ndarray:
            self._p1 = np.array(self._p1)
        return self._p1

    @p1.setter
    def p1(self, value):
        self._p1 = value

    @property
    def p2(self):
        if type(self._p2) is not np.ndarray:
            self._p2 = np.array(self._p2)
        return self._p2

    @p2.setter
    def p2(self, value):
        self._p2 = value

    @property
    def edge_geom(self):
        return self._edge_geom

    def __repr__(self):
        return f"LineSegment({self.p1}, {self.p2})"


class ArcSegment(LineSegment):
    def __init__(self, p1, p2, midpoint=None, radius=None, center=None, intersection=None, edge_geom=None):
        super(ArcSegment, self).__init__(p1, p2)
        self._midpoint = midpoint
        self._radius = radius
        self._center = center
        self._intersection = intersection
        self._edge_geom = edge_geom

    @property
    def midpoint(self):
        return self._midpoint

    @midpoint.setter
    def midpoint(self, value):
        self._midpoint = value

    @property
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, value):
        self._radius = value

    @property
    def center(self):
        return self._center

    @property
    def intersection(self):
        return self._intersection

    @property
    def edge_geom(self):
        return self._edge_geom

    def __repr__(self):
        return f"ArcSegment({self.p1}, {self.midpoint}, {self.p2})"
