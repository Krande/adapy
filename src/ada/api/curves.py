from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Optional

import numpy as np

from ada.api.nodes import Node
from ada.api.transforms import Placement
from ada.config import Config
from ada.core.curve_utils import (
    build_polycurve,
    calc_2darc_start_end_from_lines_radius,
    calc_arc_radius_center_from_3points,
    calc_center_from_start_end_radius,
    calculate_angle,
    segments3d_from_points3d,
    segments_to_indexed_lists,
    transform_2d_arc_segment_to_3d,
)
from ada.core.vector_transforms import global_2_local_nodes, local_2_global_points
from ada.core.vector_utils import is_clockwise, poly_area_from_list
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.geom.surfaces import ArbitraryProfileDef, ProfileType

if TYPE_CHECKING:
    from ada import Beam
    from ada.cad import ShapeHandle
    from ada.geom.curves import ArcLine, Edge, IndexedPolyCurve


class CurveRevolve:
    def __init__(
        self, p1, p2, radius=None, rot_axis=None, point_on=None, rot_origin=None, angle=None, parent=None, metadata=None
    ):
        if rot_axis is not None and not isinstance(rot_axis, Direction):
            rot_axis = Direction(rot_axis)

        self._p1 = p1
        self._p2 = p2
        self._angle = angle
        self._radius = radius
        self._rot_axis = rot_axis
        self._parent = parent
        self._point_on = point_on
        self._rot_origin = rot_origin
        self._ifc_elem = None
        self._profile_normal = None
        self._profile_perpendicular = None
        self.metadata = metadata if metadata is not None else dict()

        if self._point_on is not None:
            from ada.core.constants import O, X, Y, Z

            p1, p2 = self.p1, self.p2

            csys0 = [X, Y, Z]
            res = global_2_local_nodes(csys0, O, [p1, self._point_on, p2])
            lcenter, radius = calc_arc_radius_center_from_3points(res[0][:2], res[1][:2], res[2][:2])
            if True in np.isnan(lcenter) or np.isnan(radius):
                raise ValueError("Curve is not valid. Please check your input")
            res2 = local_2_global_points([lcenter], O, X, Z)
            center = res2[0]

            self._radius = radius
            self._rot_origin = center

            # Compute the profile frame here too — the BeamRevolve
            # solid-geom path reads ``profile_normal`` /
            # ``profile_perpendicular`` regardless of which branch
            # constructed the curve. Without these set the
            # downstream ``get_normalized()`` chain crashes when
            # the curve came in via the (p1, point_on, p2) form
            # (IFC import path). xvec is the radial direction from
            # rot_origin to p1 at the start of the arc; the
            # placement frame ties it to ``rot_axis``.
            xvec1 = Direction(center - p1).get_normalized()
            place = Placement(p1, xdir=xvec1, zdir=rot_axis)
            self._profile_normal = place.xdir
            self._profile_perpendicular = place.ydir
        else:
            if self._radius is not None and self._rot_origin is None:
                center1, center2 = calc_center_from_start_end_radius(p1, p2, self._radius)
                angle = calculate_angle(p1, p2, self._radius)

                center = center2
                # get normal direction of the section profile plane at the start and end points
                xvec1 = Direction(center - p1).get_normalized()
                place = Placement(p1, xdir=xvec1, zdir=rot_axis)
                self._profile_normal = place.xdir
                self._profile_perpendicular = place.ydir

                self._angle = np.rad2deg(angle)
                self._rot_origin = center

    @property
    def p1(self):
        return self._p1

    @property
    def p2(self):
        return self._p2

    @property
    def profile_normal(self):
        return self._profile_normal

    @property
    def profile_perpendicular(self):
        return self._profile_perpendicular

    @property
    def angle(self):
        return self._angle

    @property
    def radius(self):
        return self._radius

    @property
    def point_on(self):
        return self._point_on

    @property
    def rot_axis(self) -> Direction:
        return self._rot_axis

    @property
    def rot_origin(self) -> Point:
        return self._rot_origin

    @property
    def parent(self) -> "Beam":
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value


