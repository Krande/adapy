from __future__ import annotations

import os
from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.config import Config, logger

from ..geom.points import Point
from .exceptions import VectorNormalizeError
from .utils import roundoff
from .vector_transforms import linear_2dtransform_rotate, transform_3x3
from .vector_utils import (
    angle_between,
    intersect_calc,
    intersection_point,
    unit_vector,
    vector_length,
    vector_length_2d,
)

if TYPE_CHECKING:
    from ada.api.curves import ArcSegment, LineSegment

    from .. import Placement


def calculate_center(v1, v2) -> Point | None:
    # Calculate midpoints of v1 and v2
    m1 = v1 / 2
    m2 = v2 / 2

    # Calculate slopes of perpendicular bisectors
    slope1 = -v1[0] / v1[1] if v1[1] != 0 else np.inf
    slope2 = -v2[0] / v2[1] if v2[1] != 0 else np.inf

    if np.isinf(slope1) and np.isinf(slope2):
        return None  # parallel or antiparallel vectors, no unique center
    elif np.isinf(slope1):
        return Point(*[m1[0], slope2 * m1[0] + (m2[1] - slope2 * m2[0])])
    elif np.isinf(slope2):
        return Point(*[m2[0], slope1 * m2[0] + (m1[1] - slope1 * m1[0])])
    else:
        # Calculate y-intercepts
        b1 = m1[1] - slope1 * m1[0]
        b2 = m2[1] - slope2 * m2[0]

        # Solve for intersection point
        px = (b2 - b1) / (slope1 - slope2)
        py = slope1 * px + b1

        return Point(*[px, py])


def create_arc_segment(v1, v2, radius):
    from ada import ArcSegment

    # Normalize vectors
    v1 = v1 / np.linalg.norm(v1)
    v2 = v2 / np.linalg.norm(v2)

    # Calculate center point
    pc = calculate_center(v1, v2)
    if pc is None:
        return None  # cannot create arc segment

    # Get angle between vectors
    angle = np.arccos(np.clip(np.dot(v1, v2), -1.0, 1.0))

    # Find arc points
    start = pc + radius * v1
    end = pc + radius * v2
    midpoint = pc + radius * np.cos(angle / 2) * (v1 + v2) / np.linalg.norm(v1 + v2)

    return ArcSegment(start, end, midpoint, radius, pc)


def make_arc_segment_with_tolerance(
    start: Iterable | Point,
    center: Iterable | Point,
    end: Iterable | Point,
    radius: float,
    min_radius_abs: float = None,
    min_radius_rel: float = 0.3,
) -> list[LineSegment | ArcSegment]:
    original_radius = radius
    while True:
        try:
            segments = make_arc_segment(start, center, end, radius)
            if radius != original_radius:
                logger.warning(f"radius: {radius}")
            break
        except VectorNormalizeError:
            radius *= 0.99
            if min_radius_abs is not None and radius < min_radius_abs:
                raise ValueError(f"Could not make arc with radius {original_radius}. stopped at {radius}")
            if radius < min_radius_rel * original_radius:
                raise ValueError(f"Could not make arc with radius {original_radius}. stopped at {radius}")
    return segments


def make_arc_segment(
    start: Iterable | Point, intersect_p: Iterable | Point, end: Iterable | Point, radius: float
) -> list[LineSegment | ArcSegment]:
    from ada import ArcSegment, Placement

    if not isinstance(start, Point):
        start = Point(*start)
    if not isinstance(intersect_p, Point):
        intersect_p = Point(*intersect_p)
    if not isinstance(end, Point):
        end = Point(*end)

    # The SegCreator always creates a closed loop, that's why we pop the first segment
    if start.dim == 3:
        place = Placement.from_co_linear_points([start, intersect_p, end])
        points2d = place.transform_global_points_to_local([start, intersect_p, end])

        points = [points2d[0], [*points2d[1], radius], points2d[2]]
        sc = SegCreator(points, is_closed=False)
        segments = sc.build()
        segments3d = []
        for seg in segments:
            if isinstance(seg, ArcSegment):
                seg3d = transform_2d_arc_segment_to_3d(seg, place)
                segments3d.append(seg3d)
            else:
                points = np.array([seg.p1, seg.p2])
                s, e = place.transform_local_points_back_to_global(points)
                seg.p1 = s
                seg.p2 = e
                segments3d.append(seg)
        segments = segments3d
    else:
        points = [start, [*intersect_p, radius], end]
        sc = SegCreator(points, is_closed=False)
        segments = sc.build()

    if len(segments) > 3:
        segments.pop(0)

    return segments


