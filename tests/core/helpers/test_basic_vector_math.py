import numpy as np
from numpy.testing import assert_array_almost_equal

from ada.core.constants import O
from ada.core.vector_transforms import global_2_local_nodes, local_2_global_points


def test_basic_transform_of_vector():
    new_csys = ([0.0, 0.0, -1.0], [-0.0, 1.0, 0.0], [1.0, 0.0, 0.0])

    n1 = [0, 0, -1]
    res = global_2_local_nodes(new_csys, O, [n1])
    res2 = local_2_global_points(res, O, new_csys[0], new_csys[2])

    assert_array_almost_equal(n1, res2[0])


def test_basic_transform():
    points3d = np.array([(0, 5, 0), (0, 0, 0), (5, 0, 0)])

    v1 = points3d[1] - points3d[0]
    v2 = points3d[-1] - points3d[0]
    v1 = v1 / np.linalg.norm(v1)
    v2 = v2 / np.linalg.norm(v2)

    n = np.cross(v1, v2)
    n = n / np.linalg.norm(n)
    xdir = v1
    ydir = np.cross(n, xdir)

    m = np.array([xdir, ydir, n])

    points3d = np.dot(m, points3d.T)
    print("sd")
