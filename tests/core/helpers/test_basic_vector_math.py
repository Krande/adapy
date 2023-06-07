from numpy.testing import assert_array_almost_equal

from ada.core.constants import O
from ada.core.vector_transforms import global_2_local_nodes, local_2_global_points


def test_basic_transform_of_vector():
    new_csys = ([0.0, 0.0, -1.0], [-0.0, 1.0, 0.0], [1.0, 0.0, 0.0])

    n1 = [0, 0, -1]
    res = global_2_local_nodes(new_csys, O, [n1])
    res2 = local_2_global_points(res, O, new_csys[0], new_csys[2])

    assert_array_almost_equal(n1, res2[0])
