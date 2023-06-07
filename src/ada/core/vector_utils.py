from __future__ import annotations

import numpy as np

from ada.config import Settings
from .exceptions import VectorNormalizeError


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


def vector_length(vector: np.ndarray) -> float:
    """This method takes in a np.array vector and returns the length of the vector."""
    if vector.shape[0] != 3:
        if vector.shape[0] == 2:
            raise ValueError("Vector is not a 3d vector, but a 2d vector. Please consider vector_length_2d() instead")
        raise ValueError(f"Vector is not a 3d vector. Vector array length: {len(vector)}")

    return float(np.linalg.norm(vector))


def vector_length_2d(vector: np.ndarray) -> float:
    """This method takes in a np.array vector and returns the length of the vector."""
    if vector.shape[0] != 2:
        if vector.shape[0] == 3:
            raise ValueError("Vector is not a 2d vector, but a 3d vector. Please consider vector_length() instead")
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