class SegCreator:
    def __init__(
        self,
        local_points,
        tol=1e-3,
        debug=False,
        debug_name="ilog",
        parent=None,
        is_closed=True,
        fig=None,
    ):
        self._parent = parent
        self._seg_list = []
        self._local_points = local_points
        self._local_cog = self._calc_points_cog()
        self._debug_name = debug_name.replace("/", "_")
        self._debug_path = None
        self._tol = tol
        self._fig = fig
        self._i = 0
        self._debug = debug
        self._curve_data: ArcSegment | None = None
        self._is_closed = is_closed
        if debug is True:
            self._debug_path = Config().general_debug_dir

            if os.path.isdir(Config().general_debug_dir) is False:
                os.makedirs(Config().general_debug_dir, exist_ok=True)
            self._start_plot()

    def build(self) -> list[LineSegment | ArcSegment]:
        in_loop = True
        while in_loop:
            if self.radius is not None:
                self.calc_circle_line()
                if abs(self.radius) < 1e-5:
                    self._curve_data = None

                    self.calc_line()
                else:
                    self.calc_arc()
            else:
                self._curve_data = None
                self.calc_line()

            if self.i == len(self._local_points) - 1:
                in_loop = False
            else:
                self.next()

        return self._seg_list

    def next(self):
        self._i += 1

        # Debug
        if self._debug is True:
            if self.radius is not None:
                lbl_str = f"p={self._i}: Arc: p1, p2, p3, {self.radius}"
            else:
                lbl_str = f"p={self._i}: Line: p1, p2, p3"
            self._add_to_plot([self.p1, self.p2, self.p3], label=lbl_str)

    def calc_line(self):
        """Calculate line segment between p2 and p3 (p1 - p2 for i == 0)"""
        from ada import ArcSegment, LineSegment

        i = self._i
        if i == 0:
            if len(self._local_points[-1]) == 2:
                if self._debug is True:
                    self._add_to_plot([self.p1, self.p2], label=f"p={i}: Line Gen p1, p2")
                self._seg_list.append(LineSegment(p1=self.p1, p2=self.p2))

        if i == len(self._local_points) - 1:
            # Check BEFORE Center point
            if type(self.pseg) is ArcSegment:
                v = vector_length_2d((np.array(self.pseg.p2) - np.array(self.p2)))
                if v > self._tol:
                    if self._debug is True:
                        self._add_to_plot(
                            [self.pseg.p2, self.p2],
                            label=f"p={i}: Line Gen Arc End, p2",
                        )
                    self._seg_list.append(LineSegment(p1=self.pseg.p2, p2=self.p2))

            # Check AFTER center point
            if self._is_closed is False:
                return None
            v = vector_length_2d(np.array(self.p2) - self._seg_list[0].p2)
            if v < self._tol:
                if self._debug:
                    self._add_to_plot(
                        [self._seg_list[0].p1, self._seg_list[0].p2],
                        label=f"p={i}: Removing line",
                    )
                self._seg_list.pop(0)
            else:
                s = self._seg_list[0].p1
                e = self.p2
                v_s = vector_length_2d(s - e)
                if v_s > self._tol:
                    if self._debug is True:
                        self._add_to_plot(
                            [self.p2, self._seg_list[0].p2],
                            label=f"p={i}: Line Gen p2, Seg0.p2",
                        )
                    self._seg_list.append(LineSegment(p1=self.p2, p2=self._seg_list[0].p2))
        else:
            # Check BEFORE Center point
            if type(self.pseg) is ArcSegment:
                v = vector_length_2d((np.array(self.pseg.p2) - np.array(self.p2)))
                if v > self._tol:
                    if self._debug is True:
                        self._add_to_plot(
                            [self.pseg.p2, self.p2],
                            label=f"p={i}: Line Gen Arc End, p2",
                        )
                    self._seg_list.append(LineSegment(p1=self.pseg.p2, p2=self.p2))

            # Check AFTER center point
            v = vector_length_2d((np.array(self.p3) - np.array(self.p2)))
            if v > self._tol:
                if len(self._local_points[i + 1]) == 2:
                    if self._debug is True:
                        self._add_to_plot([self.p2, self.p3], label=f"p={i}: Line Gen p2, p3")
                    self._seg_list.append(LineSegment(p1=self.p2, p2=self.p3))
                elif abs(self._local_points[i + 1][2]) < 1e-5:
                    if self._debug is True:
                        self._add_to_plot([self.p2, self.p3], label=f"p={i}: Line Gen p2, p3")
                    self._seg_list.append(LineSegment(p1=self.p2, p2=self.p3))
                else:
                    pass

    def calc_arc(self):
        """Calculate arc segments when a fillet radius is given as 3rd value in the local_points listed tuples."""
        i = self._i
        from ada import ArcSegment, LineSegment

        seg_after = None

        # Before Arc
        if i == 0:
            d1 = vector_length_2d(np.array(self.curve_data.p1) - self.p1)
            if d1 > self._tol and len(self._local_points[-1]) == 2:
                if self._debug is True:
                    self._add_to_plot(
                        [self.p1, self.curve_data.p1],
                        label=f"p={i}: Line Gen BeforeArc p1, arc_start ",
                    )
                self._seg_list.append(LineSegment(p1=self.p1, p2=self.curve_data.p1))
            else:
                if type(self._local_points[-1]) is LineSegment:
                    if self._debug is True:
                        self._add_to_plot(
                            [self.p1, self.curve_data.p1],
                            label=f"p={i}: Moving arc_start to p1",
                        )
                    self.curve_data.p1 = self.p1
        elif self.pseg is None:
            pass
        else:
            if vector_length_2d(self.pseg.p2 - self.curve_data.p1) < self._tol:
                if self._debug is True:
                    self._add_to_plot(
                        [self.pseg.p2, self.curve_data.p1],
                        label=f"p={i}: Moving arc_start to pseg.p2",
                    )
                self.curve_data.p1 = self.pseg.p2
            else:
                v1 = self.curve_data.midpoint - self.pseg.p2
                v2 = self.curve_data.p1 - self.pseg.p2
                deg1 = np.rad2deg(angle_between(v1, v2))
                if deg1 < 120:
                    if self._debug is True:
                        self._add_to_plot(
                            [self.pseg.p2, self.curve_data.p1],
                            label=f"p={i}: Line Gen BeforeArc p1, arc_start ",
                        )
                    self._seg_list.append(LineSegment(p1=self.pseg.p2, p2=self.curve_data.p1))
                else:
                    if type(self.pseg) is LineSegment and roundoff(self.angle_pseg_p1arc_start) == 180.0:
                        if self._debug is True:
                            self._add_to_plot(
                                [self.pseg.p2, self.curve_data.p1],
                                label=f"p={i}: Moving pseg.p2 to arc_start ",
                            )
                        self.pseg.p2 = self.curve_data.p1
                    else:
                        if self._debug is True:
                            self._add_to_plot(
                                [self.pseg.p2, self.curve_data.p1],
                                label=f"p={i}: Moving arc_start to pseg.p2",
                            )
                        self.curve_data.p1 = self.pseg.p2

        # After Arc
        after_arc_end = None
        if i == len(self._local_points) - 1:
            if self._is_closed is False:
                return None
            if vector_length_2d(self._seg_list[0].p1 - np.array(self.curve_data.p2)) > self._tol:
                after_arc_end = self._seg_list[0].p1
                seg_after = LineSegment(p1=self.curve_data.p2, p2=self._seg_list[0].p1)
            else:
                if type(self._seg_list[0]) is ArcSegment:
                    self._seg_list[0].p1 = self.curve_data.p2
                else:
                    self.curve_data.p2 = self._seg_list[0].p1
        else:
            delta_p3_arc_end = vector_length_2d(self.p3 - np.array(self.curve_data.p2))
            if delta_p3_arc_end < self._tol:
                if self._debug:
                    self._add_to_plot([self.curve_data.p2, self.p3], label=f"p={i}: Moving arc_end to p3")
                self.curve_data.p2 = self.p3
            else:
                v1 = unit_vector(self.curve_data.p2 - self.curve_data.midpoint)
                v2 = unit_vector(self.p3 - self.curve_data.p2)
                deg1 = np.rad2deg(angle_between(v1, v2))
                if len(self._local_points[i + 1]) == 2:
                    if deg1 < 100:
                        after_arc_end = self.p3
                        seg_after = LineSegment(p1=self.curve_data.p2, p2=self.p3)
                    else:
                        if self._debug:
                            self._add_to_plot(
                                [self.curve_data.p2, self.p3],
                                label=f"p={i}: Moving arc_end to p3",
                            )
                        self.curve_data.p2 = self.p3
                else:
                    # A line segment is added prior to next arc\line segment
                    pass

        # Adding segments
        if self._debug is True:
            self._add_to_plot(
                [self.curve_data.p1, self.curve_data.midpoint, self.curve_data.p2],
                label=f"p={i}: Arc Gen start, midp, end, radius={self.radius}",
            )

        self._seg_list.append(
            ArcSegment(
                p1=self.curve_data.p1,
                p2=self.curve_data.p2,
                midpoint=self.curve_data.midpoint,
                radius=self.radius,
                center=self.curve_data.center,
                intersection=self.curve_data.intersection,
                s_normal=self.curve_data.s_normal,
                e_normal=self.curve_data.e_normal,
            )
        )

        if seg_after is not None:
            if self._debug is True:
                self._add_to_plot(
                    [self.curve_data.p2, after_arc_end],
                    label=f"p={i}: Line Gen AfterArc, end, p3",
                )
            self._seg_list.append(seg_after)

    def calc_circle_line(self):
        from ada import ArcSegment

        self._curve_data = ArcSegment.from_start_center_end_radius(self.p1, self.p2, self.p3, self.radius)

        if self._debug is True:
            self._add_to_plot(
                [self.curve_data.center, self.curve_data.p1, self.curve_data.p2, self.curve_data.midpoint],
                label=f"p={self._i}: Arc Center, start, end, midp",
                mode="markers",
            )

    def _calc_points_cog(self):
        x = []
        y = []
        for p in self._local_points:
            x.append(p[0])
            y.append(p[1])
        return (min(x) + max(x)) / 2, (min(y) + max(y)) / 2

    @property
    def cog(self):
        return self._local_cog

    @property
    def p1(self):
        if self._i == 0:
            return np.array(self._local_points[-1][:2])
        else:
            return np.array(self._local_points[self._i - 1][:2])

    @property
    def p2(self):
        return np.array(self._local_points[self._i][:2])

    @property
    def p3(self):
        if self._i == len(self._local_points) - 1:
            return np.array(self._local_points[0][:2])
        else:
            return np.array(self._local_points[self._i + 1][:2])

    @property
    def prevp_to_arc_start_len(self):
        if self.curve_data.p1 is not None:
            return vector_length_2d(np.array(self.curve_data.p1) - self.p1)
        else:
            return vector_length_2d(self.p2 - self.p1)

    @property
    def pseg(self):
        if len(self._seg_list) > 0:
            return self._seg_list[-1]
        else:
            return None

    @property
    def pseg_vector(self):
        return unit_vector(self.pseg.p2 - self.pseg.p1)

    @property
    def p1p2_cross(self):
        return np.cross(unit_vector(self.p1 - self.p2), np.array([0, 0, 1]))

    @property
    def p2p3_cross(self):
        return np.cross(unit_vector(self.p3 - self.p2), np.array([0, 0, 1]))

    @property
    def angle_p1p2p3(self):
        return np.rad2deg(angle_between(self.p1p2_cross, self.p2p3_cross))

    @property
    def intersect_pseg_p1_arcstart(self):
        A = np.append(self.pseg.p1, [0])
        B = np.append(self.pseg.p2, [0])
        C = np.append(self.p1, [0])
        D = np.append(self.curve_data.p1, [0])
        s, t = intersect_calc(A, C, B - A, D - C)
        return s

    @property
    def intersect_p3arcend_arcmidend(self):
        A = np.append(self.curve_data.p2, [0])
        B = np.append(self.p3, [0])
        C = np.append(self.curve_data.midpoint, [0])
        D = np.append(self.curve_data.p2, [0])
        s, t = intersect_calc(A, C, B - A, D - C)
        return s

    @property
    def angle_pseg_p1arc_start(self):
        from ada import ArcSegment

        if type(self.pseg) is ArcSegment:
            n = np.array([0, 0, 1])
            tangent = np.cross(unit_vector(self.pseg.p2 - self.pseg.center), n)
            deg = np.rad2deg(angle_between(tangent[:2], self.arc_start_tangent))
            return deg
        else:
            return np.rad2deg(angle_between(self.pseg_vector, self.arc_start_tangent))

    @property
    def angle_arc_end_p3(self):
        n = np.array([0, 0, 1])
        end = np.append(self.curve_data.p2, [0])
        center = np.append(self.curve_data.center, [0])
        tangent = np.cross(unit_vector(end - center), n)
        nextseg = np.append(self.p3 - self.curve_data.p2, [0])
        deg = np.rad2deg(angle_between(tangent, nextseg))
        return deg

    # Arc Related properties
    @property
    def curve_data(self) -> ArcSegment:
        return self._curve_data

    @property
    def radius(self):
        if len(self._local_points[self._i]) == 3:
            if abs(self._local_points[self._i][2]) < 1e-5:
                return None
            else:
                return self._local_points[self._i][2]
        else:
            return None

    @property
    def arc_start_tangent(self):
        if self.curve_data.p1 is not None:
            n = np.array([0, 0, 1])
            tangent = np.cross(unit_vector(self.curve_data.p1 - self.curve_data.center), n)
            return tangent[:2]
        else:
            return None

    @property
    def psegp2_arc_start_cross(self):
        if self.curve_data.p1 is not None and self.pseg is not None:
            return np.cross(unit_vector(self.curve_data.p1 - self.pseg.p2), np.array([0, 0, 1]))
        else:
            return None

    @property
    def arc_endp3_cross(self):
        if self.curve_data.p2 is not None:
            return np.cross(unit_vector(self.curve_data.p2 - self.p3), np.array([0, 0, 1]))
        else:
            return None

    @property
    def plot_path(self):
        return rf"{self._debug_path}\{self._debug_name}.html"

    @property
    def i(self):
        return self._i

    @i.setter
    def i(self, value):
        self._i = value

    # Private methods
    def _start_plot(self):
        from plotly import graph_objs as go

        xv = [p[0] for p in self._local_points]
        yv = [p[1] for p in self._local_points]

        self._fig = go.FigureWidget() if self._fig is None else self._fig
        self._fig["layout"]["yaxis"]["scaleanchor"] = "x"
        trace1 = go.Scatter(
            x=xv,
            y=yv,
            mode="lines+markers",
            name="Original Local Points",
            # line=go.scatter.Line(color="gray"),
            marker=dict(symbol="circle"),
        )
        self._fig.add_trace(trace1)
        self._add_to_plot([self.p1, self.p2, self.p3], label=f"p={self._i}: p1, p2, p3 ")
        print(f'Creating debug HTML at "{self.plot_path}"')
        self._fig.write_html(self.plot_path)

    def _add_to_plot(self, data, label=None, mode="lines+markers", hovertemplate=None, text=None):
        from plotly import graph_objs as go

        xvals = [p[0] for p in data]
        yvals = [p[1] for p in data]
        trace = go.Scatter(
            x=xvals,
            y=yvals,
            name=label,
            mode=mode,
            # line=go.scatter.Line(color="gray"),
            # showlegend=False
            hovertemplate=hovertemplate,
            text=text,
        )

        self._fig.add_trace(trace)
        self._fig.write_html(self.plot_path)


