import pytest

from ada import Placement


@pytest.fixture
def place1():
    return dict(placement=Placement(origin=(0, 0, 0), xdir=(1, 0, 0), zdir=(0, 0, 1)))


@pytest.fixture
def place2():
    return dict(placement=Placement(origin=(0, 0, 0), xdir=(1, 0, 0), zdir=(0, -1, 0)))
