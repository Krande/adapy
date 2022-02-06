import pytest

from ada.concepts.containers import Nodes
from ada.concepts.exceptions import DuplicateNodes


def test_empty():
    n = Nodes([])
    assert len(n) == 0


def test_from_sequence(nodes):
    n = Nodes(nodes[:3])

    assert len(n) == 3


def test_with_duplicates(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    with pytest.raises(DuplicateNodes):
        Nodes([n1, n2, n1])


def test_from_iterables(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes

    def geniter():
        yield n1
        yield n2
        yield n3

    g = geniter()
    n = Nodes(g)

    assert len(n) == 3
