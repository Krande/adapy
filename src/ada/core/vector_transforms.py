from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
import pyquaternion as pq
from pyquaternion import Quaternion

from ada.core.vector_utils import (
    angle_between,
    calc_yvec,
    calc_zvec,
    is_parallel,
    unit_vector,
)
from ada.geom.direction import Direction
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada.api.transforms import Placement


def transform_3d_points_to_2d(
    points3d: np.ndarray, origin: Point, xdir: Direction, normal: Direction, inverse=False
) -> tuple[Placement, np.ndarray]:
    from ada.api.transforms import Placement

    yv = calc_yvec(xdir, normal)

    rotation_matrix = transform_csys_to_csys(np.array([1, 0, 0]), np.array([0, 1, 0]), xdir, yv, inverse=inverse)

    # use that rotation matrix to transform the points from 3d (x,y,z) to a 2d coordinate system (x', y')
    points2d = np.matmul(rotation_matrix, points3d.T).T

    # trim the column related to the normal vector (z)
    points2d_trimmed = points2d[:, :-1]

    return Placement(origin, xdir, yv, normal), points2d_trimmed


def transform_points3d_to_2d(
    points3d: list[Point] | np.ndarray, origin=None, xdir=None, inverse=False
) -> tuple[Placement, list[Point]]:
    """Transforms a list of co-linear 3D Cartesian points (x,y,z) to a 2D coordinate system (x',y')."""

    if len(points3d) < 3:
        raise ValueError("At least 3 points are required.")

    if not isinstance(points3d, np.ndarray):
        points3d = np.array(points3d)

    origin = points3d[0] if origin is None else origin
    points3d = points3d.copy() - origin
    n = normal_to_points_in_plane(points3d)
    if xdir is None:
        xdir = Direction(points3d[1] - points3d[0]).get_normalized()

    place, points2d = transform_3d_points_to_2d(points3d, origin, xdir, n, inverse=inverse)
    local2d_points = [Point(*p) for p in points2d]

    return place, local2d_points


def transform_3points_to_2d(points) -> tuple[Placement, list[Point]]:
    from numpy.linalg import svd

    from ada.api.transforms import Placement

    # Ensure that we have exactly 3 points
    assert len(points) == 3, "Exactly 3 points are required."

    # Calculate the centroid of the points
    origin = points[0]

    # Move points to origin
    centered_points = [Point(p.x - origin.x, p.y - origin.y, p.z - origin.z) for p in points]

    # Create a matrix from moved points
    mat = np.array([[p.x, p.y, p.z] for p in centered_points])

    # Apply singular value decomposition
    _, _, Vt = svd(mat)

    # The normal of the plane is the last row of Vt
    Vt[-1, :]

    # Create transformation matrix (we keep only the first 2 vectors which form the new plane)
    tra_matrix = Vt[:-1, :]

    # Apply transformation: dot product between each point and the transformation matrix
    points2d = np.dot(tra_matrix, mat.T).T

    return Placement(origin=origin, xdir=tra_matrix[0], ydir=tra_matrix[1]), [Point(p[0], p[1]) for p in points2d]


def transform_2d_to_3d(points_2d, transformation_matrix):
    """Converts 2D points (x', y') to 3D points (x', y', 0) and applies a transformation matrix to them."""
    # Convert 2D points (x', y') to 3D points (x', y', 0) and add a homogeneous coordinate of 1
    points_2d_homogeneous = np.hstack((points_2d, np.zeros((points_2d.shape[0], 1)), np.ones((points_2d.shape[0], 1))))

    # Apply the transformation matrix to each point
    points_3d_homogeneous = np.dot(transformation_matrix, points_2d_homogeneous.T).T

    # Remove the fourth coordinate (the homogeneous coordinate)
    points_3d = points_3d_homogeneous[:, :3]

    return points_3d


def global_2_local_nodes(csys, origin, nodes):
    """

    :param csys: List of tuples containing; [LocalX, LocalY]
    :param origin: Point of origin. Node object
    :param nodes: list of nodes
    :return: List of local node coordinates
    """
    from ada import Node

    global_csys = [(1, 0, 0), (0, 1, 0)]
    rot_mat = rotation_matrix_csys_rotate(global_csys, csys)

    if type(origin) is Node:
        origin = origin.p
    elif type(origin) in (list, tuple):
        origin = np.array(origin)

    if type(nodes[0]) is Node:
        nodes = [no.p for no in nodes]

    points2d = [np.dot(rot_mat, p - origin) for p in nodes]
    return points2d


def local_2_global_points(points, origin, xdir, normal) -> list[Point]:
    """
    A method for converting a list of points in a 2d coordinate system to global 3d coordinates

    :param normal: Normal to 2d plane
    :param origin: Origin of local coordinate system
    :param points: List of points in 2d coordinate system
    :param xdir: Local X-direction
    :return:
    """
    from ada.api.nodes import Node
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


