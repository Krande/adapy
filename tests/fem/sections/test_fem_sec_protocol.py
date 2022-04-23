from ada import Beam, Material, Node, Section
from ada.fem import Elem, FemSection, FemSet
from ada.materials.metals import CarbonSteel


def test_positive_contained(part_with_beam):
    fem_sec = part_with_beam.fem.sections[0]

    assert fem_sec in part_with_beam.fem.sections


def test_negative_sec_contained(part_with_beam):
    # A minor change in section box thickness
    sec = Section("myBG", from_str="BG800x400x20x40")
    mat = Material("my_mat")

    bm = Beam("my_beam", (0, 0, 0), (1, 0, 0), sec, mat)
    elem = Elem(1, [bm.n1, bm.n2], "line")
    fem_set = FemSet("my_set", [elem], "elset")
    fem_sec = FemSection("my_sec", "line", fem_set, mat, sec, local_z=(0, 0, 1))

    assert fem_sec not in part_with_beam.fem.sections


def test_negative_mat_contained(part_with_beam):
    # A minor change in material property (S420 instead of S355)
    sec = Section("myBG", from_str="BG800x400x30x40")

    mat = Material("my_mat", CarbonSteel("S420"))

    bm = Beam("my_beam", (0, 0, 0), (1, 0, 0), sec, mat)
    elem = Elem(1, [bm.n1, bm.n2], "line")
    fem_set = FemSet("my_set", [elem], "elset")
    fem_sec = FemSection("my_sec", "line", fem_set, mat, sec, local_z=(0, 0, 1))

    assert fem_sec not in part_with_beam.fem.sections


def test_positive_contained_shell(part_with_shell):
    fem_sec = part_with_shell.fem.sections[0]

    assert fem_sec in part_with_shell.fem.sections


def test_negative_contained_shell(part_with_shell):
    # Testing equal operator for different shell thickness
    mat = Material("my_mat")
    elem = Elem(1, [Node((0, 0, 0)), Node((1, 0, 0)), Node((1, 1, 0)), Node((0, 1, 0))], "quad")
    fem_set = FemSet("my_set", [elem], "elset")
    fem_sec = FemSection("my_sec", "shell", fem_set, mat, thickness=0.02)

    assert fem_sec not in part_with_shell.fem.sections


def test_negative_contained_shell_(part_with_shell):
    # Testing equal operator for change in element type
    mat = Material("my_mat")
    elem = Elem(1, [Node((0, 0, 0)), Node((1, 0, 0)), Node((1, 1, 0)), Node((0, 1, 0))], "quad")
    fem_set = FemSet("my_set", [elem], "elset")
    fem_sec = FemSection("my_sec", "shell", fem_set, mat, thickness=0.01)

    assert fem_sec not in part_with_shell.fem.sections
