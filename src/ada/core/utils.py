# coding=utf-8
import datetime
import hashlib
import logging
import os
import pathlib
import shutil
import uuid
import warnings
import zipfile
from decimal import ROUND_HALF_EVEN, Decimal

import ifcopenshell
import numpy as np
import plotly.graph_objs as go
from plotly import io as pio

__all__ = [
    "make_box_by_points",
    "make_sphere",
    "angle_between",
    "Counter",
    "clockwise",
    "get_current_user",
    "get_file_size",
    "is_coplanar",
    "make_cylinder_from_points",
    "make_edge",
    "segments_to_indexed_lists",
    "build_polycurve",
    "build_polycurve_occ",
    "rotation_matrix_csys_rotate",
    "face_to_wires",
    "vector_length",
    "roundoff",
    "parallel_check",
    "intersect_calc",
    "get_list_of_files",
    "is_occ_shape",
    "points_in_cylinder",
    "unit_vector",
    "bool2text",
    "make_wire_from_points",
    "rot_matrix",
    "tuple_minus",
    "random_color",
    "get_boundingbox",
    "global_2_local_nodes",
    "local_2_global_nodes",
    "calc_2darc_start_end_from_lines_radius",
    "NewLine",
    "linear_2dtransform_rotate",
    "make_box",
    "easy_plotly",
    "poly_area",
    "normal_to_points_in_plane",
    "is_edges_ok",
    "make_face_w_cutout",
    "make_circle",
    "calc_arc_radius_center_from_3points",
    "rotate_shp_3_axis",
    "download_to",
    "create_guid",
    "segments_to_local_points",
    "zip_dir",
]


# Geometry
def linear_2dtransform_rotate(origin, point, degrees):
    """
    Rotate

    :param origin: (x, y) coordinate of point of rotation
    :param point: (x, y) coordinate of point to rotate
    :param degrees: Degree rotation
    :return: Updated x, y coordinates based on rotation
    """
    theta = np.deg2rad(degrees)
    A = np.column_stack([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
    v = np.array(point) - np.array(origin)
    #    return A @ v.T
    return np.array(origin) + A.dot(v.T)


def rot_matrix_alt(a, b, g, inverse=False, matr_tol=8):
    """

    :param a:
    :param b:
    :param g:
    :param inverse:
    :param matr_tol: Matrix decimal precision tolerance
    :return:
    """

    def R_x(al):
        return np.array([[1, 0, 0], [0, np.cos(al), -np.sin(al)], [0, np.sin(al), np.cos(al)]])

    def R_y(be):
        return np.array([[np.cos(be), 0, np.sin(be)], [0, 1, 0], [-np.sin(be), 0, np.cos(be)]])

    def R_z(ga):
        return np.array([[np.cos(ga), -np.sin(ga), 0], [np.sin(ga), np.cos(ga), 0], [0, 0, 1]])

    rot_mat = np.dot(R_z(g), np.dot(R_y(b), R_x(a)))
    rot_mat = np.around(rot_mat, matr_tol)
    if inverse:
        return rot_mat.T
    else:
        return rot_mat


def rotation_matrix_csys_rotate(csys1_in, csys2_in, inverse=False, use_quaternion=True):
    """
    Create a rotation matrix by providing two sets of coordinate systems defined by 2 vectors each (X- and Y-vectors)

    Resources:

        https://en.wikipedia.org/wiki/Rotation_matrix
        http://kieranwynn.github.io/pyquaternion/

    :param csys1_in: Coordinate system 1 defined by 2 vectors [LocalX, LocalY]
    :param csys2_in: Coordinate system 1 defined by 2 vectors [LocalX, LocalY]
    :param inverse: True if you want to reverse the applied rotations from csys1 to csys2
    :param use_quaternion: Create transformation matrix using the pyquaternion package
    :return: A rotation matrix ready to be employed for further use
    """

    # Ensure Consistent right-hand-rule
    csys1 = np.array([csys1_in[0], csys1_in[1], np.cross(csys1_in[0], csys1_in[1])]).astype(float)
    csys2 = np.array([csys2_in[0], csys2_in[1], np.cross(csys2_in[0], csys2_in[1])]).astype(float)

    if not np.allclose(np.dot(csys1, csys1.conj().transpose()), np.eye(3), rtol=1e-05, atol=1e-08):
        use_quaternion = False

    # Using PyQuaternion
    if use_quaternion:
        from pyquaternion import Quaternion

        q1 = Quaternion(matrix=csys1)
        q2 = Quaternion(matrix=csys2)
        q3 = q1 * q2

        if inverse:
            q3 = q3.inverse

        rm = q3.rotation_matrix
        return rm
    else:
        a = angle_between(csys1[0], csys2[0])
        b = angle_between(csys1[1], csys2[1])
        z = angle_between(csys1[2], csys2[2])

        rm_alt = rot_matrix_alt(a, b, z, inverse)
        return rm_alt


def angle_between(v1, v2):
    """
    Returns the angle in radians between vectors 'v1' and 'v2'

    source:

        https://stackoverflow.com/questions/2827393/angles-between-two-n-dimensional-vectors-in-python/13849249#13849249

    :param v1:
    :param v2:

            >>> angle_between((1, 0, 0), (0, 1, 0))
            1.5707963267948966
            >>> angle_between((1, 0, 0), (1, 0, 0))
            0.0
            >>> angle_between((1, 0, 0), (-1, 0, 0))
            3.141592653589793
    """
    v1_u = unit_vector(v1)
    v2_u = unit_vector(v2)
    return np.arccos(np.clip(np.dot(v1_u, v2_u), -1.0, 1.0))


def rot_matrix(normal, original_normal=(0, 0, 1)):
    """
    Creates a rotation matrix between two normals

    :param normal:
    :param original_normal:
    :return:
    """
    # Check if the two normals are similar
    if [True if abs(x - y) == 0 else False for x, y in zip(normal, original_normal)] == [True, True, True]:
        return np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]])

    M = np.array(normal)
    N = np.array(original_normal)
    costheta = np.dot(M, N) / (np.linalg.norm(M) * np.linalg.norm(N))

    axis = np.cross(M, N) / np.linalg.norm(np.cross(M, N))
    if np.isnan(axis[0]) or np.isnan(axis[1]) or np.isnan(axis[2]):
        raise ValueError("Axis contains isnan members")
    c = costheta
    s = np.sqrt(1 - c * c)
    C = 1 - c
    x, y, z = axis[0], axis[1], axis[2]

    return np.array(
        [
            [x * x * C + c, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, y * y * C + c, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, z * z * C + c],
        ]
    )


