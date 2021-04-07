import unittest

from ada import Node
from ada.core.containers import Nodes


def get_nodes():
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


class TestConstruction(unittest.TestCase):
    def test_empty(self):
        n = Nodes([])
        assert len(n) == 0

    def test_from_sequence(self):
        all_nodes = get_nodes()
        n = Nodes(all_nodes[:3])

        assert len(n) == 3

    def test_with_duplicates(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        n = Nodes([n1, n2, n1])

        assert len(n) == 2

    def test_from_iterables(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()

        def geniter():
            yield n1
            yield n2
            yield n3

        g = geniter()
        n = Nodes(g)

        assert len(n) == 3


class TestContainerProtocol(unittest.TestCase):
    def setUp(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.n = Nodes([n1, n2, n3])

    def test_positive_contained(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertTrue(n1 in self.n)

    def test_negative_contained(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertFalse(n5 in self.n)

    def test_positive_not_contained(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertTrue(n5 not in self.n)

    def test_negative_not_contained(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        n = Nodes([n1, n2, n3, n4, n5, n6, n7, n8, n9, n10])
        self.assertFalse(n1 not in n)


class TestSizedProtocol(unittest.TestCase):
    def setUp(self):
        self.n = Nodes(get_nodes())

    def test_empty(self):
        n = Nodes()
        self.assertEqual(len(n), 0)

    def test_one(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        n = Nodes([n1])
        self.assertEqual(len(n), 1)

    def test_ten(self):
        le = len(self.n)
        print(le, type(le), len(get_nodes()))
        self.assertEqual(le, 10)

    def test_with_duplicates(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        n = Nodes([n1, n1, n1])

        self.assertEqual(len(n), 1)


class TestIterableProtocol(unittest.TestCase):
    def setUp(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.n = Nodes([n1, n2, n3])

    def test_iter(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        i = iter(self.n)
        self.assertEqual(next(i), n2)
        self.assertEqual(next(i), n1)
        self.assertEqual(next(i), n3)

    def test_for_loop(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        expected = [n2, n1, n3]
        for i, item in enumerate(self.n):
            self.assertEqual(item, expected[i])


class TestEqualityProtocol(unittest.TestCase):
    def test_positive_equal(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertTrue(Nodes([n1, n2, n3]) == Nodes([n1, n2, n3]))

    def test_negative_equal(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertFalse(Nodes([n1, n2, n3]) == Nodes([n4, n5, n6]))

    def test_type_mismatch(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertFalse(Nodes([n1, n2, n3]) == [n1, n2, n3])

    def test_identical(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes([n1, n2, n3])
        self.assertTrue(s == s)

    def test_random(self):
        n1 = Node((1.0, 2.0, 3.0), 1)
        n5 = Node((1.0, 3.0, 2.0), 5)

        assert tuple(n1.p) < tuple(n5.p)


class TestSequenceProtocol(unittest.TestCase):
    def setUp(self):
        self.n = Nodes(get_nodes())

    def test_index_zero(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertEqual(self.n[0], n2)

    def test_index_four(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertEqual(self.n[4], n4)

    def test_index_one_beyond_the_end(self):
        with self.assertRaises(IndexError):
            self.n[11]

    def test_index_minus_one(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertEqual(self.n[-1], n10)

    def test_index_minus_five(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertEqual(self.n[-10], n2)

    def test_index_one_before_the_beginning(self):
        with self.assertRaises(IndexError):
            self.n[-11]

    def test_slice_from_start(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertEqual(self.n[:3], Nodes([n2, n6, n9]))

    def test_slice_to_end(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertEqual(self.n[7:], Nodes([n8, n7, n10]))

    def test_slice_empty(self):
        self.assertEqual(self.n[11:], Nodes())

    def test_slice_arbitrary(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        self.assertEqual(self.n[2:4], Nodes([n9, n4]))

    def test_slice_full(self):
        self.assertEqual(self.n[:], self.n)

    def test_concatenate_intersect(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes([n1, n2, n3])
        t = Nodes([n4, n5, n6])

        self.assertEqual(s + t, Nodes([n1, n2, n3, n4, n5, n6]))

    def test_get_by_id_positive(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes([n1, n2, n3])
        self.assertEqual(s.from_id(1), n1)
        self.assertEqual(s.from_id(2), n2)
        self.assertEqual(s.from_id(3), n3)

    def test_get_by_id_negative(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes([n1, n2, n3])
        with self.assertRaises(ValueError):
            s.from_id(4)

    def test_add_to_list(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes([n1, n2, n3])

        n20 = Node((1, 1, 8), 20)
        n21 = Node((1, 2, 4), 21)
        n22 = Node((2, 1, 6), 22)
        s.add(n20)
        s.add(n21)
        s.add(n22)

        self.assertEqual(s, Nodes([n2, n20, n1, n21, n22, n3]))


class TestReprProtocol(unittest.TestCase):
    def test_repr_empty(self):
        s = Nodes()
        self.assertEqual(repr(s), "Nodes()")

    def test_repr_some(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes([n1, n2, n3])
        self.assertEqual(
            repr(s), "Nodes([Node([1.0, 1.0, 1.0], 2), Node([1.0, 2.0, 3.0], 1), Node([2.0, 1.0, 8.0], 3)])"
        )


class TestGetByVolume(unittest.TestCase):
    def test_get_by_volume_point(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes(get_nodes())
        c = Nodes(s.get_by_volume(p=(4.0, 5.0, 1.0)))
        self.assertEqual(c, Nodes([n7]))

    def test_get_by_volume_box(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes(get_nodes())
        c = Nodes(s.get_by_volume(p=(1.5, 0.5, 0.5), vol_box=(4.5, 5.5, 8.5)))
        self.assertEqual(c, Nodes([n3, n7, n8]))

    def test_get_by_volume_cylinder(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()
        s = Nodes(get_nodes())
        c = Nodes(s.get_by_volume(p=(1.0, 1.0, 0.5), vol_cyl=(0.2, 4, 0.2)))
        self.assertEqual(c, Nodes([n2, n6, n9]))

    def test_in_between(self):
        p1 = 284.651885, 130.233454, 553.35
        p2 = 284.651885, 130.233454, 553.425
        p3 = 284.651885, 130.233454, 553.5
        p4 = 284.651885, 130.233454, 554.5
        n1 = Node(p1, 1)
        n2 = Node(p2, 2)
        n3 = Node(p3, 3)
        n4 = Node(p4, 4)
        nodes = Nodes([n1, n2, n3, n4])
        res = Nodes(nodes.get_by_volume(p=p1))
        self.assertEqual(len(res), 1)

    def test_not_in(self):
        n1, n2, n3, n4, n5, n6, n7, n8, n9, n10 = get_nodes()

        s = Nodes([n1, n2, n3, n4, n5, n6, n7, n8, n9, n10])

        n11 = Node((0, 0, 0), 10000)
        assert n11 not in s

        assert n10 in s


if __name__ == "__main__":
    unittest.main()
