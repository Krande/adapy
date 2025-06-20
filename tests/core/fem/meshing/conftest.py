import pytest

import ada


@pytest.fixture
def pl1():
    return ada.Plate(
        "MyPl",
        [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)],
        20e-3,
        origin=(0, 0, 0),
        xdir=(1, 0, 0),
        normal=(0, 0, 1),
    )


@pytest.fixture
def pl2():
    return ada.Plate(
        "MyPl2",
        [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)],
        20e-3,
        origin=(1, 0, -0.1),
        xdir=(0, 0, 1),
        normal=(-1, 0, 0),
    )
