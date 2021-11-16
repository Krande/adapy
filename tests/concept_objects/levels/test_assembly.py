import copy

import pytest

from ada import Assembly, Beam, Part, Section


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
def secvar():
    return dict(sec_type="IG", h=0.8, w_top=0.2, w_btn=0.2, t_fbtn=0.01, t_ftop=0.01, t_w=0.01)


@pytest.fixture
def sec1(secvar):
    return Section(name="sec1", **secvar)


def test_section_equal(sec1, secvar):
    sec2 = copy.deepcopy(sec1)
    sec2.name = "sec2"
    sec3 = Section(name="sec3", **secvar)
    list_of_secs = [sec1, sec2, sec3]
    assert sec1 == sec1
    assert sec1 in list_of_secs
    assert Section(name="sec4", **secvar) not in list_of_secs


def test_parts_hierarchy():
    a = Assembly("MyAssembly")
    p1 = Part("my_part1")
    p2 = Part("my_part2")
    p21 = Part("my_part2_subpart1")
    p22 = Part("my_part2_subpart2")
    p3 = Part("my_part2_subpart1")
    p4 = Part("my_part3_subpart1")

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

    list_of_ps = a.get_all_parts_in_assembly()

    assert len(list_of_ps) == 6

    assert len(a.parts[p1.name].parts) == 2
    assert len(a.parts[p1.name].parts[p21.name].parts) == 1
    assert len(a.parts[p1.name].parts[p21.name].parts[p3.name].parts) == 1


def test_ex1(bm1, bm2):
    a = Assembly("MyAssembly") / [Part("MyPart") / bm1, bm2]
    p = a.parts["MyPart"]
    assert p.beams.from_name("Bm1")
    assert a.beams.from_name("Bm2")


def test_ex2(bm1, bm2, bm3):
    a = Assembly("MyAssembly") / (Part("MyPart") / [bm1, bm2, bm3])
    p = a.parts["MyPart"]
    assert p.beams.from_name("Bm1")
    assert p.beams.from_name("Bm2")
