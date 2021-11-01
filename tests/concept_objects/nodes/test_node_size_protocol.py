from ada.concepts.containers import Nodes


def test_empty():
    n = Nodes()
    assert len(n) == 0


def test_one(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    n = Nodes([n1])
    assert len(n) == 1


def test_ten(nodes):
    n = Nodes(nodes)
    assert len(n) == 10


def test_with_duplicates(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    n = Nodes([n1, n1, n1])

    assert len(n) == 1