def rotate_plane(points, normal, global_normal=(0, 0, 1)):
    """
    Method takes points from local plane rotates plane into global plane and returns points

    .. Note: Assumes rotation about origin (0,0,0)

    :param points:
    :param normal:
    :param global_normal:
    :return:
    """

    rmat = rot_matrix(normal, global_normal)
    newpoints = []
    for pid in points:
        point = points[pid].no.p
        newpoint = np.dot(rmat, point)
        newpoints.append(newpoint)
    return newpoints


def vector_length(vector):
    """
    This method takes in a np.array vector and returns the length
    of the vector.

    :param vector: A numpy array of a 3d vector
    :type vector: np.array
    :rtype: float
    """
    if len(vector) == 2:
        raise ValueError("Vector is not a 3d vector, but a 2d vector. Please consider vector_length_2d() instead")
    elif len(vector) != 3:
        raise ValueError(f"Vector is not a 3d vector. Vector array length: {len(vector)}")

    return float(np.linalg.norm(vector))


def vector_length_2d(vector):
    """
    This method takes in a np.array vector and returns the length
    of the vector.

    :param vector: A numpy array of a 2d vector
    :type vector: np.array
    :rtype: float
    """
    if len(vector) == 3:
        raise ValueError("Vector is not a 2d vector, but a 3d vector. Please consider vector_length() instead")
    elif len(vector) != 2:
        raise ValueError(f"Vector is not a 2d vector. Vector array length: {len(vector)}")

    return float(np.linalg.norm(vector))


def distfunc(x, point, A, B):
    """
    A function of x for the distance between point A on vector AB and arbitrary point C. X is a scalar multiplied with
    AB vector based on the distance to C.

    f(x) = np.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)

    vector = C-(A+x*AB)

    :param x: Variable x
    :type x:
    :param point: Arbitrary point parallell to AB vector
    :type point:
    :param A: Point A on AB vector
    :type A:
    :param B: Point B on AB vector
    :type B:
    :return: Vector length
    :rtype:
    """
    AB = B - A
    return vector_length(point - (A + x * AB))


def parallel_check(ab, cd, tol=0.0001):
    """
    Check if vectors AB and CD are parallel

    :param ab: Vector AB
    :type ab: np.array
    :param cd: Vector CD
    :type cd: np.array
    :param tol: Alignment tolerance
    :return: True or False
    :rtype: bool
    """
    return True if np.abs(np.sin(angle_between(ab, cd))) < tol else False


def intersect_calc(A, C, AB, CD):
    """
    Function for evaluating an intersection point between two vector-lines (AB & CD).  The function returns
    variables s & t denoting the scalar value multiplied with the two vector equations A + s*AB = C + t*CD.

    :param A:
    :type A:
    :param C:
    :type C:
    :param AB:
    :type AB:
    :param CD:
    :type CD:
    """
    # Setting up the equation for use in linalg.lstsq
    a = np.array((AB, -CD)).T
    b = C - A

    st = np.linalg.lstsq(a, b, rcond=None)

    s = st[0][0]
    t = st[0][1]
    return s, t


def intersection_point(v1, v2):
    """
    Get the coordinate of the intersecting point
    :param v1:
    :param v2:
    :return:
    """
    if len(list(v1[0])) == 2:
        is2d = True
    else:
        is2d = False

    v1 = [np.array(list(v) + [0.0]) for v in list(v1)] if is2d else v1
    v2 = [np.array(list(v) + [0.0]) for v in list(v2)] if is2d else v2
    v1_ = v1[1] - v1[0]
    v2_ = v2[1] - v2[0]
    p1 = v1[0]
    p3 = v2[0]
    s, t = intersect_calc(p1, p3, v1_, v2_)
    res = p1 + s * v1_
    if is2d:
        return res[0], res[1]
    else:
        return res


def normalize(curve):
    if type(curve) is tuple:
        return (curve[0], [y / max(abs(curve[1])) for y in curve[1]])
    else:
        return [x / max(abs(curve)) for x in curve]


def is_point_inside_bbox(p, bbox, tol=1e-3):
    """

    :param p: Point
    :param bbox: Bounding box
    :param tol: Tolerance
    :return:
    """
    if (
        bbox[0][0] - tol < p[0] < bbox[0][1] + tol
        and bbox[1][0] - tol < p[1] < bbox[1][1] + tol
        and bbox[2][0] - tol < p[2] < bbox[2][1] + tol
    ):
        return True
    else:
        return False


def points_in_cylinder(pt1, pt2, r, q):
    """

    :param pt1: Start point of cylinder
    :param pt2: End point of cylinder
    :param r: Radius of cylinder
    :param q:
    :return:
    """
    vec = pt2 - pt1
    const = r * np.linalg.norm(vec)
    if np.dot(q - pt1, vec) >= 0 >= np.dot(q - pt2, vec) and np.linalg.norm(np.cross(q - pt1, vec)) <= const:
        return True
    else:
        return False


