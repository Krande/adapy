import os

import numpy as np

from ..config import Settings as _Settings
from ..occ.utils import get_midpoint_of_arc
from .utils import (
    angle_between,
    calc_yvec,
    global_2_local_nodes,
    intersect_calc,
    intersection_point,
    linear_2dtransform_rotate,
    local_2_global_nodes,
    normal_to_points_in_plane,
    roundoff,
    unit_vector,
    vector_length_2d,
)


def make_arc_segment(p1, p2, p3, radius):
    from ada import ArcSegment, LineSegment

    from ..occ.utils import get_edge_points

    ed1, ed2, fillet = make_edges_and_fillet_from_3points(p1, p2, p3, radius)

    ed1_p = get_edge_points(ed1)
    ed2_p = get_edge_points(ed2)
    fil_p = get_edge_points(fillet)
    midpoint = get_midpoint_of_arc(fillet)
    l1 = LineSegment(*ed1_p, edge_geom=ed1)
    arc = ArcSegment(fil_p[0], fil_p[1], midpoint, radius, edge_geom=fillet)
    l2 = LineSegment(*ed2_p, edge_geom=ed2)
    return [l1, arc, l2]


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
        self._arc_center = None
        self._arc_start = None
        self._arc_end = None
        self._arc_midpoint = None
        self._is_closed = is_closed
        if debug is True:
            self._debug_path = _Settings.debug_dir

            if os.path.isdir(_Settings.debug_dir) is False:
                os.makedirs(_Settings.debug_dir, exist_ok=True)
            self._start_plot()

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
        """
        Calculate line segment between p2 and p3 (p1 - p2 for i == 0)

        :return:
        """
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
        """
        Calculate arc segments when a fillet radius is given as 3rd value in the local_points listed tuples.

        :return:
        """
        i = self._i
        from ada import ArcSegment, LineSegment

        seg_after = None

        # Before Arc
        if i == 0:
            d1 = vector_length_2d(np.array(self.arc_start) - self.p1)
            if d1 > self._tol and len(self._local_points[-1]) == 2:
                if self._debug is True:
                    self._add_to_plot(
                        [self.p1, self.arc_start],
                        label=f"p={i}: Line Gen BeforeArc p1, arc_start ",
                    )
                self._seg_list.append(LineSegment(p1=self.p1, p2=self.arc_start))
            else:
                if type(self._local_points[-1]) is LineSegment:
                    if self._debug is True:
                        self._add_to_plot(
                            [self.p1, self.arc_start],
                            label=f"p={i}: Moving arc_start to p1",
                        )
                    self.arc_start = self.p1
        elif self.pseg is None:
            pass
        else:
            if vector_length_2d(self.pseg.p2 - self.arc_start) < self._tol:
                if self._debug is True:
                    self._add_to_plot(
                        [self.pseg.p2, self.arc_start],
                        label=f"p={i}: Moving arc_start to pseg.p2",
                    )
                self.arc_start = self.pseg.p2
            else:
                v1 = self.arc_midpoint - self.pseg.p2
                v2 = self.arc_start - self.pseg.p2
                deg1 = np.rad2deg(angle_between(v1, v2))
                if deg1 < 120:
                    if self._debug is True:
                        self._add_to_plot(
                            [self.pseg.p2, self.arc_start],
                            label=f"p={i}: Line Gen BeforeArc p1, arc_start ",
                        )
                    self._seg_list.append(LineSegment(p1=self.pseg.p2, p2=self.arc_start))
                else:
                    if type(self.pseg) is LineSegment and roundoff(self.angle_pseg_p1arc_start) == 180.0:
                        if self._debug is True:
                            self._add_to_plot(
                                [self.pseg.p2, self.arc_start],
                                label=f"p={i}: Moving pseg.p2 to arc_start ",
                            )
                        self.pseg.p2 = self.arc_start
                    else:
                        if self._debug is True:
                            self._add_to_plot(
                                [self.pseg.p2, self.arc_start],
                                label=f"p={i}: Moving arc_start to pseg.p2",
                            )
                        self.arc_start = self.pseg.p2

        # After Arc
        after_arc_end = None
        if i == len(self._local_points) - 1:
            if self._is_closed is False:
                return None
            if vector_length_2d(self._seg_list[0].p1 - np.array(self.arc_end)) > self._tol:
                after_arc_end = self._seg_list[0].p1
                seg_after = LineSegment(p1=self.arc_end, p2=self._seg_list[0].p1)
            else:
                if type(self._seg_list[0]) is ArcSegment:
                    self._seg_list[0].p1 = self.arc_end
                else:
                    self.arc_end = self._seg_list[0].p1
        else:
            delta_p3_arc_end = vector_length_2d(self.p3 - np.array(self.arc_end))
            if delta_p3_arc_end < self._tol:
                if self._debug:
                    self._add_to_plot([self.arc_end, self.p3], label=f"p={i}: Moving arc_end to p3")
                self.arc_end = self.p3
            else:
                v1 = unit_vector(self.arc_end - self.arc_midpoint)
                v2 = unit_vector(self.p3 - self.arc_end)
                deg1 = np.rad2deg(angle_between(v1, v2))
                if len(self._local_points[i + 1]) == 2:
                    if deg1 < 100:
                        after_arc_end = self.p3
                        seg_after = LineSegment(p1=self.arc_end, p2=self.p3)
                    else:
                        if self._debug:
                            self._add_to_plot(
                                [self.arc_end, self.p3],
                                label=f"p={i}: Moving arc_end to p3",
                            )
                        self.arc_end = self.p3
                else:
                    # A line segment is added prior to next arc\line segment
                    pass

        # Adding segments
        if self._debug is True:
            self._add_to_plot(
                [self.arc_start, self.arc_midpoint, self.arc_end],
                label=f"p={i}: Arc Gen start, midp, end, radius={self.radius}",
            )

        self._seg_list.append(
            ArcSegment(
                p1=self.arc_start,
                p2=self.arc_end,
                midpoint=self.arc_midpoint,
                radius=self.radius,
                center=self.arc_center,
            )
        )

        if seg_after is not None:
            if self._debug is True:
                self._add_to_plot(
                    [self.arc_end, after_arc_end],
                    label=f"p={i}: Line Gen AfterArc, end, p3",
                )
            self._seg_list.append(seg_after)

    def calc_circle_line(self):
        loc_c, loc_start, loc_end, loc_midp = calc_2darc_start_end_from_lines_radius(
            self.p1, self.p2, self.p3, self.radius
        )
        self._arc_center = loc_c
        self._arc_start = loc_start
        self._arc_end = loc_end
        self._arc_midpoint = loc_midp

        if self._debug is True:
            self._add_to_plot(
                [self.arc_center, self.arc_start, self.arc_end, self.arc_midpoint],
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
        if self.arc_start is not None:
            return vector_length_2d(np.array(self.arc_start) - self.p1)
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
        D = np.append(self.arc_start, [0])
        s, t = intersect_calc(A, C, B - A, D - C)
        return s

    @property
    def intersect_p3arcend_arcmidend(self):
        A = np.append(self.arc_end, [0])
        B = np.append(self.p3, [0])
        C = np.append(self.arc_midpoint, [0])
        D = np.append(self.arc_end, [0])
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
        end = np.append(self.arc_end, [0])
        center = np.append(self.arc_center, [0])
        tangent = np.cross(unit_vector(end - center), n)
        nextseg = np.append(self.p3 - self.arc_end, [0])
        deg = np.rad2deg(angle_between(tangent, nextseg))
        return deg

    # Arc Related properties
    @property
    def arc_center(self):
        if self._arc_center is not None:
            return self._arc_center
        else:
            return None

    @property
    def arc_start(self):
        if self._arc_start is not None:
            return np.array(self._arc_start)
        else:
            return None

    @arc_start.setter
    def arc_start(self, value):
        self._arc_start = value

    @property
    def arc_end(self):
        if self._arc_end is not None:
            return np.array(self._arc_end)
        else:
            return None

    @arc_end.setter
    def arc_end(self, value):
        self._arc_end = value

    @property
    def arc_midpoint(self):
        if self._arc_midpoint is not None:
            return np.array(self._arc_midpoint)
        else:
            return None

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
        if self.arc_start is not None:
            n = np.array([0, 0, 1])
            tangent = np.cross(unit_vector(self.arc_start - self.arc_center), n)
            return tangent[:2]
        else:
            return None

    @property
    def psegp2_arc_start_cross(self):
        if self.arc_start is not None and self.pseg is not None:
            return np.cross(unit_vector(self.arc_start - self.pseg.p2), np.array([0, 0, 1]))
        else:
            return None

    @property
    def arc_endp3_cross(self):
        if self.arc_end is not None:
            return np.cross(unit_vector(self.arc_end - self.p3), np.array([0, 0, 1]))
        else:
            return None

    @property
    def plot_path(self):
        return rf"{self._debug_path}\{self._debug_name}.html"

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


def segments_to_local_points(segments_in):
    """

    :param segments_in:
    :return:
    """
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


def segments_to_indexed_lists(segments):
    """

    :param segments:
    :return:
    """
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


def intersect_line_circle(line, center, radius):
    """

    Source:

        http://paulbourke.net/geometry/circlesphere/

        # Working with threshold value for real parts
        https://stackoverflow.com/a/28084225/8053631

    :param line:
    :param center:
    :param radius:
    :return:
    """

    x1, y1 = line[0][:2]
    x2, y2 = line[1][:2]
    x3, y3 = center[:2]
    z1, z2, z3 = 0, 0, 0

    a = (x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2
    b = 2 * ((x2 - x1) * (x1 - x3) + (y2 - y1) * (y1 - y3) + (z2 - z1) * (z1 - z3))
    c = x3 ** 2 + y3 ** 2 + z3 ** 2 + x1 ** 2 + y1 ** 2 + z1 ** 2 - 2 * (x3 * x1 + y3 * y1 + z3 * z1) - radius ** 2

    tol = 1e-1
    # if abs(b) < tol:
    #     b = roundoff(b)
    # if abs(c) < tol:
    #     c = roundoff(c)

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

    if ev < 0.0 and abs(ev) > tol:
        raise ValueError(f'The line "{line}" does not intersect sphere ({center}, {radius})')
    elif ev > 0.0 and abs(ev) > tol:
        raise ValueError(f'The line "{line}" intersects sphere ({center}, {radius}) at multiple points')

    return p


def get_center_from_3_points_and_radius(p1, p2, p3, radius):
    """

    :param p1:
    :param p2:
    :param p3:
    :param radius:
    :return:
    """
    from ada.core.constants import X, Y

    p1 = np.array(p1)
    p2 = np.array(p2)
    p3 = np.array(p3)

    points = [p1, p2, p3]
    n = normal_to_points_in_plane(points)
    xv = p2 - p1
    yv = calc_yvec(xv, n)
    if angle_between(xv, X) in (np.pi, 0) and angle_between(yv, Y) in (np.pi, 0):
        locn = [p - p1 for p in points]
        res_locn = calc_2darc_start_end_from_lines_radius(*locn, radius)
        res_glob = [np.array([p[0], p[1], 0]) + p1 for p in res_locn]
    else:
        locn = global_2_local_nodes([xv, yv], p1, points)
        res_loc = calc_2darc_start_end_from_lines_radius(*locn, radius)
        res_glob = local_2_global_nodes(res_loc, p1, xv, n)
    center, start, end, midp = res_glob

    return center, start, end, midp


def calc_2darc_start_end_from_lines_radius(p1, p2, p3, radius):
    """
    From intersecting lines and a given radius return the arc start, end, center of radius and a point on the arc

    Source:

        http://paulbourke.net/geometry/circlesphere/
        https://math.stackexchange.com/questions/797828/calculate-center-of-circle-tangent-to-two-lines-in-space

    :param p1:
    :param p2:
    :param p3:
    :param radius:
    :return: center, start, end, midp
    """

    p1 = p1[:2] if type(p1) is np.ndarray else np.array(p1[:2])
    p2 = p2[:2] if type(p2) is np.ndarray else np.array(p2[:2])
    p3 = p3[:2] if type(p3) is np.ndarray else np.array(p3[:2])

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
        start = intersect_line_circle((p1, p2), center, radius)
        end = intersect_line_circle((p3, p2), center, radius)

        vc1 = np.array([start[0], start[1], 0.0]) - np.array([center[0], center[1], 0.0])
        vc2 = np.array([end[0], end[1], 0.0]) - np.array([center[0], center[1], 0.0])

        arbp = angle_between(vc1, vc2)

        if dir_eval < 0:
            gamma = arbp / 2
        else:
            gamma = -arbp / 2

    midp = linear_2dtransform_rotate(center, start, np.rad2deg(gamma))

    return center, start, end, midp


def build_polycurve(local_points2d, tol=1e-3, debug=False, debug_name=None, is_closed=True):
    """

    :param local_points2d:
    :param tol:
    :param debug:
    :param debug_name:
    :return:
    """

    segc = SegCreator(local_points2d, tol=tol, debug=debug, debug_name=debug_name, is_closed=is_closed)
    in_loop = True
    while in_loop:
        if segc.radius is not None:
            segc.calc_circle_line()
            if abs(segc.radius) < 1e-5:
                segc._arc_center = None
                segc._arc_start = None
                segc._arc_end = None
                segc._arc_midpoint = None
                segc.calc_line()
            else:
                segc.calc_arc()
        else:
            segc._arc_center = None
            segc._arc_start = None
            segc._arc_end = None
            segc._arc_midpoint = None
            segc.calc_line()

        if segc._i == len(local_points2d) - 1:
            in_loop = False
        else:
            segc.next()

    return segc._seg_list


def make_edges_and_fillet_from_3points(p1, p2, p3, radius):
    from ..occ.utils import make_edge, make_fillet

    edge1 = make_edge(p1[:3], p2[:3])
    edge2 = make_edge(p2[:3], p3[:3])
    ed1, ed2, fillet = make_fillet(edge1, edge2, radius)
    return ed1, ed2, fillet
