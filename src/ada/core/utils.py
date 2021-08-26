# coding=utf-8
import datetime
import logging
import os
import pathlib
import shutil
import zipfile
from decimal import ROUND_HALF_EVEN, Decimal

import numpy as np

from ..config import Settings


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
    from ..occ.utils import compute_minimal_distance_between_shapes

    dss = compute_minimal_distance_between_shapes(pl1.solid, pl2.solid)
    if dss.Value() <= tol:
        return dss
    else:
        return None


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


def sort_points_by_dist(p, points):
    return sorted(points, key=lambda x: vector_length(x - p))


def is_point_on_line(a, b, p):
    ap = p - a
    ab = b - a
    result = a + np.dot(ap, ab) / np.dot(ab, ab) * ab
    return result


def is_parallel(ab, cd, tol=0.0001):
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
    from ada.concepts.points import Node
    from ada.core.constants import X, Y

    if type(origin) is Node:
        origin = origin.p

    if type(nodes[0]) is Node:
        nodes = [no.p for no in nodes]

    nodes = [np.array(n, dtype=np.float64) if len(n) == 3 else np.array(list(n) + [0], dtype=np.float64) for n in nodes]
    yvec = calc_yvec(xdir, normal)

    rmat = rotation_matrix_csys_rotate([xdir, yvec], [X, Y], inverse=True)

    return [np.array(origin, dtype=np.float64) + np.dot(rmat, n) for n in nodes]


def normal_to_points_in_plane(points):
    """

    :param points: List of Node objects
    :return:
    """
    if len(points) <= 2:
        raise ValueError("Insufficient number of points")
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
        self.i = start - 1
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
    """Convert the size from bytes to other units like KB, MB or GB"""
    if unit == SIZE_UNIT.KB:
        return size_in_bytes / 1024
    elif unit == SIZE_UNIT.MB:
        return size_in_bytes / (1024 ** 2)
    elif unit == SIZE_UNIT.GB:
        return size_in_bytes / (1024 ** 3)
    else:
        return size_in_bytes


def get_file_size(file_name, size_type=SIZE_UNIT.MB):
    """Get file in size in given unit like KB, MB or GB"""
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


def get_current_user():
    """

    :return: Name of current user
    """
    import getpass

    return getpass.getuser()


def get_list_of_files(dir_path, file_ext=None, strict=False):
    """
    Get a list of file and sub directories for a given directory

    :param dir_path: Parent directory in which the recursive search for files will take place
    :param file_ext: File extension
    :param strict: If True the function raiser errors when no files are found.
    :return: list of all found files
    """
    all_files = []
    list_of_file = os.listdir(dir_path)

    # Iterate over all the entries
    for entry in list_of_file:
        # Create full path
        full_path = os.path.join(dir_path, entry)
        # If entry is a directory then get the list of files in this directory
        if os.path.isdir(full_path):
            all_files = all_files + get_list_of_files(full_path, file_ext, strict)
        else:
            all_files.append(full_path)

    if file_ext is not None:
        all_files = [f for f in all_files if f.endswith(file_ext)]

    if len(all_files) == 0:
        msg = f'Files with "{file_ext}"-extension is not found in "{dir_path}" or any sub-folder.'
        if strict:
            raise FileNotFoundError(msg)
        else:
            logging.info(msg)

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


def datetime_to_str(obj):
    return obj.isoformat()


def get_last_file_modified(file_dir, file_ext):
    last_date = None
    for f in get_list_of_files(file_dir, file_ext):
        curr_date = get_file_time_local(f)
        if last_date is None:
            last_date = curr_date
        elif curr_date > last_date:
            last_date = curr_date
    return last_date


def datetime_from_str(obj_str):
    import dateutil.parser

    return dateutil.parser.parse(obj_str)


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


def make_name_fem_ready(value, no_dot=False):
    """
    Based on typically allowed names in FEM, this function will try to rename objects to comply without significant
    changes to the original name

    :param value:
    :param no_dot:
    :return: Fixed name
    """
    logging.debug("Converting bad name")

    if value[0] == "/":
        value = value[1:]

    value = value.replace("/", "_").replace("=", "")
    if str.isnumeric(value[0]):
        value = "_" + value

    if "/" in value:
        logging.error(f'Character "/" found in {value}')

    # if "-" in value:
    #     value = value.replace("-", "_")

    if no_dot:
        value = value.replace(".", "_")

    return value.strip()


def get_version():
    from importlib.metadata import version

    return version("ada-py")


def closest_val_in_dict(val, dct):
    """
    When mapping using a dictionary and value do not match with the keys in the dictionary.
    :param val: Value a number, usually float
    :param dct: Dictionary with number keys (int o float)
    :return: Dictionary-value corresponding to the keys nearest the input value
    """
    table_looksups = np.array(list(dct))
    dct_index = table_looksups[np.abs(table_looksups - val).argmin()]
    return dct[dct_index]


def flatten(t):
    return [item for sublist in t for item in sublist]


def calc_yvec(x_vec, z_vec=None):
    """

    :param x_vec:
    :param z_vec:
    :return:
    """

    if z_vec is None:
        calc_zvec(x_vec)

    return np.cross(z_vec, x_vec)


def calc_zvec(x_vec, y_vec=None):
    """
    Calculate Z-vector (up) from an x-vector (along beam) only.

    :param x_vec:
    :param y_vec:
    :return:
    """
    from ada.core.constants import Y, Z

    if y_vec is None:
        z_vec = np.array(Z)
        a = angle_between(x_vec, z_vec)
        if a == np.pi or a == 0:
            z_vec = np.array(Y)
        return z_vec
    else:
        np.cross(x_vec, y_vec)


def faceted_tol(units):
    """

    :param units:
    :return:
    """
    if units == "m":
        return 1e-2
    else:
        return 1


def replace_node(old_node, new_node):
    """

    :param old_node:
    :param new_node:
    :type old_node: ada.Node
    :type new_node: ada.Node
    """
    for elem in old_node.refs.copy():
        node_index = elem.nodes.index(old_node)

        elem.nodes.pop(node_index)
        elem.nodes.insert(node_index, new_node)
        elem.update()
        # new_node.refs.extend(old_node.refs)
        old_node.refs.pop(old_node.refs.index(elem))
        new_node.refs.append(elem)
        logging.debug(f"{old_node} exchanged with {new_node} --> {elem}")


def replace_nodes_by_tol(nodes, decimals=0, tol=Settings.point_tol):
    """

    :param nodes:
    :param decimals:
    :param tol:
    :type nodes: ada.core.containers.Nodes
    """

    def rounding(vec, decimals_):
        return np.around(vec, decimals=decimals_)

    def n_is_most_precise(n, nearby_nodes_, decimals_=0):
        most_precise = [np.array_equal(n.p, rounding(n.p, decimals_)) for n in [node] + nearby_nodes_]

        if most_precise[0] and not np.all(most_precise[1:]):
            return True
        elif not most_precise[0] and np.any(most_precise[1:]):
            return False
        elif decimals_ == 10:
            logging.error(f"Recursion started at 0 decimals, but are now at {decimals_} decimals. Will proceed with n.")
            return True
        else:
            return n_is_most_precise(n, nearby_nodes_, decimals_ + 1)

    for node in nodes:
        nearby_nodes = list(filter(lambda x: x != node, nodes.get_by_volume(node.p, tol=tol)))
        if nearby_nodes and n_is_most_precise(node, nearby_nodes, decimals):
            for nearby_node in nearby_nodes:
                replace_node(nearby_node, node)
