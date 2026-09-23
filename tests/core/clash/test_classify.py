"""The joint type key -- built ONLY from core vocabulary, never a profile designation.

``classify.py``'s own docstring names the closed list a key may be built from: member count,
``MemberKind``, section FAMILY, ``member_type``, one bucketed angle. These tests pin that list by
construction, not by reading the docstring back.
"""

from __future__ import annotations

import ada
from ada.clash.classify import (
    angle_bucket,
    describe_member,
    type_key_for,
    type_label_for,
)


def _beam(name: str, direction, section: str) -> ada.Beam:
    return ada.Beam(name, (0, 0, 0), direction, section)


# ── section FAMILY, not profile designation ──────────────────────────


def test_same_family_different_designation_shares_one_key():
    # IPE200 and IPE300 are both `I`-family: the key must not separate them, or a user deciding
    # "detail every girder crossing like this" would face two rows for one decision.
    ipe200 = describe_member(_beam("b1", (1, 0, 0), "IPE200"))
    ipe300 = describe_member(_beam("b2", (0, 1, 0), "IPE300"))
    same_a = describe_member(_beam("b1b", (0, 1, 0), "IPE200"))
    same_b = describe_member(_beam("b2b", (1, 0, 0), "IPE300"))

    mixed_key = type_key_for([ipe200, ipe300], 90.0)
    matching_key = type_key_for([same_a, same_b], 90.0)
    assert mixed_key == matching_key
    assert ipe200.section == ipe300.section == "I"


def test_different_families_produce_different_keys():
    # HP180x10 (ANGULAR/HP family) meeting an I-family girder is a different decision than two
    # I-family members meeting -- the key must say so.
    i_member = describe_member(_beam("b1", (1, 0, 0), "IPE200"))
    hp_member = describe_member(_beam("b2", (0, 1, 0), "HP180x10"))
    two_i = describe_member(_beam("b3", (0, 1, 0), "IPE300"))

    assert i_member.section == "I"
    assert hp_member.section == "HP"
    assert type_key_for([i_member, hp_member], 90.0) != type_key_for([i_member, two_i], 90.0)


# ── member order ──────────────────────────────────────────────────────


def test_member_order_does_not_change_the_key():
    # The clash walk may see the two members of one contact in either order; a key that were
    # sensitive to that order would split one joint's group by accident of iteration.
    a = describe_member(_beam("b1", (1, 0, 0), "IPE200"))
    b = describe_member(_beam("b2", (0, 1, 0), "HP180x10"))
    assert type_key_for([a, b], 90.0) == type_key_for([b, a], 90.0)


# ── the angle bucket ──────────────────────────────────────────────────


def test_angle_bucket_of_none_is_unknown_never_parallel():
    # A plate-to-plate contact has no pair of axes to measure between; `None` must read as
    # "unknown", a real value -- not fall through to "parallel", which would be an invented fact.
    assert angle_bucket(None) == "unknown"


def test_angle_bucket_covers_the_declared_ranges():
    assert angle_bucket(0.0) == "parallel"
    assert angle_bucket(90.0) == "perpendicular"
    assert angle_bucket(45.0) == "skew"
    assert angle_bucket(179.0) == "parallel"


def test_type_key_for_never_silently_reports_parallel_for_an_unmeasurable_angle():
    a = describe_member(_beam("b1", (1, 0, 0), "IPE200"))
    b = describe_member(_beam("b2", (0, 1, 0), "IPE200"))
    key = type_key_for([a, b], None)
    assert key.endswith("|unknown")


# ── the human label ───────────────────────────────────────────────────


def test_type_label_matches_the_plans_shape():
    # The docstring's own example shape: "<n> × <kinds> · <sections> · <types> · <bucket>",
    # sections slash-joined, types arrow-joined -- pinned with a hand-built pair so the assertion
    # does not depend on the demo's own layout.
    girder = describe_member(_beam("b1", (0, 1, 0), "IPE200"))  # family I
    column = describe_member(_beam("b2", (0, 0, 1), "HP180x10"))  # family HP
    label = type_label_for([girder, column], 90.0)
    assert label == "2 × BEAM · HP/I · Column→Girder · perpendicular"


def test_type_label_drops_the_bucket_when_unknown():
    a = describe_member(_beam("b1", (1, 0, 0), "IPE200"))
    b = describe_member(_beam("b2", (0, 1, 0), "IPE200"))
    label = type_label_for([a, b], None)
    assert "unknown" not in label
