import numpy as np
from numpy.testing import assert_array_almost_equal, assert_array_equal

from ada import Placement
from ada.core.constants import O


def test_basic_transform_of_vector():
    place = Placement(O, xdir=[0.0, 0.0, -1.0], ydir=[-0.0, 1.0, 0.0], zdir=[1.0, 0.0, 0.0])
    points3d = [(0, 0, -1)]
    points2d = place.transform_global_points_to_local(points3d)
    points3d_b = place.transform_local_points_back_to_global(points2d)

    assert_array_almost_equal(points3d, points3d_b)


def test_transforms_rotations():
    orientation = Placement()

    assert_array_equal(orientation.rot_matrix, np.eye(3))

    for angle in [0, 10, 20]:
        orientation.rotate([1, 0, 0], angle)
