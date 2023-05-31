import pytest

from ada import Beam, Material, Part, Point, Section
from ada.fem import Elem, FemSection, FemSet


@pytest.fixture
def part_with_beam():
    sec = Section("myIPE", from_str="BG800x400x30x40")
    mat = Material("my_mat")

    bm = Beam("my_beam", (0, 0, 0), (1, 0, 0), sec, mat)
    elem = Elem(1, [bm.n1, bm.n2], "line")
    p = Part("my_part") / bm
    fem_set = p.fem.sets.add(FemSet("my_set", [elem]))
    p.fem.sections.add(FemSection("my_sec", "line", fem_set, mat, sec, local_z=(0, 0, 1)))
    p.fem.elements.add(elem)

    return p


@pytest.fixture
def part_with_shell():
    p = Part("my_part")
    mat = Material("my_mat")
    elem = Elem(1, [Point((0, 0, 0)), Point((1, 0, 0)), Point((1, 1, 0)), Point((0, 1, 0))], "quad")
    fem_set = FemSet("my_set", [elem], "elset")
    fem_sec = FemSection("my_sec", "shell", fem_set, mat, thickness=0.01)
    for n in elem.nodes:
        p.fem.nodes.add(n)
    p.fem.elements.add(elem)
    p.fem.sets.add(fem_set)
    p.fem.sections.add(fem_sec)
    return p
