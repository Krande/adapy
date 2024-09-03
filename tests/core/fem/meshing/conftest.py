import pytest

import ada


@pytest.fixture
def pl1():
    place1 = dict(origin=(0, 0, 0), xdir=(1, 0, 0), n=(0, 0, 1))
    return ada.Plate("MyPl", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **place1)


@pytest.fixture
def pl2():
    place2 = dict(origin=(1, 0, -0.1), xdir=(0, 0, 1), n=(-1, 0, 0))
    return ada.Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **place2)
