from ada import Node
from ada.concepts.containers import Nodes


def test_positive_equal(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert Nodes([n1, n2, n3]) == Nodes([n1, n2, n3])


def test_negative_equal(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert Nodes([n1, n2, n3]) != Nodes([n4, n5, n6])


def test_type_mismatch(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert Nodes([n1, n2, n3]) != [n1, n2, n3]


def test_identical(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes([n1, n2, n3])
    assert s == s


def test_random():
    n1 = Node((1.0, 2.0, 3.0), 1)
    n5 = Node((1.0, 3.0, 2.0), 5)

    assert tuple(n1.p) < tuple(n5.p)
