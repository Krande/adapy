"""The role-binding matcher -- pure, capability-injected, no registry reach-through.

Driven with hand-built ``ConnectionSpec``s rather than only the built-ins, so a spec property
(angle constraint, missing kind) is pinned independent of what ``builtin_specs.py`` happens to
declare today.
"""

from __future__ import annotations

import ada
from ada.api.connections.spec import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    RegisteredConnection,
)
from ada.clash.match import applicable_specs, bindings_for_spec


def _beam(name: str, direction, section: str = "IPE200") -> ada.Beam:
    return ada.Beam(name, (0, 0, 0), direction, section)


def _plate(name: str = "pl") -> ada.Plate:
    return ada.Plate(name, [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, orientation=ada.Placement())


_TWO_BEAM_SPEC = ConnectionSpec(
    name="test.two_beam",
    roles=(
        MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM),
        MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.BEAM),
    ),
)

_ANGLE_SPEC = ConnectionSpec(
    name="test.perpendicular_only",
    roles=(
        MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM),
        MemberCriteria(
            role=MemberRole.INCOMING,
            kind=MemberKind.BEAM,
            angle_to_role=MemberRole.LANDING,
            angle_range=AngleRange(min_deg=80.0, max_deg=100.0),
        ),
    ),
)


# ── LANDING/INCOMING side binding ────────────────────────────────────


def test_landing_binds_only_to_the_joints_main_mem():
    # JointBase.main_mem tells the matcher which side landed; a spec whose roles distinguish the
    # two sides must respect that, not guess or bind either way round.
    b1, b2 = _beam("b1", (1, 0, 0)), _beam("b2", (0, 1, 0))

    bound_on_b1 = bindings_for_spec(_TWO_BEAM_SPEC, [b1, b2], landing=b1)
    assert len(bound_on_b1) == 1
    assert bound_on_b1[0][MemberRole.LANDING] is b1
    assert bound_on_b1[0][MemberRole.INCOMING] is b2

    bound_on_b2 = bindings_for_spec(_TWO_BEAM_SPEC, [b1, b2], landing=b2)
    assert len(bound_on_b2) == 1
    assert bound_on_b2[0][MemberRole.LANDING] is b2
    assert bound_on_b2[0][MemberRole.INCOMING] is b1


def test_with_no_known_landing_both_bindings_are_offered():
    # A plate-to-plate contact (or a joint built without a landing determination) skips the side
    # test rather than guessing -- both ways round come back as two bindings.
    b1, b2 = _beam("b1", (1, 0, 0)), _beam("b2", (0, 1, 0))
    bindings = bindings_for_spec(_TWO_BEAM_SPEC, [b1, b2], landing=None)
    sides = {(b[MemberRole.LANDING].name, b[MemberRole.INCOMING].name) for b in bindings}
    assert sides == {("b1", "b2"), ("b2", "b1")}


# ── kind presence ─────────────────────────────────────────────────────


def test_a_spec_whose_kinds_are_not_all_present_cannot_bind():
    # test.two_beam wants two BEAMs; a beam + a plate can never satisfy it, whatever the angle.
    b1 = _beam("b1", (1, 0, 0))
    pl = _plate()
    assert bindings_for_spec(_TWO_BEAM_SPEC, [b1, pl], landing=b1) == []


# ── angle constraint ──────────────────────────────────────────────────


def test_angle_constrained_spec_binds_only_inside_its_range():
    perpendicular = (_beam("b1", (1, 0, 0)), _beam("b2", (0, 1, 0)))
    parallel = (_beam("b3", (1, 0, 0)), _beam("b4", (1, 0, 0)))

    assert bindings_for_spec(_ANGLE_SPEC, list(perpendicular), landing=perpendicular[0])
    assert bindings_for_spec(_ANGLE_SPEC, list(parallel), landing=parallel[0]) == []


# ── applicable_specs sorting ────────────────────────────────────────


def test_applicable_specs_sorts_by_priority_then_name():
    b1, b2 = _beam("b1", (1, 0, 0)), _beam("b2", (0, 1, 0))
    low_b = ConnectionSpec(name="b.low", roles=_TWO_BEAM_SPEC.roles, priority=1)
    low_a = ConnectionSpec(name="a.low", roles=_TWO_BEAM_SPEC.roles, priority=1)
    high = ConnectionSpec(name="z.high", roles=_TWO_BEAM_SPEC.roles, priority=5)
    registered = [RegisteredConnection(spec=s, fn=lambda **kw: None) for s in (low_b, high, low_a)]

    result = applicable_specs([b1, b2], registered=registered)

    # priority descending first (high before both low-priority specs), then name ascending
    # within a priority tier.
    assert [a.spec for a in result] == ["z.high", "a.low", "b.low"]


# ── capability_of is injected, never resolved from a registry ───────


def test_capability_of_fills_capability_and_the_matcher_never_reaches_a_registry():
    # capability_of is injected because the real answer comes from a live worker heartbeat,
    # which this module must not reach for -- pinning it with `registered=` explicit and a
    # `all_registered` that raises if called proves the matcher never falls back to it when a
    # capability_of/registered pair is already supplied.
    import ada.clash.match as match_mod

    def _forbidden_all_registered():
        raise AssertionError("applicable_specs must not call all_registered() when registered= is given")

    original = match_mod.all_registered
    match_mod.all_registered = _forbidden_all_registered
    try:
        b1, b2 = _beam("b1", (1, 0, 0)), _beam("b2", (0, 1, 0))
        reg = RegisteredConnection(spec=_TWO_BEAM_SPEC, fn=lambda **kw: None)
        result = applicable_specs(
            [b1, b2],
            landing=b1,
            capability_of=lambda r: "injected-capability-sentinel",
            registered=[reg],
        )
    finally:
        match_mod.all_registered = original

    assert len(result) == 1
    assert result[0].capability == "injected-capability-sentinel"


def test_capability_defaults_to_none_when_capability_of_is_not_given():
    b1, b2 = _beam("b1", (1, 0, 0)), _beam("b2", (0, 1, 0))
    reg = RegisteredConnection(spec=_TWO_BEAM_SPEC, fn=lambda **kw: None)
    result = applicable_specs([b1, b2], landing=b1, registered=[reg])
    assert result[0].capability is None