def split(u, v, points):
    """

    :param u: Vector 1
    :param v: Vector 2
    :param points:
    :return:
    """

    # return points on left side of UV
    return [p for p in points if np.cross(p - u, v - u) < 0]


def extend(u, v, points):
    if not points:
        return []

    # find furthest point W, and split search to WV, UW
    w = min(points, key=lambda p: np.cross(p - u, v - u))
    p1, p2 = split(w, v, points), split(u, w, points)
    return extend(w, v, p1) + [w] + extend(u, w, p2)


def convex_hull(points):
    # find two hull points, U, V, and split to left and right search
    u = min(points, key=lambda p: p[0])
    v = max(points, key=lambda p: p[0])
    left, right = split(u, v, points), split(v, u, points)

    # find convex hull on each side
    return [v] + extend(u, v, left) + [u] + extend(v, u, right) + [v]


def s_curve(ramp_up_t, ramp_down_t, magnitude, sustained_time=0.0):
    """
    A function created to

    :param ramp_up_t:
    :param ramp_down_t:
    :param magnitude:
    :param sustained_time:
    :return: tuple of X and Y lists describing a S-Curved ramp up and ramp down.
    """
    yp = np.array([0.0, 0.1, 1.0, 1.0]) * magnitude
    if ramp_up_t is not None:
        xp1 = np.array([0.0, ramp_up_t / 2, ramp_up_t / 2, ramp_up_t])
        x1, y1 = Bezier(list(zip(xp1, yp))).T
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
        x2, y2 = Bezier(list(zip(xp2, yp))).T
    else:
        x2, y2 = None, None

    if ramp_down_t is None and ramp_up_t is not None:
        total_curve = x1, x2
    elif ramp_down_t is not None and ramp_up_t is None:
        total_curve = x2, y2[::-1]
    else:
        total_curve = np.append(x1, x2[1:] + x1[-1]), np.append(y1, y2[::-1][1:])

    return total_curve


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

    p1 = p1 if type(p1) is np.ndarray else np.array(p1)
    p2 = p2 if type(p2) is np.ndarray else np.array(p2)
    p3 = p3 if type(p3) is np.ndarray else np.array(p3)

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


def build_polycurve(local_points2d, tol=1e-3, debug=False, debug_name=None):
    """

    :param local_points2d:
    :param tol:
    :param debug:
    :param debug_name:
    :return:
    """

    segc = SegCreator(local_points2d, tol=tol, debug=debug, debug_name=debug_name)
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
            from ada.config import Settings

            self._debug_path = Settings.debug_dir

            if os.path.isdir(Settings.debug_dir) is False:
                os.makedirs(Settings.debug_dir, exist_ok=True)
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


def is_coplanar(x1, y1, z1, x2, y2, z2, x3, y3, z3, x4, y4, z4):
    """
    Python program to check if 4 points in a 3-D plane are Coplanar
    Function to find equation of plane.

    :param x1:
    :param y1:
    :param z1:
    :param x2:
    :param y2:
    :param z2:
    :param x3:
    :param y3:
    :param z3:
    :param x4:
    :param y4:
    :param z4:
    :return:
    """
    a1 = x2 - x1
    b1 = y2 - y1
    c1 = z2 - z1
    a2 = x3 - x1
    b2 = y3 - y1
    c2 = z3 - z1
    a = b1 * c2 - b2 * c1
    b = a2 * c1 - a1 * c2
    c = a1 * b2 - b1 * a2
    d = -a * x1 - b * y1 - c * z1
    # equation of plane is: a*x + b*y + c*z = 0 #
    # checking if the 4th point satisfies
    # the above equation
    if a * x4 + b * y4 + c * z4 + d == 0:
        # if(a * x + b * y + c * z + d < 0.0001 and a * x + b * y + c * z + d > -0.0001):
        # print("Coplanar")
        return True
    else:
        # print(a * x + b * y + c * z + d)
        return False
        # print("Not Coplanar")


def poly_area(x, y):
    """
    Shoelace formula based on stackoverflow

    https://stackoverflow.com/questions/24467972/calculate-area-of-polygon-given-x-y-coordinates

    :param x:
    :param y:
    :return:
    """
    return 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def global_2_local_nodes(csys, origin, nodes):
    """

    :param csys: List of tuples containing; [LocalX, LocalY]
    :param origin: Point of origin. Node object
    :param nodes: list of nodes
    :return: List of local node coordinates
    """
    from ada import Node

    global_csys = [(1, 0, 0), (0, 1, 0)]
    rmat = rotation_matrix_csys_rotate(global_csys, csys)

    if type(origin) is Node:
        origin = origin.p
    elif type(origin) in (list, tuple):
        origin = np.array(origin)

    if type(nodes[0]) is Node:
        nodes = [no.p for no in nodes]

    res = [np.dot(rmat, p) + origin for p in nodes]

    return [r for r in res]


def local_2_global_nodes(nodes, origin, xdir, normal):
    """
    A method for converting a list of nodes (points) in a 2d coordinate system to global 3d coordinates

    :param normal: Normal to 2d plane
    :param origin: Origin of local coordinate system
    :param nodes: List of points in 2d coordinate system
    :param xdir: Local X-direction
    :return:
    """
    from ada import Node
    from ada.core.constants import X, Y

    if type(origin) is Node:
        origin = origin.p

    if type(nodes[0]) is Node:
        nodes = [no.p for no in nodes]

    nodes = [np.array(n, dtype=np.float64) if len(n) == 3 else np.array(list(n) + [0], dtype=np.float64) for n in nodes]
    normal = np.array(normal, dtype=np.float64) if type(normal) in (list, tuple) else normal
    yvec = np.array([x if abs(x) != 0.0 else 0.0 for x in np.cross(normal, xdir)], dtype=np.float64)

    rmat = rotation_matrix_csys_rotate([xdir, yvec], [X, Y], inverse=True)

    return [np.array(origin, dtype=np.float64) + np.dot(rmat, n) for n in nodes]


