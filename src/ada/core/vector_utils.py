from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Iterable, List

import numpy as np

from ada.config import Settings

from .exceptions import VectorNormalizeError


class Plane(Enum):
    XY = "xy"
    XZ = "xz"
    YZ = "yz"


@dataclass
class EquationOfPlane:
    point_in_plane: tuple | list | np.ndarray
    normal: tuple | list | np.ndarray
    yvec: tuple | list | np.ndarray = None

    PLANE: ClassVar[Plane]

    @staticmethod
    def from_arbitrary_points(points):
        points = np.array(points)
        normal = normal_to_points_in_plane(points)
        pip = points[0]
        return EquationOfPlane(pip, normal)

    def __post_init__(self):
        point_in_plane = self.point_in_plane
        normal = self.normal
        x1, y1, z1 = point_in_plane
        a = normal[0]
        b = normal[1]
        c = normal[2]
        self.d = -(a * x1 + b * y1 + c * z1)

    def return_points_in_plane(self, points: np.ndarray) -> np.ndarray:
        return points[points.dot(self.normal) + self.d == 0]

    def is_point_in_plane(self, point: Iterable) -> bool:
        if isinstance(point, np.ndarray) is False:
            point = np.array(point)

        return bool(point.dot(self.normal) + self.d == 0)

    def get_lcsys(self):
        if self.yvec is None:
            if sum(abs(self.normal) - np.array([0, 0, 1])) < 1e-5:
                vec1 = np.array([1, 0, 0])
            else:
                vec1 = np.array([0, 0, 1])

            self.yvec = unit_vector(calc_yvec(vec1, self.normal))

        xvec = unit_vector(calc_xvec(self.yvec, self.normal))

        return [xvec, self.yvec, self.normal]

    def get_points_in_lcsys_plane(self, p_dist: float = 1, plane: Plane = Plane.XY):
        csys = self.get_lcsys()
        p0 = self.point_in_plane
        vec_map = {
            Plane.XY: (0, 1),
            Plane.XZ: (0, 2),
            Plane.YZ: (1, 2),
        }
        i, j = vec_map.get(plane)
        vec2 = csys[i]
        vec3 = csys[j]

        p1 = p0 + vec2 * p_dist + vec3 * p_dist
        p2 = p0 - vec2 * p_dist + vec3 * p_dist
        p3 = p0 - vec2 * p_dist - vec3 * p_dist
        p4 = p0 + vec2 * p_dist - vec3 * p_dist
        return [p1, p2, p3, p4]

    def project_point_onto_plane(self, point: Iterable) -> np.ndarray:
        p = np.array(point)
        dist = p.dot(self.normal) + self.d
        return p - dist * self.normal