def segments_to_local_points(segments_in: list[LineSegment | ArcSegment]) -> list[tuple]:
    from ada import LineSegment

    local_points = []
    segments = segments_in[1:]
    for i, seg in enumerate(segments):
        if i == 0:
            pseg = segments[-1]
        else:
            pseg = segments[i - 1]

        if i == len(segments) - 1:
            nseg = segments[0]
        else:
            nseg = segments[i + 1]

        if type(seg) is LineSegment:
            if i == 0:
                local_points.append((seg.p1[0], seg.p1[1]))
            else:
                if type(segments[i - 1]) is LineSegment:
                    local_points.append((seg.p1[0], seg.p1[1]))
            if i < len(segments) - 1:
                if type(segments[i + 1]) is LineSegment:
                    local_points.append((seg.p2[0], seg.p2[1]))
            else:
                local_points.append((seg.p2[0], seg.p2[1]))
        else:
            center, radius = calc_arc_radius_center_from_3points(seg.p1, seg.midpoint, seg.p2)

            p0 = pseg.p1
            p4 = nseg.p2
            v1 = (np.array([p0[0], p0[1]]), np.array([seg.p1[0], seg.p1[1]]))
            v2 = (np.array([seg.p2[0], seg.p2[1]]), np.array([p4[0], p4[1]]))
            v1_ = v1[1] - v1[0]
            v2_ = v2[1] - v2[0]
            ed = np.cross(v1_, v2_)
            if ed < 0:
                local_points.append((seg.p1[0], seg.p1[1]))
            ip = intersection_point(v1, v2)
            local_points.append((ip[0], ip[1], radius))

    return local_points