def normal_to_points_in_plane(points):
    """

    :param points: List of Node objects
    :return:
    """
    p1 = points[0]
    p2 = points[1]
    p3 = points[2]

    # These two vectors are in the plane
    v1 = p3 - p1
    v2 = p2 - p1

    if parallel_check(v1, v2) is True:
        for i in range(3, len(points)):
            p3 = points[i]
            v1 = p3 - p1
            if parallel_check(v1, v2) is False:
                break

    # the cross product is a vector normal to the plane

    n = np.array([x if abs(x) != 0.0 else 0.0 for x in list(np.cross(v1, v2))])
    if n[2] < 0.0:
        n *= -1

    if n[2] == 0 and n[1] == -1:
        n *= -1

    if n[2] == 0 and n[0] == -1:
        n *= -1

    if n.any() == 0.0:
        raise ValueError("Error in calculating plate normal")

    return np.array([x if abs(x) != 0.0 else 0.0 for x in list(unit_vector(n))])


def Bernstein(n, k):
    """Bernstein polynomial."""
    from scipy.special._ufuncs import binom

    coeff = binom(n, k)

    def _bpoly(x):
        return coeff * x ** k * (1 - x) ** (n - k)

    return _bpoly


def Bezier(points, num=200):
    """Build Bezier curve from points."""
    N = len(points)
    t = np.linspace(0, 1, num=num)
    curve = np.zeros((num, 2))
    for ii in range(N):
        curve += np.outer(Bernstein(N - 1, ii)(t), points[ii])
    return curve


def unit_vector(vector):
    """
    Returns the unit vector of a given vector.

    :param vector: A vector
    :type vector: np.ndarray
    """
    norm = vector / np.linalg.norm(vector)
    if np.isnan(norm).any():
        raise ValueError(f'Error trying to normalize vector "{vector}"')

    return norm


def clockwise(points):
    """

    :param points:
    :return:
    """
    psum = 0
    for p1, p2 in zip(points[:-1], points[1:]):
        psum += (p2[0] - p1[0]) * (p2[1] + p1[1])
    psum += (points[-1][0] - points[0][0]) * (points[-1][1] + points[0][1])
    if psum < 0:
        return False
    else:
        return True


def get_midpoint_of_arc(edge):
    res = divide_edge_by_nr_of_points(edge, 3)
    return res[1][1].X(), res[1][1].Y(), res[1][1].Z()


# Other


def get_now():
    d = datetime.datetime.now(datetime.timezone.utc).astimezone()
    date_in_str = "%a, %d %b %Y %H:%M:%S %Z %z"
    now_str = d.strftime(date_in_str)
    return now_str


def interpret_now(date_string):
    return datetime.datetime.strptime(date_string, "%a, %d %b %Y %H:%M:%S %Z %z")


def copy_bulk(files, destination_dir, substitution_map=None):
    """
    Use shutil to copy a list of files to a specified destination directory. Can also parse in a substition map (a
    dict with key: value substitution for specified files

    :param files:
    :param destination_dir:
    :param substitution_map:
    :return:
    """
    import os
    import shutil
    import time

    if os.path.isdir(destination_dir):
        shutil.rmtree(destination_dir)
        time.sleep(1)
    os.makedirs(destination_dir, exist_ok=True)

    for f in files:
        fname = os.path.basename(f)
        dest_file = os.path.join(destination_dir, fname)
        edited = False
        if substitution_map is not None:
            if fname in substitution_map.keys():
                edited = True
                with open(f, "r") as d:
                    in_str = d.read()
                in_str = in_str.replace(substitution_map[fname][0], substitution_map[fname][1])
                with open(dest_file, "w") as d:
                    d.write(in_str)
        if edited is False:
            shutil.copy(f, dest_file)


def create_date_str(include_minutes=False):
    """
    Function for building a formatted timestamp string

    :param include_minutes: Extends the timing format to include minutes
    :return: formatted string <year>_<month>_<day> + optionally (_<hour>_<minute>)
    """
    du = datetime.datetime.now()
    dyear = str(du.year)
    dyear2 = "" + dyear[2] + "" + dyear[3]
    dmonth = "{:02d}".format(du.month)
    dday = "{:02d}".format(du.day)
    day_format = "" + str(dyear2) + "" + str(dmonth) + "" + str(dday)
    if include_minutes:
        day_format += "_" + str(du.hour) + ":" + str(du.minute)
    return day_format


def curve_fitting(in_data):
    """

    :param in_data:
    :return:
    """
    from scipy.optimize import curve_fit

    xData = np.array(in_data[0])
    yData = np.array(in_data[1])

    # generate initial parameter values
    geneticParameters = generate_Initial_Parameters(xData, yData)

    # curve fit the test data
    fittedParameters, pcov = curve_fit(curve_f1, xData, yData, geneticParameters)

    logging.debug("Parameters", fittedParameters)

    modelPredictions = curve_f1(xData, *fittedParameters)

    absError = modelPredictions - yData

    SE = np.square(absError)  # squared errors
    MSE = np.mean(SE)  # mean squared errors
    RMSE = np.sqrt(MSE)  # Root Mean Squared Error, RMSE
    Rsquared = 1.0 - (np.var(absError) / np.var(yData))
    print("RMSE:", RMSE)
    print("R-squared:", Rsquared)
    print()
    return fittedParameters


def curve_f1(x, a, b, Offset):
    """
    A base function for use in curve fitting

    # Sigmoid A With Offset from zunzun.com

    :param x:
    :param a:
    :param b:
    :param Offset:
    :return:
    """
    return 1.0 / (1.0 + np.exp(-a * (x - b))) + Offset


