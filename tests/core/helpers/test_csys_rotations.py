import numpy as np
import pyquaternion as pq

from ada import Placement
from ada.core.utils import roundoff
from ada.core.vector_transforms import (
    global_2_local_nodes,
    local_2_global_points,
    rotation_matrix_csys_rotate,
    transform_4x4,
)
from ada.geom.points import Point


def test_roundtrip_global_coords_2_local():
    # Local Coordinate System
    xvec = (1, 0, 0)
    yvec = (0, 0, 1)
    normal = np.cross(xvec, yvec)
    csys2 = [xvec, yvec]

    origin = (0, 0, 0)
    point = (2, -0.3, 2)

    loc_points = global_2_local_nodes(csys2, origin, [point])
    glob_points = local_2_global_points(loc_points, origin, xvec, normal)
    ev1 = tuple([roundoff(x) for x in glob_points[0]])
    ev2 = tuple([float(x) for x in point])
    assert ev1 == ev2


def test_csys_rotation1():
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


def test_rotate_about_Z():
    p_a = (1, 1, 0)
    # Global axis
    globx = (1, 0, 0)
    globy = (0, 1, 0)
    globz = (0, 0, 1)
    csys_a = np.array([np.array(x).astype(float) for x in [globx, globy, globz]])
    origin = (0, 0, 0)
    normal = (0, 0, 1)

    # Start with a 90 degree counter-clockwise rotation (x = pos y)
    xvec = (0, 1, 0)  # Rotate
    yvec = np.cross(normal, xvec).astype(float)
    csys2 = np.array([np.array(xvec).astype(float), yvec, np.array(normal).astype(float)])
    rp2 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csys_a, csys2), p_a)
    assert tuple([roundoff(x) for x in rp2]) == (1.0, -1.0, 0.0)

    # Rotate another 90 degrees counter-clockwise
    xvec = (-1, 0, 0)
    yvec = np.cross(normal, xvec).astype(float)
    csys3 = np.array([np.array(xvec).astype(float), yvec, np.array(normal).astype(float)])

    rp3 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csys_a, csys3), p_a)
    assert tuple([roundoff(x) for x in rp3]) == (-1.0, -1.0, 0)

    rp4 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csys3, csys_a), rp3)
    assert tuple([roundoff(x) for x in rp4]) == tuple([float(x) for x in p_a])


def test_transform():
    matrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])

    pos = np.array([[1, 2, 3]])
    expected_output = np.array([[1, 2, 3]])

    result = transform_4x4(matrix, pos)
    assert np.allclose(result, expected_output)

    pos_multiple = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
    expected_output_multiple = pos_multiple

    result_multiple = transform_4x4(matrix, pos_multiple)
    assert np.allclose(result_multiple, expected_output_multiple)


def test_transform_placement():
    place = Placement()
    identity_matrix = place.calc_matrix4x4()
    # Assert that the identity matrix is the same as the identity matrix
    assert np.allclose(identity_matrix, np.identity(4))


def test_transform_translation():
    pos = Point(1, 2, 3)
    place = Placement(origin=pos)
    matrix = place.calc_matrix4x4()

    assert np.allclose(matrix, np.array([[1, 0, 0, 1], [0, 1, 0, 2], [0, 0, 1, 3], [0, 0, 0, 1]]))


def test_transform_rotation():
    # Rotate 90 degrees about the z-axis
    m = pq.Quaternion(axis=[0, 0, 1], angle=-np.radians(90)).transformation_matrix

    xdir = m[0][:3]
    ydir = m[1][:3]
    zdir = m[2][:3]
    place = Placement(xdir=xdir, zdir=zdir)

    assert np.allclose(place.xdir, xdir)
    assert np.allclose(place.ydir, ydir)
    assert np.allclose(place.zdir, zdir)
    matrix = place.calc_matrix4x4()

    assert np.allclose(matrix, m)


def test_transform_rotation_and_translation():
    rotation_angle = np.radians(90)
    cos_angle = np.cos(rotation_angle)
    sin_angle = np.sin(rotation_angle)

    matrix = np.array([[cos_angle, -sin_angle, 0, 1], [sin_angle, cos_angle, 0, 1], [0, 0, 1, 1], [0, 0, 0, 1]])

    pos = np.array([[1, 0, 0]])
    expected_output = np.array([[1, 2, 1]])

    result = transform_4x4(matrix, pos)
    assert np.allclose(result, expected_output, atol=1e-6)

    pos_multiple = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    expected_output_multiple = np.array([[1, 2, 1], [0, 1, 1], [1, 1, 2]])

    result_multiple = transform_4x4(matrix, pos_multiple)
    assert np.allclose(result_multiple, expected_output_multiple, atol=1e-6)


def test_transform_2d_3d():
    points2d = [Point(*x) for x in [(0, 5), (0, 0), (5, 0)]]
    place = Placement.from_axis_angle([1, 0, 0], 90, origin=(0, 0, 0))

    points3d_b = place.transform_local_points_to_global(points2d)
    points2d_b = place.transform_global_points_back_to_local(points3d_b)

    for i, p in enumerate(points2d):
        assert p.is_equal(points2d_b[i])


def test_transform_3d_points_to_2d():
    points3d = [Point([1.0, 1.5, 3.0]), Point([2.0, 1.5, 3.0]), Point([2.2, 1.7, 3.2])]

    place = Placement.from_co_linear_points(points3d)

    points2d = place.transform_global_points_to_local(points3d)
    points3d_b = place.transform_local_points_back_to_global(points2d)

    for i, p in enumerate(points3d):
        assert p.is_equal(points3d_b[i])
