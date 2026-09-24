"""The compiled beam-to-beam pass must find what the Python one finds.

`identify_joints` now uses adacpp's `find_beam_joints` whenever adacpp is installed -- the same
compiled pass the BROWSER runs, which is the point: a joint's id is a hash of its member names and
its origin pass, and a `clash_detail` hand-off re-derives joints from those ids. Two
implementations that disagreed would hand a user a joint no worker could detail.

So this holds the two to each other on models built to exercise the parts that are easy to get
subtly wrong: several beams meeting at ONE node (a joint is per contact point, not per pair), a
near-miss inside the out-of-plane tolerance, a crossing beyond a member's own half length, and
parallel members that never join.
"""

from __future__ import annotations

import pytest

import ada
from ada.clash import native_joints
from ada.clash.identify import identify_joints
from ada.clash.options import ClashOptions

pytestmark = pytest.mark.skipif(
    not native_joints.available(),
    reason="ada-cpp without find_beam_joints; adapy keeps the Python pass there",
)


def _describe(members, centre):
    """What a joint IS, for comparison: who is in it, where it is, and what type it is.

    The TYPE KEY is in here deliberately. It is not merely a label -- it is what the panel groups
    by and what a spec matches against -- and it depends on the joint's member ORDER, because the
    angle is taken between the first two. Comparing only names and centres would let the two
    passes agree on every joint and still type them differently, which is the drift most likely to
    go unnoticed.
    """
    from ada.clash.classify import describe_member, type_key_for
    from ada.clash.match import _angle_between

    described = [describe_member(m) for m in members]
    angle = _angle_between(members[0], members[1]) if len(members) >= 2 else None
    return (
        tuple(sorted(m.name for m in described)),
        tuple(round(float(v), 6) for v in centre),
        type_key_for(described, angle),
    )


def _python_joints(part, options):
    """`identify_joints`' original path, forced, so the two can be compared on one model."""
    from ada.api.containers.connections import Connections

    connections = Connections(parent=part)
    connections.find(out_of_plane_tol=options.out_of_plane_tol, point_tol=options.point_tol)
    out = []
    for joint in connections:
        members = list(joint.beams)
        if len(members) < 2:
            continue
        out.append(_describe(members, joint.centre))
    return sorted(out)


def _native_joints(part, options):
    outcome = identify_joints(part, options)
    out = []
    for found in outcome.joints:
        if found.origin != "beam-beam":
            continue
        out.append(_describe(found.members, found.centre))
    return sorted(out)


def _check(model, options=None):
    options = options or ClashOptions(include_plate_joints=False)
    assembly = ada.Assembly("a") / model
    return _native_joints(assembly, options), _python_joints(assembly, options)


def test_three_beams_at_one_node_are_one_joint_either_way():
    """Per contact POINT, not per pair -- a pair-wise reading would report three joints here."""
    part = ada.Part("p") / [
        ada.Beam("g0", (0, 0, 0), (5, 0, 0), "IPE200"),
        ada.Beam("g1", (5, 0, 0), (5, 5, 0), "IPE200"),
        ada.Beam("c0", (5, 0, 0), (5, 0, 3), "IPE200"),
    ]
    native, python = _check(part)
    assert native == python
    assert len(native) == 1
    assert native[0][0] == ("c0", "g0", "g1")


def test_a_full_frame_agrees():
    part = ada.Part("p") / [
        ada.Beam("g0", (0, 0, 0), (5, 0, 0), "IPE200"),
        ada.Beam("g1", (5, 0, 0), (5, 5, 0), "IPE200"),
        ada.Beam("g2", (5, 5, 0), (0, 5, 0), "IPE200"),
        ada.Beam("g3", (0, 5, 0), (0, 0, 0), "IPE200"),
        ada.Beam("s0", (0, 2.5, 0), (5, 2.5, 0), "HP140x8"),
        ada.Beam("c0", (0, 0, 0), (0, 0, 3), "HEB200"),
        ada.Beam("c1", (5, 5, 0), (5, 5, 3), "HEB200"),
        ada.Beam("br", (0, 0, 0), (5, 5, 3), "IPE200"),
    ]
    native, python = _check(part)
    assert native == python
    assert native, "a frame with columns, girders and a brace must yield joints at all"


