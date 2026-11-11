import xml.etree.ElementTree as ET

import pytest

from ada.api.fasteners import WeldType
from ada.api.weld_symbols import weld_symbol, weld_symbol_svg

SVG = "{http://www.w3.org/2000/svg}"


@pytest.mark.parametrize("wt", [w for w in WeldType if w is not WeldType.NONE], ids=lambda w: w.value)
def test_every_weld_type_but_none_has_a_symbol_on_the_reference_line(wt):
    symbol = weld_symbol(wt)
    assert symbol is not None and symbol.weld_type is wt
    assert symbol.paths(), "a symbol draws something"
    for path in symbol.paths():
        for x, y in path:
            # The unit box, standing on the line (a spot/seam is centred on it, as ISO draws it).
            assert -0.05 <= x <= 1.05 and -0.3 <= y <= 1.0, (wt, x, y)


def test_none_has_no_symbol():
    assert weld_symbol(WeldType.NONE) is None
    assert weld_symbol_svg(WeldType.NONE) is None


def test_a_symbol_is_found_by_name_too():
    assert weld_symbol("WELD_TYPE_FILLET") is weld_symbol(WeldType.FILLET)


def test_mirroring_hangs_the_symbol_below_the_line():
    fillet = weld_symbol(WeldType.FILLET)
    below = fillet.mirrored()
    assert [[(x, -y) for x, y in p] for p in fillet.paths()] == [list(p) for p in below.paths()]
    assert all(y <= 0 for p in below.paths() for _, y in p)


@pytest.mark.parametrize("wt", [w for w in WeldType if w is not WeldType.NONE], ids=lambda w: w.value)
def test_the_svg_is_well_formed_and_draws_the_symbol_and_its_reference_line(wt):
    svg = ET.fromstring(weld_symbol_svg(wt, size=32))
    assert svg.tag == f"{SVG}svg" and svg.get("width") == "32"
    assert len(svg.findall(f"{SVG}line")) == 1  # the reference line
    shapes = svg.findall(f"{SVG}polyline") + svg.findall(f"{SVG}polygon")
    assert len(shapes) == len(weld_symbol(wt).paths())
    assert svg.find(f"{SVG}title").text == weld_symbol(wt).description


def test_the_partial_pen_symbol_combines_the_bevel_with_a_fillet():
    pp = weld_symbol(WeldType.PARTIAL_PENETRATION_SINGLE_BEVEL_BUTT_PLUS_FILLET)
    bevel = weld_symbol(WeldType.BEVEL_GROOVE_SINGLE_BEVEL_BUTT)
    assert pp.strokes == bevel.strokes
    assert len(pp.outlines) == 1  # the fillet
