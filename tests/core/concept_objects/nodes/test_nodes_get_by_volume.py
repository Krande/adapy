from ada import Point
from ada.concepts.containers import Nodes


def test_get_by_volume_point(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes(nodes)
    c = Nodes(s.get_by_volume(p=(4.0, 5.0, 1.0)))
    assert c == Nodes([n7])


def test_get_by_volume_box(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes(nodes)
    c = Nodes(s.get_by_volume(p=(1.5, 0.5, 0.5), vol_box=(4.5, 5.5, 8.5)))
    assert c == Nodes([n3, n7, n8])


def test_get_by_volume_cylinder(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes
    s = Nodes(nodes)
    c = Nodes(s.get_by_volume(p=(1.0, 1.0, 0.5), vol_cyl=(0.2, 4, 0.2)))
    assert c == Nodes([n2, n6, n9])


def test_in_between():
    p1 = 284.651885, 130.233454, 553.35
    p2 = 284.651885, 130.233454, 553.425
    p3 = 284.651885, 130.233454, 553.5
    p4 = 284.651885, 130.233454, 554.5
    n1 = Point(p1, 1)
    n2 = Point(p2, 2)
    n3 = Point(p3, 3)
    n4 = Point(p4, 4)
    nodes = Nodes([n1, n2, n3, n4])
    res = Nodes(nodes.get_by_volume(p=p1))
    assert len(res) == 1


def test_not_in(nodes):
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = nodes

    s = Nodes([n1, n2, n3, n4, n5, n6, n7, n8, n9, n10])

    n11 = Point((0, 0, 0), 10000)
    assert n11 not in s

    assert n10 in s