class CurveOpen2d:
    """A open curve defined by a list of points."""

    def __init__(
        self,
        points: list[tuple[float, float, Optional[float]]],
        origin: Iterable | Point = None,
        normal: Iterable | Direction = None,
        xdir: Iterable | Direction = None,
        tol=1e-3,
        parent=None,
        orientation: Placement = None,
    ):
        self._tol = tol
        self._parent = parent
        self._orientation = Placement(origin, xdir=xdir, zdir=normal) if orientation is None else orientation
        self._placement = Placement()
        self._segments = None
        self._seg_index = None
        self._seg_global_points = None
        self._nodes = None

        points = self._points_fix(points)

        self._radiis = {i: x[-1] for i, x in enumerate(points) if len(x) == 3}
        self._points2d = [Point(p[:2]) for p in points]
        self._points3d = [Point(x) for x in self._orientation.transform_local_points_back_to_global(self._points2d)]
        self._points_to_segments(points, tol)

    def _points_fix(self, points):
        # Check to see if the points are clockwise
        if is_clockwise(points) is False:
            points = [p for p in reversed(points)]
        return points

    @classmethod
    def from_3d_points(cls, points, tol=1e-3, xdir=None, parent=None, flip_n=False):
        points3d = np.array([p[:3] for p in points])
        place = Placement.from_co_linear_points(points3d, xdir=xdir, flip_n=flip_n)
        points2d = place.transform_global_points_to_local(points3d)
        radiis = {i: x for i, x in enumerate(points) if len(x) > 3}
        input_points = []
        for i, p in enumerate(points2d):
            r = radiis.get(i, None)
            if r is not None:
                input_points.append((points2d[i][0], points2d[i][1], r))
            else:
                input_points.append((points2d[i][0], points2d[i][1]))

        return cls(points2d, origin=place.origin, xdir=place.xdir, normal=place.zdir, tol=tol, parent=parent)

    def _from_2d_points(self, points2d: list[np.ndarray[float, float]]) -> list[Point]:
        place = self.orientation
        return local_2_global_points(points2d, place.origin, place.xdir, place.zdir)

    def _points_to_segments(self, local_points2d, tol=1e-3):
        debug_name = self._parent.name if self._parent is not None else "PolyCurveDebugging"

        seg_list2d = build_polycurve(local_points2d, tol, Config().general_debug, debug_name)
        seg_list3d = []
        abs_place = self.orientation
        # Convert from local to global coordinates
        for i, seg in enumerate(seg_list2d):
            if type(seg) is ArcSegment:
                seg3d = transform_2d_arc_segment_to_3d(seg, abs_place)
                seg_list3d.append(seg3d)
            else:
                lpoints = [seg.p1, seg.p2]
                gp = abs_place.transform_local_points_back_to_global(lpoints)
                seg_list3d.append(LineSegment(gp[0], gp[1]))

        self._segments = seg_list2d
        self._segments3d = seg_list3d
        self._seg_global_points, self._seg_index = segments_to_indexed_lists(seg_list3d)
        self._nodes = [
            Node(p, r=self.radiis[i]) if i in self.radiis.keys() else Node(p) for i, p in enumerate(self._points3d)
        ]

    def scale(self, scale_factor, tol):
        self.orientation.origin = np.array([x * scale_factor for x in self.orientation.origin])
        self._points2d = [Point(*[x * scale_factor for x in p]) for p in self._points2d]
        self._points3d = [Point(*x) for x in self.orientation.transform_local_points_to_global(self._points2d)]
        self._points_to_segments(self.points2d, tol=tol)

    def get_area(self) -> float:
        return poly_area_from_list(self.points2d)

    @property
    def orientation(self) -> Placement:
        return self._orientation

    @orientation.setter
    def orientation(self, value: Placement):
        self._orientation = value
        self._points3d = [Point(*x) for x in value.transform_local_points_to_global(self._points2d)]
        points = [(*p, self.radiis[i]) if i in self.radiis.keys() else tuple(p) for i, p in enumerate(self.points2d)]
        self._points_to_segments(points, tol=self._tol)

    @property
    def seg_global_points(self):
        return self._seg_global_points

    @property
    def radiis(self) -> dict[int, float]:
        return self._radiis

    @property
    def points2d(self) -> list[Point[float, float]]:
        return self._points2d

    @property
    def points3d(self) -> list[Point[float, float, float]]:
        return self._points3d

    @property
    def nodes(self) -> list[Node]:
        return self._nodes

    @property
    def origin(self) -> Point:
        return self.orientation.origin

    @property
    def normal(self) -> Direction:
        return self.orientation.zdir

    @property
    def xdir(self) -> Direction:
        return self.orientation.xdir

    @property
    def ydir(self) -> Direction:
        return self.orientation.ydir

    def occ_edges(self) -> list[ShapeHandle]:
        from ada.occ.utils import segments_to_edges

        return segments_to_edges(self.segments3d)

    def curve_geom(self, use_3d_segments=False) -> IndexedPolyCurve | Edge | ArcLine:
        from ada.geom.curves import ArcLine, Edge, IndexedPolyCurve

        poly_segments = self.segments3d if use_3d_segments else self.segments

        if len(poly_segments) == 1:
            seg = poly_segments[0]
            if isinstance(seg, ArcSegment):
                return ArcLine(seg.p1, seg.midpoint, seg.p2)
            else:
                return Edge(seg.p1, seg.p2)

        segments = []
        for seg in poly_segments:
            if isinstance(seg, ArcSegment):
                segments.append(ArcLine(seg.p1, seg.midpoint, seg.p2))
            else:
                segments.append(Edge(seg.p1, seg.p2))

        return IndexedPolyCurve(segments)

    def occ_wire(self) -> ShapeHandle:
        from ada.occ.utils import make_wire

        return make_wire(self.occ_edges())

    @property
    def seg_index(self):
        return self._seg_index

    @property
    def segments(self) -> list[LineSegment | ArcSegment]:
        return self._segments

    @property
    def segments3d(self) -> list[LineSegment | ArcSegment]:
        return self._segments3d

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value


