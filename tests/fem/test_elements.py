import pytest

from ada import Node
from ada.fem import Elem
from ada.fem.containers import FemElements


@pytest.fixture
def elems():
    n1 = Node([1.0, 2.0, 3.0], 1)
    n2 = Node([1.0, 1.0, 1.0], 2)
    n3 = Node([2.0, 1.0, 8.0], 3)
    n4 = Node([1.0, 2.0, 3.0], 4)

    el1 = Elem(1, [n1, n2], "LINE")
    el2 = Elem(2, [n2, n3], "LINE")
    el3 = Elem(3, [n3, n1], "LINE")
    el4 = Elem(4, [n1, n2, n3, n4], "QUAD")
    return el1, el2, el3, el4


def test_empty():
    n = FemElements([])
    assert len(n) == 0


def test_from_sequence(elems):
    n = FemElements(elems[:3])

    assert len(n) == 3


def test_with_duplicates(elems):
    el1, el2, el3, el4 = elems

    with pytest.raises(ValueError):
        FemElements([el1, el2, el1])


def test_from_iterables(elems):
    el1, el2, el3, el4 = elems

    def geniter():
        yield el1
        yield el2
        yield el3

    g = geniter()
    n = FemElements(g)

    assert len(n) == 3
