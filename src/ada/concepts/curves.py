from __future__ import annotations

from typing import TYPE_CHECKING, List, Union, Iterable, Optional

import numpy as np

from ada.concepts.nodes import Node
from ada.concepts.transforms import Placement
from ada.core.vector_utils import (
    normal_to_points_in_plane,
    unit_vector,
    is_clockwise,
    local_2_global_points,
    global_2_local_nodes,
)
from ada.core.curve_utils import build_polycurve, segments_to_indexed_lists
from ada.geom.placement import Direction
from ada.geom.points import Point
from ada.geom.surfaces import ArbitraryProfileDefWithVoids, ProfileType

if TYPE_CHECKING:
    from ada import Beam
    from ada.geom.curves import ArcLine, IndexedPolyCurve, Line


class CurveRevolve:
    def __init__(
            self, p1, p2, radius=None, rot_axis=None, point_on=None, rot_origin=None, angle=180, parent=None,
            metadata=None
    ):
        self._p1 = p1
        self._p2 = p2
        self._angle = angle
        self._radius = radius
        self._rot_axis = rot_axis
        self._parent = parent
        self._point_on = point_on
        self._rot_origin = rot_origin
        self._ifc_elem = None
        self.metadata = metadata if metadata is not None else dict()

        if self._point_on is not None:
            from ada.core.constants import O, X, Y, Z
            from ada.core.curve_utils import calc_arc_radius_center_from_3points
            from ada.core.vector_utils import (
                global_2_local_nodes,
                local_2_global_points,
            )

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

    @property
    def p1(self):
        return self._p1

    @property
    def p2(self):
        return self._p2

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
    def rot_axis(self):
        return self._rot_axis

    @property
    def rot_origin(self):
        return np.array(self._rot_origin)

    @property
    def parent(self) -> "Beam":
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value