def curve_f2(x, a, b, c):
    """
    A base function for use in curve fitting



    :param x:
    :param a:
    :param b:
    :param c:
    :return:
    """
    return a * np.exp(-b * x) + c


def curve_f3(x, a, b):
    """
    A base function for use in curve fitting



    :param x:
    :param a:
    :param b:
    :return:
    """
    return a * np.exp(b * x)


def curve_f4(x, a, b, c):
    """
    A base function for use in curve fitting

    :param x:
    :param a:
    :param b:
    :param c:
    :return:
    """
    return a * x ** 3 + b * x ** 2 + c * x


def sumOfSquaredError(parameterTuple, *args):
    """
    function for genetic algorithm to minimize (sum of squared error)

    :param xData:
    :param yData:
    :param parameterTuple:
    :return:
    """
    import warnings

    xData = args[0]
    yData = args[1]
    if xData is None:
        logging.error("Xdata is none. Returning None")
        return None
    warnings.filterwarnings("ignore")
    val = curve_f1(xData, *parameterTuple)
    return np.sum((yData - val) ** 2.0)


def generate_Initial_Parameters(xData, yData):
    from scipy.optimize import differential_evolution

    # min and max used for bounds
    maxX = max(xData)
    minX = min(xData)
    maxY = max(yData)
    # minY = min(yData)

    parameterBounds = []
    parameterBounds.append([minX, maxX])  # seach bounds for a
    parameterBounds.append([minX, maxX])  # seach bounds for b
    parameterBounds.append([0.0, maxY])  # seach bounds for Offset

    # "seed" the numpy random number generator for repeatable results
    result = differential_evolution(sumOfSquaredError, parameterBounds, args=[xData, yData], seed=3)
    return result.x


class NewLine:
    def __init__(self, n, prefix=None, suffix=None):
        self.i = 0
        self.n = n
        self.prefix = prefix
        self.suffix = suffix

    def __iter__(self):
        return self

    def __next__(self):
        if self.i < self.n:
            self.i += 1
            return ""
        else:
            self.i = 0
            prefix = self.prefix if self.prefix is not None else ""
            suffix = self.suffix if self.suffix is not None else ""
            return prefix + "\n" + suffix


class Counter:
    def __init__(self, start=1, prefix=None):
        self.i = start
        self._prefix = prefix

    def set_i(self, i):
        self.i = i

    def __iter__(self):
        return self

    def __next__(self):
        self.i += 1
        return self.i if self._prefix is None else f"{self._prefix}{self.i}"


class SIZE_UNIT:
    """
    Enum for size units
    """

    BYTES = 1
    KB = 2
    MB = 3
    GB = 4


def convert_unit(size_in_bytes, unit):
    """ Convert the size from bytes to other units like KB, MB or GB"""
    if unit == SIZE_UNIT.KB:
        return size_in_bytes / 1024
    elif unit == SIZE_UNIT.MB:
        return size_in_bytes / (1024 * 1024)
    elif unit == SIZE_UNIT.GB:
        return size_in_bytes / (1024 * 1024 * 1024)
    else:
        return size_in_bytes


def get_file_size(file_name, size_type=SIZE_UNIT.MB):
    """ Get file in size in given unit like KB, MB or GB"""
    size = os.path.getsize(file_name)
    return convert_unit(size, size_type)


def random_color():
    from random import randint

    from OCC.Display.WebGl.jupyter_renderer import format_color

    return format_color(randint(0, 255), randint(0, 255), randint(0, 255))


def d2npy(node):
    """
    This method takes in a node object and returns a np.array.

    :param node: Node Object
    :type node: Node
    :rtype: np.array
    :return: Numpy node
    """
    return np.array([node.x, node.y, node.z], dtype=np.float)


def roundoff(x, precision=6):
    """

    :param x: Number
    :param precision: Number precision
    :return:
    """
    import warnings

    warnings.filterwarnings(action="error", category=np.ComplexWarning)
    xout = float(Decimal(float(x)).quantize(Decimal("." + precision * "0" + "1"), rounding=ROUND_HALF_EVEN))
    return xout if abs(xout) != 0.0 else 0.0


def get_short_path_name(long_name):
    """
    Gets the short path name of a given long path.

    http://stackoverflow.com/a/23598461/200291
    """
    import ctypes
    from ctypes import wintypes

    _GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
    _GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _GetShortPathNameW.restype = wintypes.DWORD

    output_buf_size = 0
    while True:
        output_buf = ctypes.create_unicode_buffer(output_buf_size)
        needed = _GetShortPathNameW(long_name, output_buf, output_buf_size)
        if output_buf_size >= needed:
            return output_buf.value
        else:
            output_buf_size = needed


def get_unc_path(path_):
    """
    Will try to convert path string to UNC path

    :param path_:
    :return:
    """
    import win32wnet

    if path_[0].lower() == "c":
        return path_
    else:
        try:
            out_path = win32wnet.WNetGetUniversalName(path_)
            return out_path
        except BaseException as e:
            logging.error(e)
            return path_


def in_ipynb():
    try:
        from IPython import get_ipython

        get_ipython()
        return True
    except NameError:
        return False


def tuple_minus(t):
    return tuple([-roundoff(x) if x != 0.0 else 0.0 for x in t])


