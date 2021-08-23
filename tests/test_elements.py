import unittest

from ada import Node
from ada.fem import Elem
from ada.fem.containers import FemElements


def get_nodes():
    n1 = Node([1.0, 2.0, 3.0], 1)
    n2 = Node([1.0, 1.0, 1.0], 2)
    n3 = Node([2.0, 1.0, 8.0], 3)
    n4 = Node([1.0, 2.0, 3.0], 4)
    n5 = Node([1.0, 3.0, 2.0], 5)
    n6 = Node([1.0, 1.0, 3.0], 6)
    n7 = Node([4.0, 5.0, 1.0], 7)
    n8 = Node([2.0, 4.0, 3.0], 8)
    n9 = Node([1.0, 1.0, 4.0], 9)
    n10 = Node([5.0, 2.0, 3.0], 10)
    return n1, n2, n3, n4, n5, n6, n7, n8, n9, n10


def get_elems():
    n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
    el1 = Elem(1, [n1, n2], "B31")
    el2 = Elem(2, [n2, n3], "B31")
    el3 = Elem(3, [n3, n1], "B31")
    el4 = Elem(4, [n1, n2, n3, n4], "S4R")
    return el1, el2, el3, el4


class TestConstruction(unittest.TestCase):
    def test_empty(self):
        n = FemElements([])
        assert len(n) == 0

    def test_from_sequence(self):
        all_elemes = get_elems()
        n = FemElements(all_elemes[:3])

        assert len(n) == 3

    def test_with_duplicates(self):
        el1, el2, el3, el4 = get_elems()

        with self.assertRaises(ValueError):
            FemElements([el1, el2, el1])

    def test_from_iterables(self):
        el1, el2, el3, el4 = get_elems()

        def geniter():
            yield el1
            yield el2
            yield el3

        g = geniter()
        n = FemElements(g)

        assert len(n) == 3


if __name__ == "__main__":
    unittest.main()
