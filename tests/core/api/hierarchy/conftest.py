import pytest

from ada import Assembly, Beam, Part


@pytest.fixture
def bm1():
    return Beam("Bm1", (0, 0, 0), (1, 0, 0), "IPE300")


@pytest.fixture
def bm2():
    return Beam("Bm2", (0, 0, 2), (1, 0, 4), "IPE300")


@pytest.fixture
def bm3():
    return Beam("Bm3", (0, 0, 4), (1, 0, 4), "IPE300")


@pytest.fixture
def assembly_hierarchy() -> Assembly:
    a = Assembly("MyAssembly")
    p1 = Part("my_part1")
    p2 = Part("my_part2")
    p21 = Part("my_part2_subpart1")
    p22 = Part("my_part2_subpart2")
    p3 = Part("my_part3_subpart1")
    p4 = Part("my_part4_subpart1")

    # Level 1
    part = a.add_part(p1)
    a.add_part(p2)

    # Level 2
    part2 = part.add_part(p21)
    part.add_part(p22)

    # Level 3
    subpart3 = part2.add_part(p3)

    # Level 4
    subpart3.add_part(p4)

    return a
