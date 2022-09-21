import numpy as np
import pytest

from ada.core.curve_utils import (
    calc_2darc_start_end_from_lines_radius,
    calc_arc_radius_center_from_3points,
    intersect_line_circle,
    get_center_from_3_points_and_radius,
)
from ada.core.utils import roundoff
from ada.core.vector_utils import (
    angle_between,
    intersection_point,
    linear_2dtransform_rotate,
    local_2_global_points,
    unit_vector,
)


def test_basic_arc():
    p1 = (0, 5)
    p2 = (0, 0)
    p3 = (5, 0)
    radius = 0.2

    center, start, end, midp = calc_2darc_start_end_from_lines_radius(p1, p2, p3, radius)
    rcenter, rradius = calc_arc_radius_center_from_3points(start, midp, end)

    v1 = (start, np.array(p1))
    v2 = (np.array(p3), end)
    rp = [roundoff(x) for x in intersection_point(v1, v2)]
    assert rp[0] == p2[0]
    assert rp[1] == p2[1]
    assert radius == rradius
    # assert center[0] == rcenter[0]
    # assert center[1] == rcenter[1]


def test_basic_arc_opposite():
    p1 = (0, -5)
    p2 = (0, 0)
    p3 = (-5, 0)
    radius = 0.2

    center, start, end, midp = calc_2darc_start_end_from_lines_radius(p1, p2, p3, radius)
    rcenter, _rradius = calc_arc_radius_center_from_3points(start, midp, end)

    v1 = (start, np.array(p1))
    v2 = (np.array(p3), end)
    rp = intersection_point(v1, v2)

    assert roundoff(rp[0]) == roundoff(p2[0])
    assert roundoff(rp[1]) == roundoff(p2[1])


def test_basic_arc_rot2():
    p1 = (0, -5)
    p2 = (0, 0)
    p3 = (5, 0)
    radius = 0.2

    center, start, end, midp = calc_2darc_start_end_from_lines_radius(p1, p2, p3, radius)
    rcenter, _rradius = calc_arc_radius_center_from_3points(start, midp, end)

    v1 = (start, np.array(p1))
    v2 = (np.array(p3), end)
    rp = intersection_point(v1, v2)

    assert roundoff(rp[0]) == roundoff(p2[0])
    assert roundoff(rp[1]) == roundoff(p2[1])


def test_basic_arc2():
    origin = (0, 0, 0)
    xdir = (1, 0, 0)
    normal = (0, -1, 0)

    p1 = np.array([-150, 100])
    p2 = np.array([-74, 81])
    p3 = np.array([-20, 0])
    radius = 40

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
    glob_c = local_2_global_points([center], origin, xdir, normal)[0]
    glob_s = local_2_global_points([start], origin, xdir, normal)[0]
    glob_e = local_2_global_points([end], origin, xdir, normal)[0]
    glob_midp = local_2_global_points([midp], origin, xdir, normal)[0]

    res_center = (-98.7039754, 0.0, 45.94493759)
    res_start = (-89.00255040102925, 0, 84.75063760025732)
    res_end = (-65.4219636289688, 0, 68.1329454434532)
    res_midp = (-75.66203793182973, 0, 78.64156001325857)

    for r, e in zip(res_start, glob_s):
        assert roundoff(r, 5) == roundoff(e, 5)

    for r, e in zip(res_end, glob_e):
        assert roundoff(r, 4) == roundoff(e, 4)

    for r, e in zip(res_midp, glob_midp):
        assert roundoff(r, 4) == roundoff(e, 4)

    for r, e in zip(res_center, glob_c):
        assert roundoff(r, 4) == roundoff(e, 4)


def test_center_of_arc_3_points_xy_plane():
    r = 0.19595812499999998
    curve_data = get_center_from_3_points_and_radius((0, 0, 0), (5, 0, 0), (5, 5, 0), r)
    assert sum(curve_data.center - np.array([4.804042, 195.958e-03, 0])) == pytest.approx(0.0)


def test_center_of_arc_3_points_out_of_plane():
    r = 0.19595812499999998
    curve_data = get_center_from_3_points_and_radius((5.2, -0.00404, 3.2), (5.2, 4.8, 3.2), (10.0, 4.8, 5.2), r)
    assert sum(curve_data.center - np.array([5.380884, 4.604042, 3.275369])) == pytest.approx(0.0, abs=1e-6)


def test_center_of_arc_3_points_out_of_plane_2():
    r = 0.19595812499999998
    curve_data = get_center_from_3_points_and_radius((5.38088, 4.8, 3.27537), (10.0, 4.8, 5.2), (10.0, 4.8, 13.2), r)
    assert sum(curve_data.center - np.array([9.804042, 4.8, 5.330639])) == pytest.approx(0.0, abs=1e-6)
