"""Three-node beams with an outline CalculiX has a keyword for go out as B32R with that outline."""

from __future__ import annotations

import pytest

import ada
from ada.fem.exceptions import IncompatibleElements
from ada.fem.formats.calculix.write.write_elements import B32R, beam_element_type
from ada.fem.formats.calculix.write.writer import beam_str
from ada.fem.meshing.concepts import GmshOptions
from ada.materials.metals import CarbonSteel


def beam_section(section: str):
    beam = ada.Beam("bm", (0, 0, 0), (4, 0, 0), section, ada.Material("S355", CarbonSteel("S355")))
    part = ada.Part("beam") / beam
    ada.Assembly("a") / part
    fem = part.to_fem_obj(1.0, bm_repr="line", options=GmshOptions(Mesh_ElementOrder=2))
    return next(iter(fem.sections.lines))


@pytest.mark.parametrize("section", ["FB100x10", "CIRC50"])
def test_the_outline_is_a_b32r(section):
    fem_sec = beam_section(section)
    assert beam_element_type(fem_sec.elset.members[0].type, fem_sec) == B32R


def test_a_flat_bar_is_a_rect_with_its_width_along_local_y():
    lines = beam_str(beam_section("FB100x10")).splitlines()
    assert lines[1].endswith("section=RECT") and lines[2] == "0.01, 0.1"


def test_a_circle_is_written_by_its_diameter():
    lines = beam_str(beam_section("CIRC50")).splitlines()
    assert lines[1].endswith("section=CIRC") and lines[2] == "0.1, 0.1"


def test_an_i_section_is_still_refused():
    fem_sec = beam_section("IPE300")
    with pytest.raises(IncompatibleElements, match="three-node beam"):
        beam_element_type(fem_sec.elset.members[0].type, fem_sec)
