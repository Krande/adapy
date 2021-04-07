import unittest

from ada import Beam, Material, Node, Part, Section
from ada.fem import Elem, FemSection, FemSet
from ada.materials.metals import CarbonSteel


def get_fsec_bm_collection():
    sec = Section("myIPE", from_str="BG800x400x30x40")
    mat = Material("my_mat")
    p = Part("my_part")
    bm = Beam("my_beam", (0, 0, 0), (1, 0, 0), sec, mat)
    elem = Elem(1, [bm.n1, bm.n2], "B31")
    fem_set = FemSet("my_set", [elem], "elset")
    fem_sec = FemSection("my_sec", "beam", fem_set, mat, sec, local_z=(0, 0, 1))

    p.add_beam(bm)
    p.fem.elements.add(elem)
    p.fem.sets.add(fem_set)
    p.fem.sections.add(fem_sec)
    return p


def get_fsec_sh_collection():
    p = Part("my_part")
    mat = Material("my_mat")
    elem = Elem(1, [Node((0, 0, 0)), Node((1, 0, 0)), Node((1, 1, 0)), Node((0, 1, 0))], "S4R")
    fem_set = FemSet("my_set", [elem], "elset")
    fem_sec = FemSection("my_sec", "shell", fem_set, mat, thickness=0.01)
    for n in elem.nodes:
        p.fem.nodes.add(n)
    p.fem.elements.add(elem)
    p.fem.sets.add(fem_set)
    p.fem.sections.add(fem_sec)
    return p


class TestContainerProtocol(unittest.TestCase):
    def test_positive_contained(self):
        p = get_fsec_bm_collection()

        fem_sec = p.fem.sections[0]

        self.assertTrue(fem_sec in p.fem.sections)

    def test_negative_sec_contained(self):
        # A minor change in section box thickness
        sec = Section("myBG", from_str="BG800x400x20x40")
        mat = Material("my_mat")

        bm = Beam("my_beam", (0, 0, 0), (1, 0, 0), sec, mat)
        elem = Elem(1, [bm.n1, bm.n2], "B31")
        fem_set = FemSet("my_set", [elem], "elset")
        fem_sec = FemSection("my_sec", "beam", fem_set, mat, sec)
        p = get_fsec_bm_collection()

        self.assertFalse(fem_sec in p.fem.sections)

    def test_negative_mat_contained(self):
        # A minor change in material property (S420 instead of S355)
        sec = Section("myBG", from_str="BG800x400x30x40")

        mat = Material("my_mat", CarbonSteel("S420"))

        bm = Beam("my_beam", (0, 0, 0), (1, 0, 0), sec, mat)
        elem = Elem(1, [bm.n1, bm.n2], "B31")
        fem_set = FemSet("my_set", [elem], "elset")
        fem_sec = FemSection("my_sec", "beam", fem_set, mat, sec)
        p = get_fsec_bm_collection()

        self.assertFalse(fem_sec in p.fem.sections)

    def test_positive_contained_shell(self):
        p = get_fsec_sh_collection()

        fem_sec = p.fem.sections[0]

        self.assertTrue(fem_sec in p.fem.sections)

    def test_negative_contained_shell(self):
        # Testing equal operator for different shell thickness
        mat = Material("my_mat")
        elem = Elem(1, [Node((0, 0, 0)), Node((1, 0, 0)), Node((1, 1, 0)), Node((0, 1, 0))], "S4R")
        fem_set = FemSet("my_set", [elem], "elset")
        fem_sec = FemSection("my_sec", "shell", fem_set, mat, thickness=0.02)

        p = get_fsec_sh_collection()

        self.assertFalse(fem_sec in p.fem.sections)

    def test_negative_contained_shell_(self):
        # Testing equal operator for change in element type
        mat = Material("my_mat")
        elem = Elem(1, [Node((0, 0, 0)), Node((1, 0, 0)), Node((1, 1, 0)), Node((0, 1, 0))], "S4")
        fem_set = FemSet("my_set", [elem], "elset")
        fem_sec = FemSection("my_sec", "shell", fem_set, mat, thickness=0.01)

        p = get_fsec_sh_collection()

        self.assertFalse(fem_sec in p.fem.sections)


if __name__ == "__main__":
    unittest.main()
