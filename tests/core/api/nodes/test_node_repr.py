from ada.api.containers import Nodes


def test_repr_empty():
    s = Nodes()
    assert repr(s) == "Nodes(0, min_id: 0, max_id: 0)"


def test_repr_some(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes([n1, n2, n3])
    assert repr(s) == "Nodes(3, min_id: 1, max_id: 3)"
