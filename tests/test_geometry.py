import unittest

import numpy as np

from ada.core.utils import (
    calc_2darc_start_end_from_lines_radius,
    calc_arc_radius_center_from_3points,
    global_2_local_nodes,
    intersection_point,
    local_2_global_nodes,
    rotation_matrix_csys_rotate,
    roundoff,
)

# Arbitrary global coordinates
pA = (1, 1, 0)
# Global axis
globx = (1, 0, 0)
globy = (0, 1, 0)
globz = (0, 0, 1)
csysA = np.array([np.array(globx).astype(float), np.array(globy).astype(float), np.array(globz).astype(float)])


class BasicTransforms(unittest.TestCase):
    def test_rotate_about_Z(self):
        origin = (0, 0, 0)
        normal = (0, 0, 1)

        # Start with a 90 degree counter-clockwise rotation (x = pos y)
        xvec = (0, 1, 0)  # Rotate
        yvec = np.cross(normal, xvec).astype(float)
        csys2 = np.array([np.array(xvec).astype(float), yvec, np.array(normal).astype(float)])
        rp2 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csysA, csys2), pA)
        assert tuple([roundoff(x) for x in rp2]) == (1.0, -1.0, 0.0)

        # Rotate another 90 degrees counter-clockwise
        xvec = (-1, 0, 0)
        yvec = np.cross(normal, xvec).astype(float)
        csys3 = np.array([np.array(xvec).astype(float), yvec, np.array(normal).astype(float)])

        rp3 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csysA, csys3), pA)
        assert tuple([roundoff(x) for x in rp3]) == (-1.0, -1.0, 0)

        rp4 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csys3, csysA), rp3)
        assert tuple([roundoff(x) for x in rp4]) == tuple([float(x) for x in pA])


class CoordinateSystems(unittest.TestCase):
    def test_roundtrip_global_coords_2_local(self):
        # Local Coordinate System
        xvec = (1, 0, 0)
        yvec = (0, 0, 1)
        normal = np.cross(xvec, yvec)
        csys2 = [xvec, yvec]

        origin = (0, 0, 0)
        point = (2, -0.3, 2)

        loc_points = global_2_local_nodes(csys2, origin, [point])
        glob_points = local_2_global_nodes(loc_points, origin, xvec, normal)
        ev1 = tuple([roundoff(x) for x in glob_points[0]])
        ev2 = tuple([float(x) for x in point])
        assert ev1 == ev2

    def test_csys_rotation1(self):
        csys1 = [(1, 0, 0), (0, 1, 0)]

        point = (2, -0.3, 2)
        xvec = np.array([1, 0, 0])
        yvec = np.array([0, 0, 1])
        csys2 = [xvec, yvec]

        rm_to_local = rotation_matrix_csys_rotate(csys1, csys2)
        p_local = np.dot(rm_to_local, point)

        rm_to_global = rotation_matrix_csys_rotate(csys2, csys1, inverse=True)
        p_global = np.dot(rm_to_global, p_local)
        ev1 = tuple([roundoff(x) for x in p_global])
        ev2 = tuple([float(x) for x in point])
        ev = ev1 == ev2

        assert ev is True


class ArcGeom(unittest.TestCase):
    def test_basic_arc(self):
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

    def test_basic_arc_opposite(self):
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

    def test_basic_arc_rot2(self):

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

    def test_basic_arc2(self):
        from ada.core.utils import (
            angle_between,
            intersect_line_circle,
            linear_2dtransform_rotate,
            local_2_global_nodes,
            unit_vector,
        )

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
        glob_c = local_2_global_nodes([center], origin, xdir, normal)[0]
        glob_s = local_2_global_nodes([start], origin, xdir, normal)[0]
        glob_e = local_2_global_nodes([end], origin, xdir, normal)[0]
        glob_midp = local_2_global_nodes([midp], origin, xdir, normal)[0]

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


if __name__ == "__main__":
    unittest.main()
