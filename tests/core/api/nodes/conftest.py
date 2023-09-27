import pytest

from ada import Node
from ada.api.containers import Nodes


@pytest.fixture
def nodes():
    n1 = Node((1.0, 2.0, 3.0), 1)
    n2 = Node((1.0, 1.0, 1.0), 2)
    n3 = Node((2.0, 1.0, 8.0), 3)
    n4 = Node((1.0, 2.0, 3.0), 4)
    n5 = Node((1.0, 3.0, 2.0), 5)
    n6 = Node((1.0, 1.0, 3.0), 6)
    n7 = Node((4.0, 5.0, 1.0), 7)
    n8 = Node((2.0, 4.0, 3.0), 8)
    n9 = Node((1.0, 1.0, 4.0), 9)
    n10 = Node((5.0, 2.0, 3.0), 10)
    return n1, n2, n3, n4, n5, n6, n7, n8, n9, n10


@pytest.fixture
def contained3nodes(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    return Nodes([n1, n2, n3])