def easy_plotly(
    title,
    in_data,
    xlbl="X-axis",
    ylbl="Y-axis",
    xrange=None,
    yrange=None,
    yaxformat="E",
    legend=None,
    autoreverse=False,
    save_filename=None,
    mode="lines",
    marker="circle",
    traces=None,
    template="plotly_white",
    annotations=None,
    shapes=None,
    renderer="notebook_connected",
    return_widget=True,
):
    """
    A Plotly template for quick and easy interactive scatter plotting using some pre-defined values. If you need more
    control of the plotly plot, you are probably better off using plotly directly

    See https://plot.ly/python/reference/#scatter for a complete list of input for

    :param title: Plot title
    :param in_data: tuple (x, y) for single plots or dict {'var1':{'x': [..], 'y': [..] }, 'var2': {..}, etc..}
    :param xlbl: X-axis label
    :param ylbl: Y-axis label
    :param xrange: min and max values of x-axis
    :param yrange: min and max values of y-axis
    :param yaxformat: "none" | "e" | "E" | "power" | "SI" | "B" (default) exponent format of y-axis.
    :param legend: dict(x=-.1, y=1.2)
    :param autoreverse: Autoreverse the X-axis (opposed to inputting the reversed x-list)
    :param save_filename: Abs path to file location or file name of figure.
    :param mode:
    :param marker:
    :param traces: Add plotly traces manually
    :param template: Which plot template. Default is 'plotly_white'. Alternatives are shown below
    :param annotations:
    :param renderer: Which renderer should be used. Default is 'notebook_connected'. See below for alternatives
    :param return_widget:
    :type title: str
    :type xlbl: str
    :type ylbl: str
    :type xrange: list
    :type yrange: list
    :type yaxformat: str
    :type save_filename: str
    :type mode: str

    Templates:
                'ggplot2', 'seaborn', 'plotly', 'plotly_white', 'plotly_dark', 'presentation', 'xgridoff', 'none'

    renderers:
                'plotly_mimetype', 'jupyterlab', 'nteract', 'vscode', 'notebook', 'notebook_connected', 'kaggle',
                'azure', 'colab', 'cocalc', 'databricks', 'json', 'png', 'jpeg', 'jpg', 'svg', 'pdf', 'browser',
                'firefox', 'chrome', 'chromium', 'iframe', 'iframe_connected', 'sphinx_gallery'

    """

    plot_data = []
    if type(in_data) is dict:
        for key in in_data.keys():
            if type(in_data[key]) is dict:
                x_ = in_data[key]["x"]
                y_ = in_data[key]["y"]
            elif type(in_data[key]) is tuple:
                x_ = in_data[key][0]
                y_ = in_data[key][1]
            else:
                raise Exception('unrecognized input in dict "{}"'.format(type(in_data[key])))

            trace = go.Scatter(
                x=x_,
                y=y_,
                name=key,
                mode=mode,
                marker=dict(symbol=marker),
            )
            plot_data.append(trace)
    elif type(in_data) in [list, tuple]:
        x, y = in_data
        trace = go.Scatter(
            x=x,
            y=y,
            mode=mode,
            marker=dict(symbol=marker),
        )
        plot_data.append(trace)
    else:
        if traces is None:
            raise Exception('No Recognized input type found for "in_data" or "traces"')
    if traces is not None:
        plot_data += traces
    autorange = "reversed" if autoreverse is True else None
    layout = go.Layout(
        title=title,
        xaxis=dict(
            title=xlbl,
            titlefont=dict(family="Arial, monospace", size=18, color="#7f7f7f"),
            autorange=autorange,
            range=xrange,
        ),
        yaxis=dict(
            title=ylbl,
            titlefont=dict(family="Arial, monospace", size=18, color="#7f7f7f"),
            range=yrange,
            exponentformat=yaxformat,
        ),
        legend=legend,
        template=template,
        shapes=shapes,
    )
    if annotations is not None:
        layout["annotations"] = annotations
    fig = go.FigureWidget(data=plot_data, layout=layout)
    # plotly.offline.init_notebook_mode(connected=True)
    if save_filename is not None:
        # fig.show(renderer=renderer)
        filepath = save_filename
        if ".png" not in filepath:
            filepath += ".png"

        dirpath = os.path.dirname(filepath)
        print('Saving "{}" to "{}"'.format(os.path.basename(filepath), dirpath))
        filename = os.path.splitext(filepath)[0].replace(dirpath + "\\", "")
        if os.path.isdir(dirpath) is False:
            os.makedirs(dirpath)
        pio.write_image(fig, save_filename, width=1600, height=800)
        if "\\" not in save_filename:
            output_file = pathlib.Path(f"C:/ADA/temp/{filename}.png")
            if os.path.isfile(output_file) is True:
                shutil.move(output_file, dirpath + "\\" + filename + ".png")
            else:
                print("{} not found".format(output_file))

    else:
        if return_widget is True:
            return fig
        fig.show(renderer=renderer)


def get_current_user():
    """

    :return: Name of current user
    """
    import getpass

    return getpass.getuser()


def get_list_of_files(dir_name, file_ext=None, strict=False):
    """
    Get a list of file and sub directories for a given directory

    :param dir_name: Parent directory in which the recursive search for files will take place
    :param file_ext: File extension
    :param strict: If True the function raiser errors when no files are found.
    :return:
    """

    list_of_file = os.listdir(dir_name)
    all_files = list()

    # Iterate over all the entries
    for entry in list_of_file:
        # Create full path
        full_path = os.path.join(dir_name, entry)
        # If entry is a directory then get the list of files in this directory
        if os.path.isdir(full_path):
            all_files = all_files + get_list_of_files(full_path)
        else:
            all_files.append(full_path)

    if file_ext is not None:
        all_files = [f for f in all_files if f.endswith(file_ext)]

    if not all_files:
        msg = f'Files with "{file_ext}"-extension is not found in "{dir_name}" or any sub-folder.'
        if strict:
            raise FileNotFoundError(msg)
        else:
            warnings.warn(msg)

    return all_files


