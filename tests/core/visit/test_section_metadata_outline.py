"""A section given only as an outline (a polygon section) still says what box it is in its metadata:
an outline that is a rectangle with four equal round corners gives the box's size and corner radius,
and an inner outline inside it gives the wall."""

import pytest

import ada
from ada.api.curves import CurvePoly2d
from ada.comms.msg_handling.object_metadata import (
    beam_metadata,
    outline_rectangle,
    section_to_dict,
)
from ada.sections.categories import BaseTypes


def _rounded_rect(w, h, r):
    hw, hh = w / 2.0, h / 2.0
    pts = (
        [(-hw, -hh, r), (hw, -hh, r), (hw, hh, r), (-hw, hh, r)]
        if r > 0
        else [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    )
    return CurvePoly2d(pts, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))


def _poly_box(w, h, t, r, name="RHS_poly"):
    return ada.Section(
        name,
        sec_type=BaseTypes.POLY,
        outer_poly=_rounded_rect(w, h, r),
        inner_poly=_rounded_rect(w - 2 * t, h - 2 * t, max(r - t, 0.0)),
        t_w=t,
    )


def test_a_rounded_box_outline_gives_the_box_keys_and_its_corner_radius():
    d = section_to_dict(_poly_box(0.2, 0.2, 0.01, 0.015))
    assert d["type"] == "poly"
    assert d["h"] == pytest.approx(0.2) and d["w_top"] == pytest.approx(0.2) and d["w_btn"] == pytest.approx(0.2)
    assert d["r"] == pytest.approx(0.015)
    assert d["t_w"] == pytest.approx(0.01)
    assert d["t_ftop"] == pytest.approx(0.01) and d["t_fbtn"] == pytest.approx(0.01)


def test_a_rectangular_outline_reads_its_width_and_height_apart():
    d = section_to_dict(_poly_box(0.3, 0.2, 0.012, 0.024))
    assert d["w_top"] == pytest.approx(0.3) and d["h"] == pytest.approx(0.2)
    assert d["r"] == pytest.approx(0.024)
    assert d["t_w"] == pytest.approx(0.012) and d["t_ftop"] == pytest.approx(0.012)


def test_an_outer_corner_with_no_inner_round_still_gives_the_wall():
    # Wall thicker than the radius: the inner corners are square.
    d = section_to_dict(_poly_box(0.2, 0.2, 0.02, 0.015))
    assert d["r"] == pytest.approx(0.015) and d["t_ftop"] == pytest.approx(0.02)


def test_a_square_cornered_outline_says_its_corners_have_no_radius():
    sec = ada.Section("p", sec_type=BaseTypes.POLY, outer_poly=_rounded_rect(0.2, 0.1, 0.0))
    d = section_to_dict(sec)
    assert d["h"] == pytest.approx(0.1) and d["w_top"] == pytest.approx(0.2) and d["r"] == 0.0
    assert d["t_w"] is None


def test_an_outline_that_is_no_rectangle_adds_nothing():
    tri = CurvePoly2d([(0, 0), (0.2, 0), (0.1, 0.15)], origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
    d = section_to_dict(ada.Section("tri", sec_type=BaseTypes.POLY, outer_poly=tri))
    assert d["h"] is None and d["w_top"] is None and d["r"] is None
    assert outline_rectangle(tri) is None


def test_unequal_corner_radii_are_not_a_box():
    pts = [(-0.1, -0.1, 0.015), (0.1, -0.1, 0.015), (0.1, 0.1, 0.03), (-0.1, 0.1, 0.015)]
    curve = CurvePoly2d(pts, origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))
    assert outline_rectangle(curve) is None
    d = section_to_dict(ada.Section("odd", sec_type=BaseTypes.POLY, outer_poly=curve))
    assert d["r"] is None and d["h"] is None


def test_what_a_section_carries_itself_is_kept():
    d = section_to_dict(ada.Section("box", from_str="BG200x200x10x10"))
    assert d["type"] == "BOX" and d["h"] == pytest.approx(0.2) and d["t_w"] == pytest.approx(0.01)


def test_a_beam_with_a_rounded_poly_section_carries_the_radius_in_its_metadata():
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), _poly_box(0.2, 0.2, 0.01, 0.015))
    meta = beam_metadata(bm.name, bm.section, bm.material, beam=bm)
    assert meta["section"]["r"] == pytest.approx(0.015)
    assert meta["section"]["h"] == pytest.approx(0.2)
