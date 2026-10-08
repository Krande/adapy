"""Angle section strings: ``L<h>x<t>`` (equal legs) and ``L<h>x<b>x<t>`` (EN 10056 notation, legs h
and b, thickness t).

``L100x100x10`` used to match ``L<h>x<t>`` on its first two numbers and ignore the third: a 100 mm
thick L100, a square, which the angle properties refuse by name (no second leg). ``L150x90x10`` was a
90 mm thick L150.
"""

from __future__ import annotations

import pytest

import ada
from ada.sections.properties import calculate_general_properties

CASES = {
    "L100x10": (0.1, 0.1, 0.01),
    "L100x100x10": (0.1, 0.1, 0.01),
    "L150x90x10": (0.15, 0.09, 0.01),
    "L200x100x14": (0.2, 0.1, 0.014),
}


@pytest.mark.parametrize("sec_str", list(CASES))
def test_an_angle_string_gives_its_legs_and_thickness(sec_str):
    h, b, t = CASES[sec_str]
    sec = ada.Section.from_str(sec_str)
    assert sec.type == sec.TYPES.ANGULAR
    assert (sec.h, sec.w_btn, sec.w_top, sec.t_w, sec.t_fbtn, sec.t_ftop) == pytest.approx(
        (h, b, b, t, t, t), rel=1e-12
    )


def test_the_three_number_equal_angle_is_the_two_number_one():
    """Same section, same properties -- Ax 1.9e-3 = (2 x 100 - 10) x 10 mm^2."""
    a, b = ada.Section.from_str("L100x100x10"), ada.Section.from_str("L100x10")
    assert a.unique_props() == b.unique_props()
    pa, pb = calculate_general_properties(a), calculate_general_properties(b)
    assert pa.Ax == pytest.approx(1.9e-3, rel=1e-12) and pa == pb


@pytest.mark.parametrize("sec_str", ["L100x10", "L150x90x10"])
def test_an_angle_section_string_reads_back_as_the_angle(sec_str):
    """``sec_str`` gives the equal angle as ``L<h>x<t>`` and an unequal one as ``L<h>x<b>x<t>``."""
    sec = ada.Section.from_str(sec_str)
    assert sec.sec_str == sec_str
    assert ada.Section.from_str(sec.sec_str).unique_props() == sec.unique_props()


def test_a_bulb_flat_keeps_its_profile_name():
    """An HP from the profile database is named by it, not described as an L."""
    assert ada.Section.from_str("HP180x10").sec_str == "HP180x10"