def test_parallel_members_are_not_a_joint_either_way():
    part = ada.Part("p") / [
        ada.Beam("a", (0, 0, 0), (5, 0, 0), "IPE200"),
        ada.Beam("b", (0, 1, 0), (5, 1, 0), "IPE200"),
    ]
    native, python = _check(part)
    assert native == python == []


def test_a_crossing_far_past_a_member_end_is_not_its_joint():
    """Two non-parallel lines always meet somewhere; past half a member's own length it is not
    that member's joint, and both passes have to draw the line in the same place."""
    part = ada.Part("p") / [
        ada.Beam("a", (0, 0, 0), (1, 0, 0), "IPE200"),
        ada.Beam("b", (8, -1, 0), (8, 1, 0), "IPE200"),
    ]
    native, python = _check(part)
    assert native == python == []


def test_a_mid_span_crossing_is_found_by_both():
    """The case that used to be found by NEITHER, and is the reason this file exists.

    `Beams.get_beams_within_volume` indexes beams by their END NODES, so a beam was only ever a
    candidate if one of its ends landed inside the other's box. Two long beams crossing away from
    either's ends have no such endpoint, were never offered to `beam_cross_check`, and so were
    never a joint however plainly they met. `basic_intersect` now compares BOX AGAINST BOX.
    """
    part = ada.Part("p") / [
        ada.Beam("a", (-5, 0, 0), (5, 0, 0), "IPE200"),
        ada.Beam("b", (0, -5, 0), (0, 5, 0), "IPE200"),
    ]
    native, python = _check(part)
    assert native == python
    assert len(native) == 1, "two beams crossing at mid-span meet, whatever indexed the candidates"


def test_one_contact_is_one_joint_even_when_the_members_only_nearly_meet():
    """A near miss inside the tolerance has a different closest point on each member's line.

    Taking the point on whichever member was asked first registers the same physical contact
    twice, at two places up to `out_of_plane_tol` apart -- far beyond `point_tol`, so they never
    merge. Both passes take the midpoint.
    """
    part = ada.Part("p") / [
        ada.Beam("a", (0, 0, 0), (5, 0, 0), "IPE200"),
        ada.Beam("b", (2.5, -1, 0.05), (2.5, 1, 0.05), "IPE200"),
    ]
    native, python = _check(part)
    assert native == python
    assert len(native) == 1
    assert native[0][1] == (2.5, 0.0, 0.025)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_the_two_passes_agree_on_random_frames(seed):
    """The anti-drift gate.

    Hand-picked cases pin the behaviours someone thought of. These pin that the two
    implementations stay the same implementation: a change to either that alters a tolerance, the
    half-length rule, the node merge or the candidate filter will disagree on some frame in here
    long before it disagrees on a model anyone notices.
    """
    import random

    rng = random.Random(seed)

    def coord():
        # A coarse grid, so beams genuinely share ends and cross mid-span rather than all missing
        # each other -- random points in a continuum would make almost every pair a non-joint and
        # the test would pass without exercising anything.
        return float(rng.choice([0.0, 1.0, 2.0, 3.0]))

    beams = []
    for i in range(14):
        p1 = (coord(), coord(), coord())
        p2 = (coord(), coord(), coord())
        if p1 == p2:
            continue
        beams.append(ada.Beam(f"b{i}", p1, p2, rng.choice(["IPE200", "HEB200", "TUB200x10"])))
    if len(beams) < 2:
        pytest.skip("degenerate draw")

    native, python = _check(ada.Part("p") / beams)
    assert native == python


def test_the_out_of_plane_tolerance_is_honoured_identically():
    """A near miss inside the tolerance is a joint; the same miss outside it is not."""
    part = lambda dz: ada.Part("p") / [  # noqa: E731 - a fixture factory reads better inline here
        ada.Beam("a", (0, 0, 0), (5, 0, 0), "IPE200"),
        ada.Beam("b", (2.5, -1, dz), (2.5, 1, dz), "IPE200"),
    ]
    inside_native, inside_python = _check(part(0.05))
    assert inside_native == inside_python
    assert len(inside_native) == 1

    outside_native, outside_python = _check(part(0.5))
    assert outside_native == outside_python == []
