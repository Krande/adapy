import pytest

from ada import Node
from ada.api.containers import Nodes


@pytest.fixture
def contained_all_nodes(nodes):
    return Nodes(nodes)


def test_index_zero(nodes, contained_all_nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert contained_all_nodes[0] == n2


def test_index_four(nodes, contained_all_nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert contained_all_nodes[4] == n4


def test_index_one_beyond_the_end(contained_all_nodes):
    with pytest.raises(IndexError):
        contained_all_nodes[11]


def test_index_minus_one(nodes, contained_all_nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert contained_all_nodes[-1] == n10


def test_index_minus_five(nodes, contained_all_nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert contained_all_nodes[-10] == n2


def test_index_one_before_the_beginning(contained_all_nodes):
    with pytest.raises(IndexError):
        contained_all_nodes[-11]


def test_slice_from_start(nodes, contained_all_nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert contained_all_nodes[:3] == Nodes([n2, n6, n9])


def test_slice_to_end(nodes, contained_all_nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert contained_all_nodes[7:] == Nodes([n8, n7, n10])


def test_slice_empty(nodes, contained_all_nodes):
    assert contained_all_nodes[11:] == Nodes()


def test_slice_arbitrary(nodes, contained_all_nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert contained_all_nodes[2:4] == Nodes([n9, n1])


def test_slice_full(contained_all_nodes):
    assert contained_all_nodes[:] == contained_all_nodes


def test_concatenate_intersect(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes([n1, n2, n3])
    t = Nodes([n4, n5, n6])

    assert s + t == Nodes([n1, n2, n3, n4, n5, n6])


def test_get_by_id_positive(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes([n1, n2, n3])
    assert s.from_id(1) == n1
    assert s.from_id(2) == n2
    assert s.from_id(3) == n3


def test_get_by_id_negative(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes([n1, n2, n3])
    with pytest.raises(ValueError):
        s.from_id(4)


def test_add_to_list(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes([n1, n2, n3])

    n20 = Node((1, 1, 8), 20)
    n21 = Node((1, 2, 4), 21)
    n22 = Node((2, 1, 6), 22)
    s.add(n20)
    s.add(n21)
    s.add(n22)

    assert s == Nodes([n2, n20, n1, n21, n22, n3])


def test_remove_from_list(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes([n1, n2, n3])
    s.remove(n3)
    s.remove(n7)

    assert s == Nodes([n1, n2])