class CurvePoly2d(CurveOpen2d):
    """A closed curve defined by a list of 2d points represented by line and arc segments."""

    def __init__(
        self,
        points2d,
        origin: Iterable | Point = None,
        normal: Iterable | Direction = None,
        xdir: Iterable | Direction = None,
        tol=1e-3,
        parent=None,
        orientation: Placement = None,
    ):
        # Check to see if it is a closed curve
        super().__init__(points2d, origin, normal, xdir, tol, parent, orientation)

    def _points_fix(self, points):
        # Check to see if the points are clockwise
        if is_clockwise(points) is False:
            points = [points[0]] + [p for p in reversed(points[1:])]
        return points

    @classmethod
    def from_fem_shell(cls, points3d, tol=1e-3, parent=None) -> CurvePoly2d:
        """Fast constructor for flat FEM shell elements (3- or 4-gon, no arcs/radii).

        Geometrically equivalent to ``from_3d_points`` for a radius-free polygon,
        but it (a) computes the orientation once and injects it via
        :meth:`Placement.from_dirs_precomputed`, so the computed-placement LRU is
        never touched (it thrashes on per-element placements), and (b) builds the
        closed line loop directly instead of running ``build_polycurve`` /
        ``SegCreator``. Used only by the FEM shell -> Plate conversion; the general
        ``from_3d_points`` path (arcs, fillets, radii) is unchanged.
        """
        from ada.core.vector_transforms import (
            compute_orientation_vec,
            normal_to_points_in_plane,
            transform_3x3,
        )
        from ada.core.vector_utils import calc_yvec

        pts = np.array([p[:3] for p in points3d], dtype=float)
        if pts.shape[0] < 3:
            raise ValueError("At least three points are required to define a shell plate.")
        origin = pts[0]

        # Orientation -- mirrors Placement.from_co_linear_points feeding the
        # xdir/zdir-only orientation that CurveOpen2d builds. compute_orientation_vec
        # is the pure function the LRU wraps, so calling it directly is byte-for-byte
        # identical to the general path yet never touches get_computed_placement_cached.
        n = normal_to_points_in_plane(pts)
        xdir_raw = Direction(pts[1] - pts[0]).get_normalized()
        ydir_raw = Direction(calc_yvec(xdir_raw, n)).get_normalized()
        x1, y1, z1 = compute_orientation_vec(
            tuple(map(float, xdir_raw)), tuple(map(float, ydir_raw)), tuple(map(float, n))
        )
        rot1 = np.array([x1, y1, z1])

        # final orientation == Placement(origin, xdir=P1.xdir, zdir=P1.zdir)
        xf, yf, zf = compute_orientation_vec(x1, None, z1)
        xf, yf, zf = Direction(*xf), Direction(*yf), Direction(*zf)
        orientation = Placement.from_dirs_precomputed(origin, xf, yf, zf)
        rotf = np.array([xf, yf, zf])

        # 3D -> 2D using P1 (== transform_global_points_to_local)
        local = transform_3x3(rot1, pts - origin, inverse=False)[:, :2]

        # winding (CurvePoly2d._points_fix): keep first point, reverse the rest if CCW
        local_list = [p for p in local]
        if is_clockwise(local_list) is False:
            local_list = [local_list[0]] + list(reversed(local_list[1:]))

        points2d = [Point(p[0], p[1]) for p in local_list]
        # 3D points via the final orientation (== CurveOpen2d._points3d back-transform)
        back = transform_3x3(rotf, np.array([np.asarray(p) for p in points2d]), inverse=True) + origin
        points3d_pts = [Point(p) for p in back]

        # Segment order matches build_polycurve: closing edge first
        # ((p[-1]->p[0]), (p[0]->p[1]), ...), so seg_global_points / seg_index
        # come out identical to the general from_3d_points path.
        npts = len(points2d)
        segs2d = [LineSegment(points2d[i - 1], points2d[i]) for i in range(npts)]
        segs3d = [LineSegment(points3d_pts[i - 1], points3d_pts[i]) for i in range(npts)]
        seg_global_points, seg_index = segments_to_indexed_lists(segs3d)

        self = cls.__new__(cls)
        self._tol = tol
        self._parent = parent
        self._orientation = orientation
        self._placement = Placement()
        self._radiis = {}
        self._points2d = points2d
        self._points3d = points3d_pts
        self._segments = segs2d
        self._segments3d = segs3d
        self._seg_global_points = seg_global_points
        self._seg_index = seg_index
        self._nodes = [Node(p) for p in points3d_pts]
        return self

    def get_face_geom(self) -> ArbitraryProfileDef:
        outer_curve = self.curve_geom()
        return ArbitraryProfileDef(ProfileType.AREA, outer_curve, [])

    def face(self):
        from ada.occ.geom.surfaces import make_profile_from_geom

        return make_profile_from_geom(self.get_face_geom())

    def get_centroid(self):
        xyz_points = np.asarray([p for p in self.points3d])
        centroid = Point(np.sum(xyz_points, axis=0) / len(xyz_points))
        return centroid

    def copy_to(self, origin=None, xdir=None, n=None) -> CurvePoly2d:
        if origin is None:
            origin = self.origin

        return CurvePoly2d(
            [x for x in self.points2d], origin=origin, xdir=xdir, normal=n, tol=self._tol, parent=self._parent
        )


