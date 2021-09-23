import unittest

from ada import Node
from ada.fem import Elem
from ada.fem.containers import FemElements


class TestConstruction(unittest.TestCase):
    def setUp(self) -> None:
        n1 = Node([1.0, 2.0, 3.0], 1)
        n2 = Node([1.0, 1.0, 1.0], 2)
        n3 = Node([2.0, 1.0, 8.0], 3)
        n4 = Node([1.0, 2.0, 3.0], 4)

        el1 = Elem(1, [n1, n2], "B31")
        el2 = Elem(2, [n2, n3], "B31")
        el3 = Elem(3, [n3, n1], "B31")
        el4 = Elem(4, [n1, n2, n3, n4], "S4R")
        self.elems = (el1, el2, el3, el4)

    def test_empty(self):
        n = FemElements([])
        assert len(n) == 0

    def test_from_sequence(self):
        all_elemes = self.elems
        n = FemElements(all_elemes[:3])

        assert len(n) == 3

    def test_with_duplicates(self):
        el1, el2, el3, el4 = self.elems

        with self.assertRaises(ValueError):
            FemElements([el1, el2, el1])

    def test_from_iterables(self):
        el1, el2, el3, el4 = self.elems

        def geniter():
            yield el1
            yield el2
            yield el3

        g = geniter()
        n = FemElements(g)

        assert len(n) == 3


if __name__ == "__main__":
    unittest.main()