def getfileprop(filepath):
    """
    Read all properties of a given exe file return them as a dictionary.

    :param filepath:
    :type filepath: str
    :return:
    :rtype: dict
    """
    import win32api

    filepath = str(filepath)
    propNames = (
        "Comments",
        "InternalName",
        "ProductName",
        "CompanyName",
        "LegalCopyright",
        "ProductVersion",
        "FileDescription",
        "LegalTrademarks",
        "PrivateBuild",
        "FileVersion",
        "OriginalFilename",
        "SpecialBuild",
    )

    props = {"FixedFileInfo": None, "StringFileInfo": None, "FileVersion": None}

    try:
        # backslash as parm returns dictionary of numeric info corresponding to VS_FIXEDFILEINFO struc
        fixedInfo = win32api.GetFileVersionInfo(filepath, "\\")
        props["FixedFileInfo"] = fixedInfo
        props["FileVersion"] = "%d.%d.%d.%d" % (
            fixedInfo["FileVersionMS"] / 65536,
            fixedInfo["FileVersionMS"] % 65536,
            fixedInfo["FileVersionLS"] / 65536,
            fixedInfo["FileVersionLS"] % 65536,
        )

        # \VarFileInfo\Translation returns list of available (language, codepage)
        # pairs that can be used to retreive string info. We are using only the first pair.
        lang, codepage = win32api.GetFileVersionInfo(filepath, "\\VarFileInfo\\Translation")[0]

        # any other must be of the form \StringfileInfo\%04X%04X\parm_name, middle
        # two are language/codepage pair returned from above

        strInfo = {}
        for propName in propNames:
            strInfoPath = "\\StringFileInfo\\%04X%04X\\%s" % (lang, codepage, propName)
            strInfo[propName] = win32api.GetFileVersionInfo(filepath, strInfoPath)

        props["StringFileInfo"] = strInfo
    except Exception as e:
        logging.error(f'Unable to Read file properties due to "{e}"')
        pass

    return props


def get_file_time_local(file_path):
    from datetime import datetime, timezone

    utc_time = datetime.fromtimestamp(os.path.getmtime(file_path), timezone.utc)
    return utc_time.astimezone()


def get_time_stamp_now():
    import pytz

    utc = pytz.UTC
    return utc.localize(datetime.datetime.utcnow())


def path_leaf(path):
    import ntpath

    head, tail = ntpath.split(path)
    return tail or ntpath.basename(head)


def thread_this(list_in, function, cpus=4):
    """
    Make a function (which only takes in a list) to run on multiple processors

    :param list_in:
    :param function:
    :param cpus:
    :return:
    """
    import multiprocessing
    from functools import partial

    var = int(len(list_in) / cpus)
    blocks = [list_in[:var]]
    for i in range(1, cpus - 1):
        blocks.append(list_in[var * i : (i + 1) * var])

    blocks.append(list_in[(cpus - 1) * var :])
    pool = multiprocessing.Pool()
    func = partial(function)
    res = pool.map(func, blocks)
    pool.close()
    pool.join()
    # Join results from the various processes
    out_res = []
    for r in res:
        out_res += r
        print(r)
    return out_res


def download_to(destination, url, file_override_ok=False):
    """

    :param destination: Destination file path
    :param url: Url of file subject for download
    :param file_override_ok: Download and write over existing file
    """
    import urllib.request

    destination = pathlib.Path(destination)
    os.makedirs(destination.parent, exist_ok=True)

    if destination.exists() and file_override_ok is False:
        print("The destination file already exists. Will skip download again")
        return

    if destination.exists() is False:
        with urllib.request.urlopen(url) as response, open(destination, "wb") as out_file:
            shutil.copyfileobj(response, out_file)


def bool2text(in_str):
    return "YES" if in_str is True else "NO"


def traverse_hdf_datasets(hdf_file):
    """Traverse all datasets across all groups in HDF5 file."""

    import h5py

    def h5py_dataset_iterator(g, prefix=""):
        for key in g.keys():
            item = g[key]
            path = "{}/{}".format(prefix, key)
            if isinstance(item, h5py.Dataset):  # test for dataset
                yield (path, item)
            elif isinstance(item, h5py.Group):  # test for group (go down)
                yield from h5py_dataset_iterator(item, path)

    with h5py.File(hdf_file, "r") as f:
        for (path, dset) in h5py_dataset_iterator(f):
            print(path, dset)

    return None


def zip_it(filepath):
    import pathlib
    import zipfile

    fp = pathlib.Path(filepath)
    with zipfile.ZipFile(fp.with_suffix(".zip"), "w") as zip_archive:
        zip_archive.write(fp, arcname=fp.name, compress_type=zipfile.ZIP_DEFLATED)


def zip_dir(directory, zip_path, incl_only=None):
    """

    :param directory: Directory path subject for zipping
    :param zip_path: Destination path of zip file
    :param incl_only: (optional) List of suffixes that all files should have in order to be included in the zip file
    :return:
    """

    directory = pathlib.Path(directory)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_archive:
        for root, dirs, files in os.walk(directory):
            for file in files:
                if incl_only is not None:
                    keep = False
                    suffix = file.split(".")[-1]
                    if "." + suffix in incl_only:
                        keep = True
                    if keep is False:
                        continue
                zip_archive.write(
                    os.path.join(root, file),
                    os.path.relpath(os.path.join(root, file), os.path.join(directory, "..")),
                    # compress_type=zipfile.ZIP_DEFLATED
                )


def unzip_it(zip_path, extract_path=None):
    import pathlib
    import zipfile

    fp = pathlib.Path(zip_path)
    if extract_path is None:
        extract_path = fp.parents[0]
    with zipfile.ZipFile(fp, "r") as zip_archive:
        zip_archive.extractall(extract_path)


# OCC
def is_edges_ok(edge1, fillet, edge2):
    from OCC.Extend.TopologyUtils import TopologyExplorer

    t1 = TopologyExplorer(edge1).number_of_vertices()
    t2 = TopologyExplorer(fillet).number_of_vertices()
    t3 = TopologyExplorer(edge2).number_of_vertices()

    if t1 == 0 or t2 == 0 or t3 == 0:
        return False
    else:
        return True