class CurveOpen3d:
    """A 3 dimensional open poly curve defined by a list of 3d points represented by line and arc segments."""

    def __init__(
        self,
        points3d,
        origin: Iterable | Point = None,
        normal: Iterable | Direction = None,
        xdir: Iterable | Direction = None,
        tol=1e-3,
        parent=None,
        orientation: Placement = None,
        radiis: dict[int, float] = None,
    ):
        self._radiis = {i: x[-1] for i, x in enumerate(points3d) if len(x) == 4} if radiis is None else radiis
        self._points3d = [Point(p[:3]) for p in points3d]

        self._segments = None
        self._tol = tol
        self._parent = parent
        self._orientation = Placement(origin, xdir=xdir, zdir=normal) if orientation is None else orientation

    def curve_geom(self) -> IndexedPolyCurve | Edge | ArcLine:
        from ada.geom.curves import ArcLine, Edge, IndexedPolyCurve

        poly_segments = self.segments

        if len(poly_segments) == 1:
            seg = poly_segments[0]
            if isinstance(seg, ArcSegment):
                return ArcLine(seg.p1, seg.midpoint, seg.p2)
            else:
                return Edge(seg.p1, seg.p2)

        segments = []
        for seg in poly_segments:
            if isinstance(seg, ArcSegment):
                segments.append(ArcLine(seg.p1, seg.midpoint, seg.p2))
            else:
                segments.append(Edge(seg.p1, seg.p2))

        return IndexedPolyCurve(segments)

    @property
    def segments(self) -> list[LineSegment | ArcSegment]:
        if self._segments is None:
            self._segments = segments3d_from_points3d(self._points3d, radius_dict=self._radiis)
        return self._segments

    @property
    def start_vector(self) -> Direction:
        seg0 = self.segments[0]
        if isinstance(seg0, ArcSegment):
            return seg0.s_normal
        else:
            return seg0.direction

    @property
    def orientation(self) -> Placement:
        return self._orientation

    @property
    def radiis(self) -> dict[int, float]:
        return self._radiis

    @property
    def points3d(self) -> list[Point]:
        return self._points3d

    def __repr__(self):
        return f"CurveOpen3d({self.points3d}, xdir={self.orientation.xdir}, zdir={self.orientation.zdir}, radiis={self.radiis})"


