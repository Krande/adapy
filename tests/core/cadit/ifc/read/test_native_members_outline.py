"""A member whose profile name is not in the catalogue gets its section from its OUTLINE.

Kept apart from `test_native_members.py`: these build the member records directly, so they need
no adacpp and run in every env, where that module is deselected without the native kernel.
"""

from __future__ import annotations

import pytest

import ada
from ada.cadit.ifc.read.native_members import _section_for, members_to_part
from ada.sections.categories import BaseTypes

_OUTLINE = [(-0.05, -0.1), (0.05, -0.1), (0.05, 0.1), (-0.05, 0.1)]


def _record(**overrides) -> dict:
    record = {
        "id": 7,
        "ifc_class": "IFCBEAM",
        "name": "b0",
        "guid": "",
        "profile_name": "project label 123",
        "outline": _OUTLINE,
        "p1": (0.0, 0.0, 0.0),
        "p2": (2.0, 0.0, 0.0),
    }
    record.update(overrides)
    return record


def test_an_unknown_profile_name_falls_back_to_a_poly_section_of_the_outline():
    sec = _section_for(_record())
    assert sec.type == BaseTypes.POLY
    assert sec.name == "project label 123"
    xs = [p[0] for p in sec.poly_outer.points2d]
    ys = [p[1] for p in sec.poly_outer.points2d]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((-0.05, 0.05, -0.1, 0.1))


def test_an_unnamed_profile_is_named_after_the_member_id():
    assert _section_for(_record(profile_name="")).name == "profile_7"


def test_fewer_than_three_outline_points_give_no_section():
    assert _section_for(_record(profile_name="", outline=_OUTLINE[:2])) is None


def test_a_beam_with_an_outline_section_reaches_the_part():
    part = members_to_part([_record()])
    beams = list(part.get_all_physical_objects(by_type=ada.Beam))
    assert [b.name for b in beams] == ["b0"]
    assert beams[0].section.type == BaseTypes.POLY


def _framed(xdir, normal, **overrides) -> dict:
    # Identity placement, so the swept plane's LOCAL vectors are already world.
    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    return _record(placement=identity, origin=(0.0, 0.0, 0.0), xdir=xdir, normal=normal, **overrides)


def test_a_beam_takes_its_orientation_from_the_swept_profile():
    # Profile +x along world +z, extruded along the axis (+x): the profile's +y is world -y.
    (beam,) = members_to_part([_framed(xdir=(0.0, 0.0, 1.0), normal=(1.0, 0.0, 0.0))]).get_all_physical_objects()
    assert tuple(beam.up) == pytest.approx((0.0, -1.0, 0.0))
    assert tuple(beam.yvec) == pytest.approx((0.0, 0.0, 1.0))


def test_a_profile_not_square_to_the_axis_keeps_the_default_orientation():
    default = ada.Beam("ref", (0, 0, 0), (2, 0, 0), _section_for(_record()))
    # Profile +y along the axis: not a section of this beam, so nothing is taken from it.
    (beam,) = members_to_part([_framed(xdir=(0.0, 0.0, 1.0), normal=(0.0, 1.0, 0.0))]).get_all_physical_objects()
    assert tuple(beam.up) == pytest.approx(tuple(default.up))