def make_wire_from_points(points):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.gp import gp_Pnt
    from OCC.Extend.ShapeFactory import make_wire

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


def get_boundingbox(shape, tol=1e-6, use_mesh=True):
    """

    :param shape: TopoDS_Shape or a subclass such as TopoDS_Face the shape to compute the bounding box from
    :param tol: tolerance of the computed boundingbox
    :param use_mesh: a flag that tells whether or not the shape has first to be meshed before the bbox computation.
                     This produces more accurate results
    :return: return the bounding box of the TopoDS_Shape `shape`
    """
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib_Add
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh

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
    from OCC.Core.TopoDS import (
        TopoDS_Compound,
        TopoDS_Shape,
        TopoDS_Shell,
        TopoDS_Solid,
        TopoDS_Vertex,
        TopoDS_Wire,
    )

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
    from OCC.Extend.TopologyUtils import TopologyExplorer

    topo_exp = TopologyExplorer(face)
    wires = list()
    for w in topo_exp.wires_from_face(face):
        wires.append(w)
    return wires


def make_fillet(edge1, edge2, bend_radius):
    from OCC.Core.BRep import BRep_Tool_Pnt
    from OCC.Core.ChFi2d import ChFi2d_AnaFilletAlgo
    from OCC.Core.gp import gp_Dir, gp_Pln, gp_Vec
    from OCC.Extend.TopologyUtils import TopologyExplorer

    f = ChFi2d_AnaFilletAlgo()

    points1 = get_points_from_edge(edge1)
    # vec1 = unit_vector(np.array(points1[-1]) - np.array(points1[0]))

    points2 = get_points_from_edge(edge2)
    # vec2 = unit_vector(np.array(points2[-1]) - np.array(points2[0]))

    # par = parallel_check(vec1, vec2)
    normal = normal_to_points_in_plane([np.array(x) for x in points1] + [np.array(x) for x in points2])
    # normal = unit_vector(np.cross(vec1, vec2))

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
    from OCC.Core.BRep import BRep_Tool_Pnt
    from OCC.Extend.TopologyUtils import TopologyExplorer

    texp1 = TopologyExplorer(edge)
    points = []
    for v in texp1.vertices():
        apt = BRep_Tool_Pnt(v)
        points.append((apt.X(), apt.Y(), apt.Z()))
    return points


def make_closed_polygon(*args):
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakePolygon

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


def make_n_sided(edges):
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
    from OCC.Core.BRepFill import BRepFill_Filling
    from OCC.Core.GeomAbs import GeomAbs_C0

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
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Pnt

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
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt

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

    return BRepBuilderAPI_MakeEdge(gp_Pnt(*[float(x) for x in p1[:3]]), gp_Pnt(*[float(x) for x in p2[:3]])).Edge()


def make_vector(name, origin, csys, parent, pnt_r=0.2, cyl_l=0.3, cyl_r=0.2, units="m"):
    """
    Visualize a plates locale coordinate system and node numbering.

    :param name:
    :param origin:
    :param csys:
    :param cyl_l:
    :param cyl_r:
    :return:
    """
    from ada import PrimCyl, PrimSphere

    origin = np.array(origin)
    parent.add_shape(PrimSphere(name + "_origin", origin, pnt_r, units=units, metadata=dict(origin=origin)))
    parent.add_shape(
        PrimCyl(
            name + "_X",
            origin,
            origin + np.array(csys[0]) * cyl_l,
            cyl_r,
            units=units,
            colour="RED",
        )
    )
    parent.add_shape(
        PrimCyl(
            name + "_Y",
            origin,
            origin + np.array(csys[1]) * cyl_l,
            cyl_r,
            units=units,
            colour="GREEN",
        )
    )
    parent.add_shape(
        PrimCyl(
            name + "_Z",
            origin,
            origin + np.array(csys[2]) * cyl_l,
            cyl_r,
            units=units,
            colour="BLUE",
        )
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


def align_to_plate(plate):
    """

    :param plate:
    :type plate: ada.Plate
    :return:
    """
    normal = plate.poly.normal
    h = plate.t * 5
    origin = plate.poly.origin - h * normal * 1.1 / 2
    xdir = plate.poly.xdir
    return dict(h=h, normal=normal, origin=origin, xdir=xdir)


def align_to_beam(beam):
    """

    :param beam:
    :type beam: ada.Beam
    :return:
    """
    ymin = beam.yvec * np.array(beam.bbox[0])
    ymax = beam.yvec * np.array(beam.bbox[1])
    origin = beam.n1.p - ymin * 1.1
    normal = -beam.yvec
    xdir = beam.xvec
    h = vector_length(ymax - ymin) * 1.2
    return dict(h=h, normal=normal, origin=origin, xdir=xdir)


def create_guid(name=None):
    if name is None:
        hexdig = uuid.uuid1().hex
    else:
        if type(name) != bytes:
            n = name.encode()
        else:
            n = name
        hexdig = hashlib.md5(n).hexdigest()
    result = ifcopenshell.guid.compress(hexdig)
    return result


def split_beam(bm, fraction):
    """
    TODO: Should this insert something in the beam metadata which a mesh-algo can pick up or two separate beam objects?

    :param bm:
    :param fraction: Fraction of beam length (from n1)
    :type bm: ada.Beam
    :return:
    """
    raise NotImplementedError()
    # nmid = bm.n1.p + bm.xvec * bm.length * fraction


def are_plates_touching(pl1, pl2, tol=1e-3):
    """
    Check if two plates are within tolerance of eachother.

    This uses the OCC shape representation of the plate.

    :param pl1:
    :param pl2:
    :param tol:
    :return:
    """
    dss = compute_minimal_distance_between_shapes(pl1.solid, pl2.solid)
    if dss.Value() <= tol:
        return dss
    else:
        return None


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