def transform_2d_arc_segment_to_3d(arc2d: ArcSegment, place: Placement):
    from ada import ArcSegment

    arc_points = [arc2d.p1, arc2d.p2, arc2d.midpoint, arc2d.center, arc2d.intersection]
    r = place.transform_local_points_back_to_global(np.asarray(arc_points))
    sn, en = transform_3x3(place.rot_matrix, np.array([arc2d.s_normal, arc2d.e_normal]), inverse=True)
    return ArcSegment(r[0], r[1], r[2], arc2d.radius, r[3], r[4], sn, en)


def segments_to_indexed_lists(segments: list[LineSegment | ArcSegment]):
    from ada import ArcSegment

    final_point_list = []
    seg_index = []
    for i, seg in enumerate(segments):
        si = []
        if i == 0:
            final_point_list.append(seg.p1)

        if i == len(segments) - 1:
            si += [len(final_point_list)]
            if type(seg) is ArcSegment:
                final_point_list[-1] = seg.p1
                final_point_list.append(seg.midpoint)
                si += [len(final_point_list)]

            if len(segments) == i + 1:
                si += [1]
            else:
                si += [len(final_point_list)]
            seg_index.append(si)
        else:
            si += [len(final_point_list)]
            if type(seg) is ArcSegment:
                final_point_list[-1] = seg.p1
                final_point_list.append(seg.midpoint)
                si += [len(final_point_list)]

            final_point_list.append(seg.p2)
            if len(segments) == i + 1:
                si += [1]
            else:
                si += [len(final_point_list)]
            seg_index.append(si)
    return final_point_list, seg_index