def transform_4x4(matrix4x4: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """Transforms an array of points by a transformation matrix."""
    # In case of 2d points, add a z-coordinate of 0
    if pos.shape[1] == 2:
        pos = np.hstack((pos, np.zeros((pos.shape[0], 1))))

    pos = np.hstack((pos, np.ones((pos.shape[0], 1))))
    transformed = pos @ matrix4x4.T
    transformed /= transformed[:, 3].reshape(-1, 1)

    return transformed[:, :3]


def transform_3x3(matrix3x3: np.ndarray, pos: np.ndarray, inverse=False) -> np.ndarray:
    """Transforms 2d or 3d cartesian points by a transformation matrix."""

    # In case of 2d points, add a z-coordinate of 0
    if len(pos.shape) == 1 or pos.shape[1] == 2:
        pos = np.hstack((pos, np.zeros((pos.shape[0], 1))))

    if inverse:
        transformed = np.dot(matrix3x3.T, pos.T).T
    else:
        transformed = np.dot(pos, matrix3x3.T)

    return transformed


def transform3d(csys_1, csys_2, origin, points) -> list[Point]:
    """Transform points between coordinate systems"""
    rmat = rotation_matrix_csys_rotate(csys_1, csys_2, inverse=True)

    return [Point(*origin) + np.dot(rmat, n) for n in points]


def normal_to_points_in_plane(points_) -> Direction:
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

    # the cross product is a vector normal to the plane. ``np.cross`` carries
    # heavy per-call overhead (moveaxis / normalize_axis_tuple) that dominates
    # for these length-3 vectors, so author the cross product by hand — this is
    # the single largest cost in the Genie/SAT plate-read path.
    normal = (
        v1[1] * v2[2] - v1[2] * v2[1],
        v1[2] * v2[0] - v1[0] * v2[2],
        v1[0] * v2[1] - v1[1] * v2[0],
    )
    return Direction(normal).get_normalized()


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


def rotation_matrix_csys_rotate(csys1_in, csys2_in, inverse=False):
    """
    Create a rotation matrix by providing two sets of coordinate systems defined by 2 vectors each (X- and Y-vectors)

    Resources:

        https://en.wikipedia.org/wiki/Rotation_matrix
        https://kieranwynn.github.io/pyquaternion/

    :param csys1_in: Coordinate system 1 defined by 2 vectors [LocalX, LocalY]
    :param csys2_in: Coordinate system 1 defined by 2 vectors [LocalX, LocalY]
    :param inverse: True if you want to reverse the applied rotations from csys1 to csys2
    :return: A rotation matrix ready to be employed for further use
    """

    # Ensure Consistent right-hand-rule
    csys1 = np.array([csys1_in[0], csys1_in[1], np.cross(csys1_in[0], csys1_in[1])]).astype(float)
    csys2 = np.array([csys2_in[0], csys2_in[1], np.cross(csys2_in[0], csys2_in[1])]).astype(float)

    q1 = pq.Quaternion(matrix=csys1)
    q2 = pq.Quaternion(matrix=csys2)
    q3 = q1 * q2

    if inverse:
        q3 = q3.inverse

    rm = q3.rotation_matrix
    return rm


def transform_csys_to_csys(x_vector1, y_vector1, x_vector2, y_vector2, inverse=False) -> np.ndarray:
    # Original coordinate system
    z_vector1 = np.cross(x_vector1, y_vector1)

    # Final coordinate system
    z_vector2 = np.cross(x_vector2, y_vector2)

    # Normalize vectors
    x_vector1 = x_vector1 / np.linalg.norm(x_vector1)
    y_vector1 = y_vector1 / np.linalg.norm(y_vector1)
    z_vector1 = z_vector1 / np.linalg.norm(z_vector1)

    x_vector2 = x_vector2 / np.linalg.norm(x_vector2)
    y_vector2 = y_vector2 / np.linalg.norm(y_vector2)
    z_vector2 = z_vector2 / np.linalg.norm(z_vector2)

    # Create matrices
    original_matrix = np.array([x_vector1, y_vector1, z_vector1]).T
    new_matrix = np.array([x_vector2, y_vector2, z_vector2]).T

    # Calculate rotation matrix
    rotation_matrix = np.dot(new_matrix, np.linalg.inv(original_matrix))

    if inverse:
        return np.linalg.inv(rotation_matrix)

    return rotation_matrix


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


# cache on (xvec, angle, up) → (up, yvec, angle)
@lru_cache(maxsize=128)
def compute_orientation(
    xvec_tup: tuple[float, float, float], angle: float, up_tup: tuple[float, float, float] | None
) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    from ada.core.utils import round_array

    xvec = np.array(xvec_tup)
    zvec = calc_zvec(xvec)
    if up_tup is None:
        # default up is z-vec rotated by angle (or just zvec if angle==0)
        if angle:
            rot = Quaternion(axis=xvec, degrees=angle).rotation_matrix
            up_arr = zvec @ rot.T
        else:
            up_arr = zvec
    else:
        up_arr = np.array(up_tup)
        # recalc angle if custom up
        rad = angle_between(up_arr, zvec)
        angle = float(np.rad2deg(rad))
    # round & zero small bits

    up_arr = round_array(up_arr)
    # y is always based on final up
    y_arr = calc_yvec(xvec, up_arr)
    return tuple(up_arr), tuple(y_arr), angle


# cache on (xvec, yvec, zvec)
@lru_cache(maxsize=128)
def compute_orientation_vec(
    xvec_tup: tuple[float, float, float] | None,
    yvec_tup: tuple[float, float, float] | None,
    up_tup: tuple[float, float, float] | None,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    from ada.core.utils import round_array
    from ada.core.vector_utils import calc_xvec, calc_yvec, calc_zvec

    # Default vectors if all are None
    if xvec_tup is None and yvec_tup is None and up_tup is None:
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)

    # Convert to numpy arrays for calculations
    xvec = Direction(xvec_tup) if xvec_tup is not None else None
    yvec = Direction(yvec_tup) if yvec_tup is not None else None
    zvec = Direction(up_tup) if up_tup is not None else None

    # Calculate missing vectors
    if xvec is not None and yvec is not None and zvec is None:
        zvec = Direction(calc_zvec(xvec, yvec))
    elif xvec is not None and zvec is not None and yvec is None:
        yvec = Direction(calc_yvec(xvec, zvec))
    elif yvec is not None and zvec is not None and xvec is None:
        xvec = Direction(calc_xvec(yvec, zvec))
    elif xvec is not None and yvec is None and zvec is None:
        # If only xvec is provided, use default z-axis and calculate y
        zvec = Direction([0.0, 0.0, 1.0])
        yvec = calc_yvec(xvec, zvec)
    elif xvec is None and yvec is not None and zvec is None:
        # If only yvec is provided, use default z-axis and calculate x
        zvec = Direction([0.0, 0.0, 1.0])
        xvec = Direction(calc_xvec(yvec, zvec))
    elif xvec is None and yvec is None and zvec is not None:
        # If only zvec is provided, use default x-axis and calculate y
        # If zvec is (1,0,0) use default xvec to 0,0,1
        if zvec.is_parallel(Direction([1.0, 0.0, 0.0])):
            xvec = Direction([0.0, 0.0, 1.0])
        else:
            xvec = Direction([1.0, 0.0, 0.0])
        yvec = Direction(calc_yvec(xvec, zvec))

    # A reference up-vector parallel to the beam axis (e.g. a vertical column whose default
    # or supplied Z-up coincides with its axis) collapses the in-plane axis to a zero
    # vector. Whenever the beam axis is known and the derived yvec degenerated, rebuild the
    # frame from a non-parallel global reference so normalization doesn't blow up.
    if xvec is not None and (yvec is None or float(np.linalg.norm(np.asarray(yvec, dtype=float))) < 1e-9):
        xref = Direction(xvec)
        zref = Direction([0.0, 0.0, 1.0])
        if xref.is_parallel(zref):
            zref = Direction([0.0, 1.0, 0.0])
        yvec = Direction(calc_yvec(xref, zref))
        zvec = Direction(calc_zvec(xref, yvec))

    # Normalize vectors
    xvec = xvec.get_normalized()
    yvec = yvec.get_normalized()
    zvec = zvec.get_normalized()

    # Round to avoid floating point issues
    xvec = round_array(xvec)
    yvec = round_array(yvec)
    zvec = round_array(zvec)

    # Convert back to tuples for caching
    return tuple(xvec), tuple(yvec), tuple(zvec)


def _round_rows_unique(a: np.ndarray) -> np.ndarray:
    """round_array over the rows of ``a`` (m,3), Decimal-rounding each UNIQUE row once.

    FEM meshes repeat orientations heavily (co-planar plate patches share their
    frame), so deduplicating before the per-component ``roundoff`` keeps the exact
    scalar semantics at a fraction of the Decimal cost."""
    from ada.core.utils import roundoff

    uniq, inv = np.unique(a, axis=0, return_inverse=True)
    out = np.empty_like(uniq)
    for i in range(uniq.shape[0]):
        row = uniq[i]
        out[i] = [roundoff(x) if x != 0 else x for x in row]
    return out[inv.reshape(-1)]


def shell_orientations_bulk(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized orientation math for ``m`` flat FEM shell k-gons (m, k, 3).

    Replicates, array-wise and in the same floating-point operation order, the
    scalar chain CurvePoly2d.from_fem_shell runs per element:
    ``normal_to_points_in_plane`` -> ``calc_yvec`` ->
    ``compute_orientation_vec(x, y, z)`` -> ``compute_orientation_vec(x, None, z)``
    (each vector normalized once on construction and once inside
    compute_orientation_vec, then Decimal-rounded via round_array — see
    :func:`_round_rows_unique`).

    Returns ``(ok, rot1, rotf)``: ``ok (m,)`` False for rows the scalar path must
    handle (duplicate points, (near-)parallel edge vectors, zero-length axes —
    the scalar code has dedupe/rescan branches there), ``rot1``/``rotf`` (m,3,3)
    with rows (xdir, ydir, zdir) — the projection and final frames.
    """
    pts = np.asarray(pts, dtype=float)
    m, k, _ = pts.shape

    def _cross(a, b):
        # component order identical to normal_to_points_in_plane / fast_cross
        out = np.empty_like(a)
        out[:, 0] = a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1]
        out[:, 1] = a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2]
        out[:, 2] = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        return out

    def _norm(a):
        # np.linalg.norm(v) for a 1-D length-3 vector == sqrt(v.v), summed in
        # component order
        return np.sqrt(a[:, 0] * a[:, 0] + a[:, 1] * a[:, 1] + a[:, 2] * a[:, 2])

    # normal_to_points_in_plane: v1 = p3 - p1, v2 = p2 - p1, normal = v1 x v2
    v1 = pts[:, 2] - pts[:, 0]
    v2 = pts[:, 1] - pts[:, 0]
    normal = _cross(v1, v2)
    n_len = _norm(normal)
    v1_len = _norm(v1)
    v2_len = _norm(v2)

    # Degenerate-row escape (handled by the scalar path's dedupe / parallel-rescan
    # branches): any duplicated corner, edge vectors (near-)parallel, or a
    # zero-length first edge. The parallel test is a conservative superset of the
    # scalar ``is_parallel`` (sin(angle) < tol): escaping a borderline row to the
    # scalar path only costs time, never correctness.
    dup = np.zeros(m, dtype=bool)
    for i in range(k):
        for j in range(i + 1, k):
            dup |= np.all(pts[:, i] == pts[:, j], axis=1)
    denom = v1_len * v2_len
    # sin(angle) == |v1 x v2| / (|v1||v2|); escape at 2x the scalar is_parallel
    # tolerance so every row the scalar rescan branch would touch goes scalar.
    from ada.config import Config

    par_tol = 2.0 * float(Config().general_point_tol)
    near_parallel = n_len <= par_tol * np.where(denom > 0, denom, 1.0)
    x_raw = pts[:, 1] - pts[:, 0]
    x_len = _norm(x_raw)
    ok = ~(dup | near_parallel | (denom == 0) | (x_len == 0) | (n_len == 0))

    safe = lambda d: np.where(d == 0, 1.0, d)  # noqa: E731 - masked rows are already not-ok
    n_hat = normal / safe(n_len)[:, None]
    x_hat = x_raw / safe(x_len)[:, None]
    y_raw = _cross(n_hat, x_hat)  # calc_yvec(x, z) == fast_cross(z, x)
    y_len = _norm(y_raw)
    # compute_orientation_vec rebuilds the frame from a global reference when a
    # derived yvec degenerates (norm < 1e-9) — those rows must go scalar.
    ok &= y_len >= 1e-9
    y_hat = y_raw / safe(y_len)[:, None]

    # compute_orientation_vec(x_hat, y_hat, n_hat): all three supplied -> each is
    # re-normalized (Direction.get_normalized divides by its own norm again) and
    # Decimal-rounded.
    def _renorm_round(a):
        ln = _norm(a)
        return _round_rows_unique(a / safe(ln)[:, None])

    x1 = _renorm_round(x_hat)
    y1 = _renorm_round(y_hat)
    z1 = _renorm_round(n_hat)

    # compute_orientation_vec(x1, None, z1): y = calc_yvec(x1, z1) = z1 x x1, then
    # all three re-normalized + rounded.
    yf_raw = _cross(z1, x1)
    yf_len = _norm(yf_raw)
    ok &= yf_len >= 1e-9
    xf = _renorm_round(x1)
    yf = _round_rows_unique(yf_raw / safe(yf_len)[:, None])
    zf = _renorm_round(z1)

    rot1 = np.stack([x1, y1, z1], axis=1)
    rotf = np.stack([xf, yf, zf], axis=1)
    return ok, rot1, rotf
