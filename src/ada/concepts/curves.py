from __future__ import annotations

from typing import TYPE_CHECKING, List, Union

import numpy as np

from .points import Node
from .transforms import Placement

if TYPE_CHECKING:
    from ada import Beam


class CurveRevolve:
    def __init__(
        self, p1, p2, radius=None, rot_axis=None, point_on=None, rot_origin=None, angle=180, parent=None, metadata=None
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

    def _generate_ifc_elem(self):
        from ada.ifc.utils import create_ifcrevolveareasolid, create_local_placement

        parent_beam = self.parent

        a = parent_beam.get_assembly()
        f = a.ifc_file
        profile = parent_beam.section.ifc_profile

        global_placement = create_local_placement(f)
        return create_ifcrevolveareasolid(f, profile, global_placement, self.rot_origin, self.rot_axis, self.angle)

    def get_ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_elem()
        return self._ifc_elem

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

        from ada.core.vector_utils import (
            is_clockwise,
            normal_to_points_in_plane,
            unit_vector,
        )

        if points2d is None and points3d is None:
            raise ValueError("Either points2d or points3d must be set")

        if points2d is not None:
            self._placement = Placement(origin, xdir=xdir, zdir=normal)
            points3d = self._from_2d_points(points2d)
        else:
            normal = normal_to_points_in_plane([np.array(x[:3]) for x in points3d])
            p1 = np.array(points3d[0][:3]).astype(float)
            p2 = np.array(points3d[1][:3]).astype(float)
            origin = p1
            xdir = unit_vector(p2 - p1)
            self._placement = Placement(origin, xdir=xdir, zdir=normal)
            points2d = self._from_3d_points(points3d)

        if is_clockwise(points2d) is False:
            if is_closed:
                points2d = [points2d[0]] + [p for p in reversed(points2d[1:])]
                points3d = [points3d[0]] + [p for p in reversed(points3d[1:])]
            else:
                points2d = [p for p in reversed(points2d)]
                points3d = [p for p in reversed(points3d)]

        self._points3d = points3d
        self._points2d = points2d

        if flip_normal:
            self.placement.zdir *= -1

        self._seg_list = None
        self._seg_index = None
        self._face = None
        self._wire = None
        self._edges = None
        self._seg_global_points = None
        self._nodes = None
        self._ifc_elem = None
        self._local2d_to_polycurve(points2d, tol)

    def _from_2d_points(self, points2d) -> List[tuple]:
        from ada.core.vector_utils import local_2_global_points

        place = self.placement

        points2d_no_r = [n[:2] for n in points2d]
        points3d = local_2_global_points(points2d_no_r, place.origin, place.xdir, place.zdir)
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
        return points3d

    def _from_3d_points(self, points3d) -> List[tuple]:
        from ada.core.vector_utils import global_2_local_nodes

        csys = [self.placement.xdir, self.placement.ydir]
        points2d = global_2_local_nodes(csys, self.placement.origin, [np.array(x[:3]) for x in points3d])
        points3d = [x.p if type(x) is Node else x for x in points3d]
        for i, p in enumerate(points3d):
            if len(p) == 4:
                points2d[i] = (points2d[i][0], points2d[i][1], p[-1])
            else:
                points2d[i] = (points2d[i][0], points2d[i][1])
        return points2d

    def _local2d_to_polycurve(self, local_points2d, tol=1e-3):
        from ada.core.curve_utils import build_polycurve, segments_to_indexed_lists
        from ada.core.vector_utils import local_2_global_points

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

        self._seg_list = seg_list
        self._seg_global_points, self._seg_index = segments_to_indexed_lists(seg_list)
        self._nodes = [Node(p) if len(p) == 3 else Node(p[:3], r=p[3]) for p in self._points3d]

    def _update_curves(self):
        from ada.core.vector_utils import local_2_global_points

        points2d_no_r = [n[:2] for n in self.points2d]
        points3d = local_2_global_points(points2d_no_r, self.placement.origin, self.placement.xdir, self.placement.zdir)
        for i, p in enumerate(self.points2d):
            if len(p) == 3:
                points3d[i] = (points3d[i][0], points3d[i][1], points3d[i][2], p[-1])
            else:
                points3d[i] = tuple(points3d[i].tolist())
        self._points3d = points3d
        self._local2d_to_polycurve(self.points2d, tol=self._tol)

    def make_extruded_solid(self, height: float):
        from ada.occ.utils import extrude_closed_wire

        return extrude_closed_wire(self.wire, self.placement.origin, self.placement.zdir, height)

    def make_revolve_solid(self, axis, angle, origin):
        from ada.occ.utils import make_revolve_solid

        return make_revolve_solid(self.face, axis, angle, origin)

    def make_shell(self):
        from ada.occ.utils import wire_to_face

        return wire_to_face(self.edges)

    def scale(self, scale_factor, tol):
        self.placement.origin = np.array([x * scale_factor for x in self.placement.origin])
        self._points2d = [tuple([x * scale_factor for x in p]) for p in self._points2d]
        self._points3d = [tuple([x * scale_factor for x in p]) for p in self._points3d]
        self._local2d_to_polycurve(self.points2d, tol=tol)

    @property
    def placement(self) -> Placement:
        return self._placement

    @placement.setter
    def placement(self, value: Placement):
        self._placement = value
        self._update_curves()

    @property
    def seg_global_points(self):
        return self._seg_global_points

    @property
    def points2d(self):
        return self._points2d

    @property
    def points3d(self) -> List[Node]:
        return self._points3d

    @property
    def nodes(self) -> List[Node]:
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

    @property
    def edges(self):
        from ada.occ.utils import segments_to_edges

        return segments_to_edges(self.seg_list)

    @property
    def wire(self):
        from ada.occ.utils import make_wire

        return make_wire(self.edges)

    @property
    def face(self):
        return self.make_shell()

    @property
    def seg_index(self):
        return self._seg_index

    @property
    def seg_list(self) -> List[Union[LineSegment, ArcSegment]]:
        return self._seg_list

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value


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