def linear_2dtransform_rotate(origin, point, degrees) -> np.ndarray:
    """
    Rotate a 2d point given an origin and a degree.

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
        https://kieranwynn.github.io/pyquaternion/

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


def sort_points_by_dist(p, points):
    return sorted(points, key=lambda x: vector_length(x - p))


def is_in_interval(value: float, interval_start: float, interval_end: float, incl_interval_ends: bool = False) -> bool:
    if incl_interval_ends:
        return interval_start <= value <= interval_end
    else:
        return interval_start < value < interval_end


def is_between_endpoints(p: np.ndarray, start: np.ndarray, end: np.ndarray, incl_endpoints: bool = False) -> bool:
    """Returns if point p is on the line between the points start and end"""
    if is_null_vector(p, start) or is_null_vector(p, end):
        if incl_endpoints:
            return True
        return False

    ab = end - start
    ap = p - start

    vec_fraction = get_vec_fraction(ap, ab)
    on_line_segment = is_in_interval(vec_fraction, 0.0, 1.0, incl_interval_ends=incl_endpoints)
    return is_parallel(ab, ap) and on_line_segment


def get_vec_fraction(vec: np.ndarray, reference_vec: np.ndarray) -> float:
    """Returns the fraction of the projection of vec onto reference_vec."""
    return np.dot(vec, reference_vec) / np.dot(reference_vec, reference_vec)


def point_on_line(start: np.ndarray, end: np.ndarray, point: np.ndarray) -> np.ndarray:
    """

    :param start: Start of line
    :param end: End of line
    :param point: Point
    :return:
    """
    ap = point - start
    ab = end - start
    result = start + get_vec_fraction(ap, ab) * ab
    return result


def is_null_vector(ab: np.array, cd: np.array, decimals=Settings.precision) -> bool:
    """Check if difference in vectors AB and CD is null vector"""
    return np.array_equal((cd - ab).round(decimals), np.zeros_like(ab))


def is_parallel(ab: np.array, cd: np.array, tol=Settings.point_tol) -> bool:
    """Check if vectors AB and CD are parallel"""
    return float(np.abs(np.sin(angle_between(ab, cd)))) < tol


def is_perpendicular(ab: np.array, cd: np.array, tol=Settings.point_tol) -> bool:
    """Returns if the vectors are perpendicular"""
    return float(np.abs(np.dot(ab, cd))) < tol


def is_angled(vector_1: np.ndarray, vector_2: np.ndarray) -> bool:
    """Returns true if 2 vectors is not perpendicular nor parallel to each other"""
    return not (is_perpendicular(vector_1, vector_2) or is_parallel(vector_1, vector_2))


def intersect_calc(a: np.ndarray, c: np.ndarray, ab: np.ndarray, cd: np.ndarray):
    """Function for evaluating an intersection point between two vector-lines (AB & CD).  The function returns
    variables s & t denoting the scalar value multiplied with the two vector equations A + s*AB = C + t*CD."""
    # Setting up the equation for use in linalg.lstsq
    matrix = np.array((ab, -cd)).T
    vec = c - a

    st = np.linalg.lstsq(matrix, vec, rcond=None)

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
    is2d = len(list(v1[0])) == 2

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


def is_point_inside_bbox(p, bbox, tol=1e-3) -> bool:
    """

    :param p: Point
    :param bbox: Bounding box
    :param tol: Tolerance
    :return:
    """
    if (
        bbox[0][0][0] - tol < p[0] < bbox[0][1][0] + tol
        and bbox[1][0][1] - tol < p[1] < bbox[1][1][1] + tol
        and bbox[2][0][2] - tol < p[2] < bbox[2][1][2] + tol
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


def is_coplanar(x1, y1, z1, x2, y2, z2, x3, y3, z3, x4, y4, z4) -> bool:
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


def poly_area_from_list(coords):
    return poly_area(*zip(*coords))


def poly2d_center_of_gravity(polygon):
    """Calculates the center of gravity of a polygon described by a numpy array containing x,y,z coordinates."""
    sum_area = 0
    sum_cx = 0
    sum_cy = 0

    for i in range(len(polygon)):
        j = (i + 1) % len(polygon)
        area = polygon[i][0] * polygon[j][1] - polygon[j][0] * polygon[i][1]
        sum_area += area
        sum_cx += (polygon[i][0] + polygon[j][0]) * area
        sum_cy += (polygon[i][1] + polygon[j][1]) * area

    if sum_area == 0:
        return None

    cx = sum_cx / (3 * sum_area)
    cy = sum_cy / (3 * sum_area)

    return np.array([cx, cy])


def global_2_local_nodes(csys, origin, nodes, use_quaternion=True):
    """

    :param csys: List of tuples containing; [LocalX, LocalY]
    :param origin: Point of origin. Node object
    :param nodes: list of nodes
    :return: List of local node coordinates
    """
    from ada import Node

    global_csys = [(1, 0, 0), (0, 1, 0)]
    rmat = rotation_matrix_csys_rotate(global_csys, csys, use_quaternion=use_quaternion)

    if type(origin) is Node:
        origin = origin.p
    elif type(origin) in (list, tuple):
        origin = np.array(origin)

    if type(nodes[0]) is Node:
        nodes = [no.p for no in nodes]

    res = [np.dot(rmat, p - origin) for p in nodes]
    return res


def local_2_global_points(points, origin, xdir, normal):
    """
    A method for converting a list of points in a 2d coordinate system to global 3d coordinates

    :param normal: Normal to 2d plane
    :param origin: Origin of local coordinate system
    :param points: List of points in 2d coordinate system
    :param xdir: Local X-direction
    :return:
    """
    from ada.concepts.points import Node
    from ada.core.constants import X, Y

    if type(origin) is Node:
        origin = origin.p

    if type(points[0]) is Node:
        points = [no.p for no in points]

    points = [
        np.array(n, dtype=np.float64) if len(n) == 3 else np.array(list(n) + [0], dtype=np.float64) for n in points
    ]
    yvec = calc_yvec(xdir, normal)

    return transform3d([xdir, yvec], [X, Y], origin, points)


def transform3d(csys_1, csys_2, origin, points) -> List[np.ndarray]:
    """Transform points between coordinate systems"""
    rmat = rotation_matrix_csys_rotate(csys_1, csys_2, inverse=True)

    return [np.array(origin, dtype=np.float64) + np.dot(rmat, n) for n in points]


def normal_to_points_in_plane(points_) -> np.ndarray:
    """Get normal to the plane created by a list of points"""
    if len(points_) <= 2:
        raise ValueError("Insufficient number of points")

    # remove duplicate points
    set_data = set(tuple(x) for x in points_)
    if len(set_data) != len(points_):
        points = [np.array(y) for y in set_data]
    else:
        points = points_

    # take 3 arbitrary points and create a normal
    p1 = points[0]
    p2 = points[1]
    p3 = points[2]

    # These two vectors are in the plane
    v1 = p3 - p1
    v2 = p2 - p1

    if is_parallel(v1, v2) is True:
        for i in range(3, len(points)):
            p3 = points[i]
            v1 = p3 - p1
            if is_parallel(v1, v2) is False:
                break

    # the cross product is a vector normal to the plane
    n = np.array([x if abs(x) != 0.0 else 0.0 for x in list(np.cross(v1, v2))])
    if n.any() == 0.0:
        raise ValueError("Error in calculating plate normal")

    return np.array([x if abs(x) != 0.0 else 0.0 for x in list(unit_vector(n))])


def unit_vector(vector: np.ndarray) -> np.ndarray:
    """Returns the unit vector of a given vector"""
    norm = vector / np.linalg.norm(vector)
    if np.isnan(norm).any():
        raise VectorNormalizeError(f'Error trying to normalize vector "{vector}"')

    return norm


def is_clockwise(points) -> bool:
    """Return true if order of 2d points are sorted in a clockwise order"""
    psum = 0
    for p1, p2 in zip(points[:-1], points[1:]):
        psum += (p2[0] - p1[0]) * (p2[1] + p1[1])
    psum += (points[-1][0] - points[0][0]) * (points[-1][1] + points[0][1])
    return not float(psum) < 0


def calc_xvec(y_vec, z_vec):
    return np.cross(y_vec, z_vec)


def calc_yvec(x_vec, z_vec=None) -> np.ndarray:
    if z_vec is None:
        calc_zvec(x_vec)

    return np.cross(z_vec, x_vec)


def calc_zvec(x_vec, y_vec=None) -> np.ndarray:
    """Calculate Z-vector (up) from an x-vector (along beam) only."""
    from ada.core.constants import Y, Z

    if y_vec is None:
        z_vec = np.array(Z)
        a = angle_between(x_vec, z_vec)
        if a == np.pi or a == 0:
            z_vec = np.array(Y)
        return z_vec
    else:
        return np.cross(x_vec, y_vec)


def is_on_line(data):
    """Evaluate intersection point between two lines"""
    l, bm = data
    A, B = np.array(l[0]), np.array(l[1])
    AB = B - A
    C = bm.n1.p
    D = bm.n2.p
    CD = D - C

    if (vector_length(A - C) < 1e-5) is True and (vector_length(B - D) < 1e-5) is True:
        return None

    s, t = intersect_calc(A, C, AB, CD)
    AB_ = A + s * AB
    CD_ = C + t * CD
    if (vector_length(AB_ - CD_) < 1e-4) is True and s not in (0.0, 1.0):
        return list(AB_), bm
    else:
        return None


def projection_onto_line(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """

    :param point: Point outside line
    :param start: Start node of line
    :param end: End node of line
    :return: Projection from n1 to p0 onto line. Returns projected line segment
    """

    v = end - start
    p = point - start
    angle = angle_between(v, p)
    t0 = np.linalg.norm(p) * np.cos(angle) * unit_vector(v)
    q = t0 - p
    return point + q
