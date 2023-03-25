import pytest

import ada
from ada.fem.formats.abaqus.read.read_sections import (
    get_beam_sections_from_inp,
    get_shell_sections_from_inp,
)


@pytest.fixture
def part():
    p = ada.Part("MyPart")
    p.add_material(ada.Material("S355"))
    p.fem.add_set(ada.fem.FemSet("MAT", [], "elset"))
    p.fem.add_set(ada.fem.FemSet("BSEC8", [], "elset"))
    p.fem.add_set(ada.fem.FemSet("BSEC9", [], "elset"))
    p.fem.add_set(ada.fem.FemSet("BG500X", [], "elset"))
    return p


def test_read_shell_section(shell_beam_section, re_in, part):
    res = list(get_shell_sections_from_inp(shell_beam_section, part.fem))
    assert len(res) == 1
    m = res[0]
    assert m.name == "sh1"
    assert m.elset.name == "MAT"
    assert m.material.name == "S355"


def test_read_beam_section(shell_beam_section, re_in, part):
    res = list(get_beam_sections_from_inp(shell_beam_section, part.fem))
    assert len(res) == 3

    fs1 = res[0]
    fs2 = res[1]
    fs3 = res[2]

    assert fs1.name == "BSEC8"
    assert fs2.name == "BSEC9"
    assert fs3.name == "BG500X"
