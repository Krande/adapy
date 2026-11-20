"""One weld per seam across the joints of a detail run.

Each builder sees one pair of members, so two joints can weld the same physical seam: two RHS members
ending side by side on one beam both weld the line between their footprints, and an RHS post standing
on a beam beside a flush side member (the side member's end on the beam's side, its top flush with the
beam's top) has its foot edge on the same line as the side member's end edge. ``build_detail`` keeps
one weld per seam, compares the seams by their LINES (each joint types the seam differently), and
records every weld it takes out in ``stats["duplicate_welds"]``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import ada
from ada.api.connections.spec import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    _clear_registry,
    get_registered,
    register_connection,
)
from ada.clash.detail import build_detail, dedup_seams


@pytest.fixture(autouse=True)
def isolate_registry():
    _clear_registry()
    yield
    _clear_registry()


SPEC = ConnectionSpec(
    name="provider.spec",
    roles=(
        MemberCriteria(
            role=MemberRole.INCOMING,
            kind=MemberKind.BEAM,
            angle_to_role=MemberRole.LANDING,
            angle_range=AngleRange(20.0, 165.0),
        ),
        MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM),
    ),
    priority=10,
    capability="pool-x",
)

SECTION = "BOX200x200x10x10"


def _ring(corners):
    """The lines round a polygon of corners: one seam per side."""
    return [(corners[k], corners[(k + 1) % len(corners)]) for k in range(len(corners))]


def _joint(incoming, landing, lines, patch=None):
    contact = SimpleNamespace(extras={"patches": [{"points_incoming": patch}]} if patch else {})
    return SimpleNamespace(
        members=[incoming, landing], landing=landing, centre=(0.0, 0.0, 0.0), contact=contact, lines=lines
    )


def _run(by_id):
    """A provider whose builder welds the lines the joint carries, one weld each."""

    @register_connection(SPEC)
    def builder(*, landing, incoming, name, **_):
        jid = name.removeprefix("provider.spec_")
        conn = ada.Connection(name=name, spec_name="provider.spec")
        for k, (p1, p2) in enumerate(by_id[jid].lines):
            conn.add_weld(
                ada.Weld(f"{jid}_w{k}", p1=p1, p2=p2, xdir=(0, 0, 1), throat=0.005, members=(incoming, landing))
            )
        return conn

    return build_detail(
        get_registered("provider.spec"),
        spec_name="provider.spec",
        joint_ids=list(by_id),
        by_id=by_id,
        gen_options={},
    )


def _welds_by_joint(stats):
    return {c["joint_id"]: sorted(w["name"] for w in c["welds"]) for c in stats["connections"]}


def test_two_members_ending_side_by_side_on_one_beam_weld_the_line_between_them_once():
    beam = ada.Beam("beam", (-1, 0, 0), (1, 0, 0), SECTION)  # top at z = 0.1
    left = ada.Beam("left", (-0.1, 0, 0.1), (-0.1, 0, 1.1), SECTION)
    right = ada.Beam("right", (0.1, 0, 0.1), (0.1, 0, 1.1), SECTION)
    foot_l = [(-0.2, -0.1, 0.1), (0.0, -0.1, 0.1), (0.0, 0.1, 0.1), (-0.2, 0.1, 0.1)]
    foot_r = [(0.0, -0.1, 0.1), (0.2, -0.1, 0.1), (0.2, 0.1, 0.1), (0.0, 0.1, 0.1)]
    by_id = {
        "b": _joint(right, beam, _ring(foot_r), patch=foot_r),
        "a": _joint(left, beam, _ring(foot_l), patch=foot_l),
    }
    _, stats = _run(by_id)

    # Both footprints' outlines carry the shared line, both welds are as long: the joint id decides.
    (dup,) = stats["duplicate_welds"]
    assert dup["joint_id"] == "b" and dup["weld"] == "b_w3"
    assert dup["kept_joint_id"] == "a" and dup["kept_weld"] == "a_w1"
    assert dup["reason"] == "the same seam as a_w1 of provider.spec_a, kept"
    assert _welds_by_joint(stats) == {"a": ["a_w0", "a_w1", "a_w2", "a_w3"], "b": ["b_w0", "b_w1", "b_w2"]}
    # The take-off counts the welds each joint kept.
    assert sorted(i["welds"] for i in stats["joints"]["items"]) == [3, 4]


def test_a_post_foot_edge_flush_with_a_side_members_end_is_welded_once_by_the_joint_found_there():
    """The post's foot (z = 0.1) and the side member's end (x = 0.1) share the edge x = 0.1, z = 0.1.
    Only the post's joint has its patch on that edge here -- the side member's joint is given a patch
    elsewhere -- so the post's weld stays though its joint id sorts after the other's."""
    beam = ada.Beam("beam", (0, -1, 0), (0, 1, 0), SECTION)
    post = ada.Beam("post", (0, 0, 0.1), (0, 0, 1.1), SECTION)
    side = ada.Beam("side", (0.1, 0, 0), (1.1, 0, 0), SECTION)
    foot = [(-0.1, -0.1, 0.1), (0.1, -0.1, 0.1), (0.1, 0.1, 0.1), (-0.1, 0.1, 0.1)]
    end = [(0.1, -0.1, -0.1), (0.1, 0.1, -0.1), (0.1, 0.1, 0.1), (0.1, -0.1, 0.1)]
    by_id = {
        "z_post": _joint(post, beam, _ring(foot), patch=foot),
        "a_side": _joint(side, beam, _ring(end), patch=[(5, 0, 0), (6, 0, 0), (6, 1, 0)]),
    }
    _, stats = _run(by_id)
    (dup,) = stats["duplicate_welds"]
    assert dup["joint_id"] == "a_side" and dup["kept_joint_id"] == "z_post"
    assert dup["weld"] == "a_side_w2" and dup["kept_weld"] == "z_post_w1"


def test_seams_a_plates_thickness_apart_or_only_partly_overlapping_are_both_kept():
    beam = ada.Beam("beam", (-1, 0, 0), (1, 0, 0), SECTION)
    a = ada.Beam("a", (0, 0, 0.1), (0, 0, 1.1), SECTION)
    b = ada.Beam("b", (0, 0.5, 0.1), (0, 0.5, 1.1), SECTION)
    by_id = {
        # A fillet each side of a 10 mm web: two seams.
        "j1": _joint(a, beam, [((0, -0.005, 0.1), (0.3, -0.005, 0.1))]),
        "j2": _joint(b, beam, [((0, 0.005, 0.1), (0.3, 0.005, 0.1))]),
        # Half of j4's line runs along j3's: j4 is kept whole.
        "j3": _joint(a, beam, [((1.0, 0, 0.1), (1.4, 0, 0.1))]),
        "j4": _joint(b, beam, [((1.2, 0, 0.1), (1.6, 0, 0.1))]),
    }
    _, stats = _run(by_id)
    assert stats["duplicate_welds"] == []


def test_the_welds_of_one_joint_are_not_compared_with_each_other():
    beam = ada.Beam("beam", (-1, 0, 0), (1, 0, 0), SECTION)
    a = ada.Beam("a", (0, 0, 0.1), (0, 0, 1.1), SECTION)
    line = ((0, 0, 0.1), (0.3, 0, 0.1))
    conn = ada.Connection(name="c", spec_name="provider.spec")
    for k in range(2):
        conn.add_weld(ada.Weld(f"w{k}", p1=line[0], p2=line[1], xdir=(0, 0, 1), throat=0.005, members=(a, beam)))
    assert dedup_seams([("j1", conn)]) == []
    assert [w.name for w in conn.welds] == ["w0", "w1"]