def calc_arc_radius_center_from_3points(start, midpoint, end):
    """

    Source:

        http://paulbourke.net/geometry/circlesphere/

    :param start:
    :param midpoint:
    :param end:
    :return: Center, Radius
    """
    p1 = np.array(start[:2])
    p2 = np.array(midpoint[:2])
    p3 = np.array(end[:2])

    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3

    ma = (y2 - y1) / (x2 - x1)
    mb = (y3 - y2) / (x3 - x2)

    x = (ma * mb * (y1 - y3) + mb * (x1 + x2) - ma * (x2 + x3)) / (2 * (mb - ma))
    yda = -(1 / ma) * (x - (x1 + x2) / 2) + (y1 + y2) / 2

    center = np.array([x, yda])
    radius = roundoff(vector_length_2d(p1 - center))

    return center, radius


def intersect_line_circle(line, center, radius, tol=1e-1):
    """

    Source:

        http://paulbourke.net/geometry/circlesphere/

        # Working with threshold value for real parts
        https://stackoverflow.com/a/28084225/8053631
    """

    x1, y1 = line[0][:2]
    x2, y2 = line[1][:2]
    x3, y3 = center[:2]
    z1, z2, z3 = 0, 0, 0

    a = (x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2
    b = 2 * ((x2 - x1) * (x1 - x3) + (y2 - y1) * (y1 - y3) + (z2 - z1) * (z1 - z3))
    c = x3**2 + y3**2 + z3**2 + x1**2 + y1**2 + z1**2 - 2 * (x3 * x1 + y3 * y1 + z3 * z1) - radius**2

    ev = b * b - 4 * a * c

    coeff = [a, b, c]

    r = np.roots(coeff)
    res = r.real[abs(r.imag) < 1e-5]
    p1 = np.array(line[0])
    p2 = np.array(line[1])
    vec = p2 - p1
    p = []

    for pa, pb in zip(p1 + res[1] * vec, p1 + res[1] * vec):
        p.append(roundoff((pa + pb) / 2, 5))

    # It's not necessarily practical to use a 1mm point tolerance for this. Will increase tol to 3mm for now
    if tol == 1:
        tol = 5

    if ev < 0.0 and abs(ev) > tol:
        raise ValueError(f'Line "{line}" does not intersect sphere ({center=}, {radius=}) {abs(ev)=}>{tol=}')
    elif ev > 0.0 and abs(ev) > tol:
        raise ValueError(f'Line "{line}" intersects sphere ({center=}, {radius=}) at multiple points {abs(ev)=}>{tol=}')

    return p


def calc_2darc_start_end_from_lines_radius(p1, p2, p3, radius, tol=1e-1) -> ArcSegment:
    """
    From intersecting lines and a given radius return the arc start, end, center of radius and a point on the arc

    Source:

        http://paulbourke.net/geometry/circlesphere/
        https://math.stackexchange.com/questions/797828/calculate-center-of-circle-tangent-to-two-lines-in-space

    """
    from ada import ArcSegment

    points = np.array([p1, p2, p3])

    if len(points.shape) > 1 and points.shape[1] == 3:
        raise NotImplementedError("3D not implemented yet")

    p1, p2, p3 = points

    v1 = unit_vector(p2 - p1)
    v2 = unit_vector(p2 - p3)

    alpha = angle_between(v1, v2)
    s = radius / np.sin(alpha / 2)
    dir_eval = np.cross(v1, v2)
    if dir_eval < 0:
        theta = -alpha / 2
    else:
        theta = alpha / 2
    A = p2 - v1 * s

    if radius < 0:
        center = p2
        start = p2 + v1 * radius
        end = p2 + v2 * radius

        vc1 = np.array([center[0], center[1], 0.0]) - np.array([start[0], start[1], 0.0])
        vc2 = np.array([center[0], center[1], 0.0]) - np.array([end[0], end[1], 0.0])

        arbp = angle_between(vc2, vc1)

        if dir_eval < 0:
            gamma = -arbp / 2
        else:
            gamma = arbp / 2

    else:
        center = linear_2dtransform_rotate(p2, A, np.rad2deg(theta))
        start = intersect_line_circle((p1, p2), center, radius, tol=tol)
        end = intersect_line_circle((p3, p2), center, radius, tol=tol)

        vc1 = np.array([start[0], start[1], 0.0]) - np.array([center[0], center[1], 0.0])
        vc2 = np.array([end[0], end[1], 0.0]) - np.array([center[0], center[1], 0.0])

        arbp = angle_between(vc1, vc2)

        if dir_eval < 0:
            gamma = arbp / 2
        else:
            gamma = -arbp / 2

    midp = linear_2dtransform_rotate(center, start, np.rad2deg(gamma))

    return ArcSegment(start, end, midp, radius, center, p2, v1, v2)


def build_polycurve(
    local_points2d: list[tuple], tol=1e-3, debug=False, debug_name=None, is_closed=True
) -> list[LineSegment | ArcSegment]:
    if len(local_points2d) == 2:
        from ada.api.curves import LineSegment

        return [LineSegment(p1=local_points2d[0], p2=local_points2d[1])]

    segc = SegCreator(local_points2d, tol=tol, debug=debug, debug_name=debug_name, is_closed=is_closed)
    return segc.build()


def s_curve(ramp_up_t, ramp_down_t, magnitude, sustained_time=0.0):
    """
    A function created to

    :param ramp_up_t:
    :param ramp_down_t:
    :param magnitude:
    :param sustained_time:
    :return: tuple of X and Y lists describing a S-Curved ramp up and ramp down.
    """
    from .curve_fitting_utils import bezier

    yp = np.array([0.0, 0.1, 1.0, 1.0]) * magnitude
    if ramp_up_t is not None:
        xp1 = np.array([0.0, ramp_up_t / 2, ramp_up_t / 2, ramp_up_t])
        x1, y1 = bezier(list(zip(xp1, yp))).T
        if sustained_time > 0.0:
            delta_x = x1[-1] - x1[-2]
            x0_ = x1[-1] + delta_x
            x1_ = x1[-1] + sustained_time
            y = y1[-1]
            add_x = np.linspace(x0_, x1_, 50, endpoint=True)
            add_y = [y for r in add_x]
            x1 = np.append(x1, add_x)
            y1 = np.append(y1, add_y)
    else:
        x1, y1 = None, None

    if ramp_down_t is not None:
        xp2 = np.array([0, ramp_down_t / 2, ramp_down_t / 2, ramp_down_t])
        x2, y2 = bezier(list(zip(xp2, yp))).T
    else:
        x2, y2 = None, None

    if ramp_down_t is None and ramp_up_t is not None:
        total_curve = x1, x2
    elif ramp_down_t is not None and ramp_up_t is None:
        total_curve = x2, y2[::-1]
    else:
        total_curve = np.append(x1, x2[1:] + x1[-1]), np.append(y1, y2[::-1][1:])

    return total_curve


def line_segments3d_from_points3d(points: list[Point | Iterable]) -> list[LineSegment]:
    from ada.api.curves import LineSegment

    if not isinstance(points[0], Point):
        points = [Point(*p) for p in points]

    prelim_segments: list[LineSegment] = []
    for p1, p2 in zip(points[:-1], points[1:]):
        straight_seg = LineSegment(p1, p2)

        if straight_seg.length == 0.0:
            logger.info("skipping zero length segment")
            continue

        prelim_segments.append(straight_seg)
    return prelim_segments


def segments3d_from_points3d(
    points: list[Point | Iterable], radius=None, radius_dict=None, angle_tol=1e-1, len_tol=1e-3
) -> list[LineSegment | ArcSegment]:
    from ada.api.curves import ArcSegment, LineSegment

    prelim_segments = line_segments3d_from_points3d(points)

    if len(prelim_segments) == 1:
        return prelim_segments
    prelim_segments_zip = list(zip(prelim_segments[:-1], prelim_segments[1:]))
    segments = []
    for i, (seg1, seg2) in enumerate(prelim_segments_zip):
        seg1: LineSegment
        seg2: LineSegment

        if seg1.length < len_tol or seg2.length == len_tol:
            logger.error(f'Segment Length is below point tolerance "{len_tol}". Skipping')
            continue

        if seg1.direction.is_parallel(seg2.direction, angle_tol):
            segments.append(LineSegment(seg1.p1.copy(), seg1.p2.copy()))
            continue

        if not seg1.p2.is_equal(seg2.p1):
            logger.error("No shared point found")

        arc_end = seg2.p2
        if i != 0 and len(segments) > 0:
            arc_start = segments[-1].p1
            arc_intersection = segments[-1].p2
        else:
            arc_start = seg1.p1
            arc_intersection = seg1.p2

        if isinstance(radius_dict, dict):
            r = radius_dict.get(i + 1, min(seg1.length, seg2.length) / 3)
        elif isinstance(radius, (int, float)):
            r = radius
        else:
            raise ValueError(f"Radius must be a float, int, or dict. Got {type(radius)}")

        try:
            new_seg1, arc, new_seg2 = make_arc_segment(arc_start, arc_intersection, arc_end, r)
        except (ValueError, VectorNormalizeError) as e:
            points = [arc_start.tolist(), arc_intersection.tolist(), arc_end.tolist()]
            logger.error(f"Arc build failed for points: {points}. Error: {e}")
            continue

        if i == 0 or len(segments) == 0:
            segments.append(LineSegment(new_seg1.p1.copy(), new_seg1.p2.copy()))
        else:
            if len(segments) == 0:
                continue
            pseg = segments[-1]
            nlen = vector_length(new_seg1.p2 - pseg.p1)
            if nlen < len_tol:
                # The new arc starts at the same point as the previous segment. Remove the previous segment
                segments.pop(-1)
            else:
                pseg.p2 = new_seg1.p2

        segments.append(
            ArcSegment(
                arc.p1.copy(),
                arc.p2.copy(),
                midpoint=arc.midpoint.copy(),
                center=arc.center.copy() if arc.center is not None else None,
                intersection=arc.intersection.copy() if arc.intersection is not None else None,
                radius=arc.radius,
                s_normal=arc.s_normal.copy(),
                e_normal=arc.e_normal.copy(),
            )
        )
        segments.append(LineSegment(new_seg2.p1.copy(), new_seg2.p2.copy()))

    return segments
