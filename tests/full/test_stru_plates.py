import numpy as np

import ada
from ada.core.vector_utils import vector_length


def test_2dinit(basic_2d_plate, dummy_display):
    dummy_display(basic_2d_plate)


def test_3d_input(dummy_display):
    pl1 = ada.Plate.from_3d_points("MyPl", [(0, 0, 0), (5, 0, 0), (5, 5, 0), (0, 5, 0)], 20e-3)
    origin = np.array([0, 0, 0])

    assert vector_length(pl1.placement.origin - origin) < 1e-8
    assert vector_length(pl1.placement.zdir - np.array([0, 0, 1])) < 1e-8
    assert vector_length(pl1.poly.points3d[0] - origin) < 1e-8

    assert_points = [(0.0, 0.0), (5.0, 0.0), (5.0, -5.0), (0.0, -5.0), (0.0, 0.0)]

    for i, (x, y) in enumerate(pl1.poly.points2d):
        x_, y_ = assert_points[i]
        assert x_ == x and y_ == y

    dummy_display(pl1)