class CurveSweep:
    """
    A closed curve defined by a list of points.

    :param points: Input of 2d points (x,y). Can include a 3rd value for assigning a radius to the point.
    :param origin: Origin of the curve
    :param normal: Local Normal direction
    :param xdir: Local X-Direction
    """

    def __init__(
            self,
            points: list[tuple[float, float, Optional[float]]],
            origin: Iterable | Point = None,
            normal: Iterable | Direction = None,
            xdir: Iterable | Direction = None,
            tol=1e-3,
            parent=None,
            debug=False,
    ):
        self._tol = tol
        self._parent = parent
        self._placement = Placement(origin, xdir=xdir, zdir=normal)
        self._debug = debug
        self._segments = None
        self._seg_index = None
        self._seg_global_points = None
        self._nodes = None

        points = self._points_fix(points)

        self._radiis = {i: x[-1] for i, x in enumerate(points) if len(x) == 3}
        self._points2d = [Point(*p[:2]) for p in points]
        self._points3d = self._from_2d_points(self._points2d)
        self._points_to_segments(points, tol)

    def _points_fix(self, points):
        # Check to see if the points are clockwise
        if is_clockwise(points) is False:
            points = [p for p in reversed(points)]
        return points

    @classmethod
    def from_3d_points(cls, points, flip_normal=False, **kwargs):

        normal = normal_to_points_in_plane([np.array(x[:3]) for x in points])
        if flip_normal:
            normal *= -1

        p1 = np.array(points[0][:3]).astype(float)
        p2 = np.array(points[1][:3]).astype(float)
        origin = p1
        xdir = unit_vector(p2 - p1)
        placement = Placement(origin, xdir=xdir, zdir=normal)
        csys = [placement.xdir, placement.ydir]
        points2d = global_2_local_nodes(csys, placement.origin, [np.array(x[:3]) for x in points])
        points = [x.p if type(x) is Node else x for x in points]
        for i, p in enumerate(points):
            if len(p) == 4:
                points2d[i] = (points2d[i][0], points2d[i][1], p[-1])
            else:
                points2d[i] = (points2d[i][0], points2d[i][1])

        return cls(points2d, origin=origin, xdir=xdir, normal=normal, **kwargs)

    def _from_2d_points(self, points2d: list[np.ndarray[float, float]]) -> list[Point]:
        place = self.placement
        return local_2_global_points(points2d, place.origin, place.xdir, place.zdir)

    def _points_to_segments(self, local_points2d, tol=1e-3):
        debug_name = self._parent.name if self._parent is not None else "PolyCurveDebugging"

        seg_list = build_polycurve(local_points2d, tol, self._debug, debug_name)
        origin, xdir, normal = self.placement.origin, self.placement.xdir, self.placement.zdir
        # Convert from local to global coordinates
        for i, seg in enumerate(seg_list):
            if type(seg) is ArcSegment:
                lpoints = [seg.p1, seg.p2, seg.midpoint]
                gp = local_2_global_points(lpoints, origin, xdir, normal)
                seg.p1 = gp[0]
                seg.p2 = gp[1]
                seg.midpoint = gp[2]
            else:
                lpoints = [seg.p1, seg.p2]
                gp = local_2_global_points(lpoints, origin, xdir, normal)
                seg.p1 = gp[0]
                seg.p2 = gp[1]

        self._segments = seg_list
        self._seg_global_points, self._seg_index = segments_to_indexed_lists(seg_list)
        self._nodes = [
            Node(p, r=self.radiis[i]) if i in self.radiis.keys() else Node(p) for i, p in enumerate(self._points3d)
        ]

    def make_extruded_solid(self, height: float):
        from ada.occ.utils import extrude_closed_wire

        return extrude_closed_wire(self.wire(), self.placement.origin, self.placement.zdir, height)

    def make_revolve_solid(self, axis, angle, origin):
        from ada.occ.utils import make_revolve_solid

        return make_revolve_solid(self.face(), axis, angle, origin)

    def scale(self, scale_factor, tol):
        self.placement.origin = np.array([x * scale_factor for x in self.placement.origin])
        self._points2d = [Point(*[x * scale_factor for x in p]) for p in self._points2d]
        self._points3d = [Point(*[x * scale_factor for x in p]) for p in self._points3d]
        self._points_to_segments(self.points2d, tol=tol)

    @property
    def placement(self) -> Placement:
        return self._placement

    @placement.setter
    def placement(self, value: Placement):
        self._placement = value
        points3d = local_2_global_points(self.points2d, self.placement.origin, self.placement.xdir, self.placement.zdir)
        self._points3d = points3d
        self._points_to_segments(self.points2d, tol=self._tol)

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
    def normal(self):
        return self.placement.zdir

    @property
    def xdir(self):
        return self.placement.xdir

    @property
    def ydir(self):
        return self.placement.ydir

    def edges(self):
        from ada.occ.utils import segments_to_edges

        return segments_to_edges(self.segments)

    def get_edges_geom(self) -> IndexedPolyCurve | Line | ArcLine:
        from ada.geom.curves import ArcLine, IndexedPolyCurve, Line

        if len(self.segments) == 1:
            seg = self.segments[0]
            if isinstance(seg, ArcSegment):
                return ArcLine(seg.p1, seg.midpoint, seg.p2)
            else:
                return Line(seg.p1, seg.p2)
        segments = []
        for seg in self.segments:
            if isinstance(seg, ArcSegment):
                segments.append(ArcLine(seg.p1, seg.midpoint, seg.p2))
            else:
                segments.append(Line(seg.p1, seg.p2))

        return IndexedPolyCurve(segments)

    def wire(self):
        from ada.occ.utils import make_wire

        return make_wire(self.edges())

    @property
    def seg_index(self):
        return self._seg_index

    @property
    def segments(self) -> list[LineSegment | ArcSegment]:
        return self._segments

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value


class CurvePoly(CurveSweep):
    def __init__(self, points, origin: Iterable | Point = None, normal: Iterable | Direction = None,
                 xdir: Iterable | Direction = None, **kwargs):
        # Check to see if it is a closed curve
        super().__init__(points, origin, normal, xdir, **kwargs)

    def _points_fix(self, points):
        # Check to see if the points are clockwise
        if is_clockwise(points) is False:
            points = [points[0]] + [p for p in reversed(points[1:])]
        return points

    def get_face_geom(self) -> ArbitraryProfileDefWithVoids:
        outer_curve = self.get_edges_geom()
        return ArbitraryProfileDefWithVoids(ProfileType.AREA, outer_curve, [])

    def face(self):
        from ada.occ.geom.surfaces import make_profile_from_geom

        return make_profile_from_geom(self.get_face_geom())


class LineSegment:
    def __init__(self, p1, p2, edge_geom=None):
        self._p1 = p1
        self._p2 = p2
        self._edge_geom = edge_geom

    @property
    def p1(self) -> np.ndarray:
        if type(self._p1) is not np.ndarray:
            self._p1 = np.array(self._p1)
        return self._p1

    @p1.setter
    def p1(self, value):
        self._p1 = value

    @property
    def p2(self) -> np.ndarray:
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
    def edge_geom(self):
        return self._edge_geom

    def __repr__(self):
        return f"ArcSegment({self.p1}, {self.midpoint}, {self.p2})"
