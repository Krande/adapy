"""``detail_pairs`` -- which two members a spec is about, at a contact where more than two meet.

A builder takes a (landing, incoming) pair; a joint is a contact NODE. Requiring the joint itself
to have exactly two members refused every column head, which is the first thing a "detail every
joint that has a generator" run walks into.
"""

import pytest

import ada
from ada.api.connections.spec import (
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
)
from ada.clash.identify import _Found
from ada.clash.match import detail_pairs


def _girder_to_column_spec(column, girder) -> ConnectionSpec:
    """Roles told apart by SECTION FAMILY -- the only member term `MemberCriteria` compares on."""
    return ConnectionSpec(
        name="test.girder_to_column",
        roles=[
            MemberCriteria(
                role=MemberRole.LANDING,
                kind=MemberKind.BEAM,
                section_in=frozenset({column.section.type.value.upper()}),
            ),
            MemberCriteria(
                role=MemberRole.INCOMING,
                kind=MemberKind.BEAM,
                section_in=frozenset({girder.section.type.value.upper()}),
            ),
        ],
    )


def _column_with(n_girders: int):
    column = ada.Beam("col", (0, 0, 0), (0, 0, 3), "TUB375x35")
    girders = [
        ada.Beam(f"g{i}", (0, 0, 3), end, "IPE200")
        for i, end in enumerate([(5, 0, 3), (0, 5, 3), (-5, 0, 3), (0, -5, 3)][:n_girders])
    ]
    (ada.Part("p") / [column, *girders])
    return column, girders


def test_a_two_member_joint_gives_one_pair():
    column, girders = _column_with(1)
    found = _Found(members=[column, girders[0]], centre=(0, 0, 3), landing=column)
    assert detail_pairs(_girder_to_column_spec(column, girders[0]), found) == [(column, girders[0])]


def test_a_column_head_with_three_girders_gives_three_pairs():
    column, girders = _column_with(3)
    found = _Found(members=[column, *girders], centre=(0, 0, 3), landing=column)
    pairs = detail_pairs(_girder_to_column_spec(column, girders[0]), found)
    assert len(pairs) == 3
    assert {id(landing) for landing, _ in pairs} == {id(column)}
    assert {incoming.name for _, incoming in pairs} == {g.name for g in girders}


def test_a_spec_that_binds_nothing_here_is_an_error_naming_the_joint():
    column, girders = _column_with(3)
    plate_spec = ConnectionSpec(
        name="test.plate_only",
        roles=[
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.PLATE),
            MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.PLATE),
        ],
    )
    found = _Found(members=[column, *girders], centre=(0, 0, 3), landing=column)
    with pytest.raises(ValueError, match="binds no pair of members"):
        detail_pairs(plate_spec, found)


def test_a_roleless_spec_still_details_a_two_member_contact():
    column, girders = _column_with(1)
    found = _Found(members=[column, girders[0]], centre=(0, 0, 3), landing=None)
    bare = ConnectionSpec(name="test.bare", roles=[])
    assert detail_pairs(bare, found) == [(column, girders[0])]


def test_a_spec_may_bind_a_sub_connection_at_a_larger_contact():
    """The joint's landing member is about the WHOLE contact, so it must not force the spec's
    LANDING role when the spec is about only part of it.

    This is the bug behind "Not all Pre-requisite member types ['Girder', 'Girder'] are found":
    at a column head the joint's landing member is the COLUMN, the girder-gusset spec's LANDING
    role was pinned to it, and the spec either bound nothing or bound the column into a builder
    that wanted two girders.
    """
    column = ada.Beam("col", (0, 0, 0), (0, 0, 3), "IPE300")
    g1 = ada.Beam("g1", (0, 0, 3), (5, 0, 3), "IPE200")
    g2 = ada.Beam("g2", (0, 0, 3), (0, 5, 3), "IPE200")
    (ada.Part("p") / [column, g1, g2])

    girder_to_girder = ConnectionSpec(
        name="test.girder_to_girder",
        roles=[
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, member_types=frozenset({"Girder"})),
            MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.BEAM, member_types=frozenset({"Girder"})),
        ],
    )
    found = _Found(members=[column, g1, g2], centre=(0, 0, 3), landing=column)

    pairs = detail_pairs(girder_to_girder, found)
    assert len(pairs) == 1
    assert {m.name for m in pairs[0]} == {"g1", "g2"}


def test_the_landing_member_is_still_honoured_when_the_spec_covers_the_whole_contact():
    column = ada.Beam("col", (0, 0, 0), (0, 0, 3), "IPE300")
    girder = ada.Beam("g1", (0, 0, 3), (5, 0, 3), "IPE200")
    (ada.Part("p") / [column, girder])
    spec = ConnectionSpec(
        name="test.any_two_beams",
        roles=[
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM),
            MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.BEAM),
        ],
    )
    found = _Found(members=[column, girder], centre=(0, 0, 3), landing=column)
    # Two members, two roles: the contact's own landing member decides the side, so there is ONE
    # binding, not one per ordering.
    assert detail_pairs(spec, found) == [(column, girder)]