class LineSegment:
    def __init__(self, p1, p2, edge_geom=None, placement: Placement = None):
        self._p1 = p1 if isinstance(p1, Point) else Point(*p1)
        self._p2 = p2 if isinstance(p2, Point) else Point(*p2)
        self._edge_geom = edge_geom
        self._placement = placement
        self._direction = None

    @property
    def direction(self) -> Direction:
        if self._direction is None:
            self._direction = self.calc_direction()

        return self._direction

    @property
    def length(self) -> float:
        return self.direction.get_length()

    @property
    def p1(self) -> Point:
        return self._p1

    @p1.setter
    def p1(self, value: Iterable | Point):
        if not isinstance(value, Point):
            value = Point(*value)
        self._p1 = value
        self._direction = self.calc_direction()

    @property
    def p2(self) -> Point:
        return self._p2

    @p2.setter
    def p2(self, value):
        if not isinstance(value, Point):
            value = Point(*value)
        self._p2 = value
        self._direction = self.calc_direction()

    @property
    def edge_geom(self) -> ShapeHandle:
        return self._edge_geom

    @property
    def placement(self) -> Placement:
        return self._placement

    @placement.setter
    def placement(self, value: Placement):
        self._placement = value

    def calc_direction(self):
        return Direction(self.p2 - self.p1)

    def curve_geom(self) -> Edge:
        from ada.geom.curves import Edge

        return Edge(self.p1, self.p2)

    def __repr__(self):
        return f"LineSegment({self.p1}, {self.p2})"


class ArcSegment(LineSegment):
    def __init__(
        self,
        p1,
        p2,
        midpoint=None,
        radius=None,
        center=None,
        intersection=None,
        s_normal: Direction = None,
        e_normal: Direction = None,
        edge_geom=None,
    ):
        super(ArcSegment, self).__init__(p1, p2)
        if midpoint is not None and not isinstance(midpoint, Point):
            midpoint = Point(midpoint)
        if center is not None and not isinstance(center, Point):
            center = Point(center)

        self._midpoint = midpoint
        self._radius = radius
        self._center = center
        self._intersection = intersection
        self._edge_geom = edge_geom
        self._s_normal = s_normal
        self._e_normal = e_normal

    @staticmethod
    def from_start_center_end_radius(start, intersection_point, end, radius, tol=1e-3) -> ArcSegment:
        points = np.array([start, intersection_point, end])
        dim = points.shape[1]
        place = None
        if dim == 3:
            points3d = points.copy()
            place = Placement.from_co_linear_points(points3d)
            points2d = place.transform_global_points_to_local(points3d)
        else:
            points2d = points.copy()

        arc2d = calc_2darc_start_end_from_lines_radius(*points2d, radius, tol=tol)

        if place is not None:
            return transform_2d_arc_segment_to_3d(arc2d, place)

        return arc2d

    @property
    def midpoint(self):
        return self._midpoint

    @midpoint.setter
    def midpoint(self, value):
        self._midpoint = value

    @property
    def radius(self) -> float:
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
    def s_normal(self):
        """Start normal"""
        return self._s_normal

    @s_normal.setter
    def s_normal(self, value):
        self._s_normal = value

    @property
    def e_normal(self):
        """End normal"""
        return self._e_normal

    @e_normal.setter
    def e_normal(self, value):
        self._e_normal = value

    def curve_geom(self) -> ArcLine:
        from ada.geom.curves import ArcLine

        return ArcLine(self.p1, self.midpoint, self.p2)

    def __repr__(self):
        return f"ArcSegment({self.p1}, {self.midpoint}, {self.p2})"
