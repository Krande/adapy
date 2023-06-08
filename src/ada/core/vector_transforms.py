from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyquaternion as pq

from ada.core.vector_utils import angle_between, calc_yvec, is_parallel, unit_vector
from ada.geom.placement import Direction
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

    # nodes_alt = np.asarray(nodes) - origin
    # points2d_alt = np.dot(rot_mat, nodes_alt.T).T

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

    # the cross product is a vector normal to the plane
    return Direction(np.cross(v1, v2)).get_normalized()


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
