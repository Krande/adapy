import numpy as np
from common import dummy_display

import ada
from ada.core.vector_utils import vector_length


def test_3d_input():
    pl1 = ada.Plate("MyPl", [(0, 0, 0), (5, 0, 0), (5, 5, 0), (0, 5, 0)], 20e-3, use3dnodes=True)
    origin = np.array([0, 0, 0])

    assert vector_length(pl1.placement.origin - origin) < 1e-8
    assert vector_length(pl1.placement.zdir - np.array([0, 0, 1])) < 1e-8
    assert vector_length(pl1.poly.nodes[0].p - origin) < 1e-8

    assert_points = [(0.0, 0.0), (5.0, 0.0), (5.0, -5.0), (0.0, -5.0)]

    for i, (x, y) in enumerate(pl1.poly.points2d):
        x_, y_ = assert_points[i]
        assert x_ == x and y_ == y

    dummy_display(pl1)
