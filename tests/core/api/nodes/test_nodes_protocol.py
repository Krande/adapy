from ada.api.containers import Nodes


def test_positive_contained(nodes, contained3nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert n1 in contained3nodes


def test_negative_contained(nodes, contained3nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    assert n5 not in contained3nodes


def test_negative_not_contained(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    n = Nodes([n1, n2, n3, n4, n5, n6, n7, n8, n9, n10])
    assert n1 in n
