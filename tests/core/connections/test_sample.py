import math

import pytest

from ada import Beam
from ada.api.connections import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    build_sample,
)


def _box_to_box_spec() -> ConnectionSpec:
    return ConnectionSpec(
        name="test.box_to_box",
        roles=(
            MemberCriteria(
                role=MemberRole.INCOMING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX", "SHS", "RHS"}),
                angle_to_role=MemberRole.LANDING,
                angle_range=AngleRange(20.0, 165.0),
            ),
            MemberCriteria(
                role=MemberRole.LANDING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX", "SHS", "RHS"}),
            ),
        ),
    )


def _inputs(angle_deg=90.0, incoming_section="SHS200x10", landing_section="BOX200x200x10x10"):
    return {
        "incoming": {"section": incoming_section, "angle_deg": angle_deg},
        "landing": {"section": landing_section},
    }


def test_box_to_box_at_90deg():
    members = build_sample(_box_to_box_spec(), _inputs(90.0))

    assert set(members) == {MemberRole.INCOMING, MemberRole.LANDING}
    assert all(isinstance(m, Beam) for m in members.values())

    # The landing (anchor) member runs through the joint point along ±X
    # so it presents a continuous side surface for the incoming to butt
    # against (see build_sample / commit "landing extends ±X").
    landing = members[MemberRole.LANDING]
    assert pytest.approx(tuple(landing.n1.p)) == (-1.0, 0.0, 0.0)
    assert pytest.approx(tuple(landing.n2.p)) == (1.0, 0.0, 0.0)

    incoming = members[MemberRole.INCOMING]
    assert tuple(incoming.n1.p) == (0.0, 0.0, 0.0)
    assert pytest.approx(incoming.n2.p[0], abs=1e-9) == 0.0
    assert pytest.approx(incoming.n2.p[1]) == 1.0
    assert pytest.approx(incoming.n2.p[2]) == 0.0


def test_box_to_box_at_45deg():
    members = build_sample(_box_to_box_spec(), _inputs(45.0))
    incoming = members[MemberRole.INCOMING]
    expected = math.sqrt(2) / 2
    assert pytest.approx(incoming.n2.p[0]) == expected
    assert pytest.approx(incoming.n2.p[1]) == expected


def test_sections_assigned():
    members = build_sample(
        _box_to_box_spec(),
        _inputs(90.0, incoming_section="SHS200x10", landing_section="BOX300x300x12x12"),
    )
    # SHS200x10 normalizes internally to a BOX section
    assert members[MemberRole.INCOMING].section.type.value.upper() == "BOX"
    assert members[MemberRole.LANDING].section.type.value.upper() == "BOX"


def test_missing_role_inputs_raises():
    spec = _box_to_box_spec()
    bad = {"incoming": {"section": "SHS200x10", "angle_deg": 90.0}}  # landing missing
    with pytest.raises(ValueError, match="role 'landing'.*missing inputs entry"):
        build_sample(spec, bad)


def test_missing_section_raises():
    spec = _box_to_box_spec()
    bad = _inputs(90.0)
    del bad["landing"]["section"]
    with pytest.raises(ValueError, match="role 'landing'.*missing 'section'"):
        build_sample(spec, bad)


def test_section_not_in_section_in_raises():
    spec = _box_to_box_spec()
    bad = _inputs(90.0, landing_section="IPE300")
    with pytest.raises(ValueError, match="role 'landing'.*does not match section_in"):
        build_sample(spec, bad)


def test_section_validation_uses_resolved_type():
    """SHS sections normalize to BOX internally — matches_single sees BOX even
    when the user typed SHS, and a spec listing only {"BOX"} still accepts
    SHS inputs."""
    spec = ConnectionSpec(
        name="test.box_only",
        roles=(
            MemberCriteria(
                role=MemberRole.INCOMING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX"}),
                angle_to_role=MemberRole.LANDING,
                angle_range=AngleRange(20.0, 165.0),
            ),
            MemberCriteria(
                role=MemberRole.LANDING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX"}),
            ),
        ),
    )
    inputs = {
        "incoming": {"section": "SHS200x10", "angle_deg": 90.0},
        "landing": {"section": "BOX300x300x12x12"},
    }
    members = build_sample(spec, inputs)
    assert members[MemberRole.INCOMING].section.type.value.upper() == "BOX"


def test_missing_angle_raises():
    spec = _box_to_box_spec()
    bad = _inputs(90.0)
    del bad["incoming"]["angle_deg"]
    with pytest.raises(ValueError, match="role 'incoming'.*missing 'angle_deg'"):
        build_sample(spec, bad)


def test_angle_out_of_range_raises():
    spec = _box_to_box_spec()
    with pytest.raises(ValueError, match="outside angle_range"):
        build_sample(spec, _inputs(10.0))  # below min=20

    with pytest.raises(ValueError, match="outside angle_range"):
        build_sample(spec, _inputs(200.0))  # above max=165


def test_section_type_validation_rejects_non_string():
    spec = _box_to_box_spec()
    bad = _inputs(90.0)
    bad["incoming"]["section"] = 123
    with pytest.raises(ValueError, match="must be a string"):
        build_sample(spec, bad)


def test_angle_validation_rejects_non_numeric():
    spec = _box_to_box_spec()
    bad = _inputs(90.0)
    bad["incoming"]["angle_deg"] = "ninety"
    with pytest.raises(ValueError, match="must be numeric"):
        build_sample(spec, bad)


def test_plate_kind_not_implemented():
    spec = ConnectionSpec(
        name="test.box_to_plate",
        roles=(
            MemberCriteria(
                role=MemberRole.INCOMING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX"}),
                angle_to_role=MemberRole.LANDING,
                angle_range=AngleRange(20.0, 165.0),
            ),
            MemberCriteria(
                role=MemberRole.LANDING,
                kind=MemberKind.PLATE,
                section_in=frozenset({"PLATE"}),
            ),
        ),
    )
    inputs = {
        "incoming": {"section": "BOX200x200x10x10", "angle_deg": 90.0},
        "landing": {"section": "PLATE"},
    }
    with pytest.raises(NotImplementedError, match="plate sample synthesis"):
        build_sample(spec, inputs)


def test_anchor_role_runs_along_x():
    """When no angle_to_role is set on any criteria, the first role becomes anchor."""
    spec = ConnectionSpec(
        name="test.no_angle",
        roles=(
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=frozenset({"BOX"})),
            MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.BEAM, section_in=frozenset({"BOX"})),
        ),
    )
    inputs = {
        "landing": {"section": "BOX200x200x10x10"},
        "incoming": {"section": "BOX200x200x10x10"},
    }
    members = build_sample(spec, inputs)
    # Both members run along +X (degenerate placeholder, but valid)
    assert pytest.approx(members[MemberRole.LANDING].n2.p[0]) == 1.0
    assert pytest.approx(members[MemberRole.INCOMING].n2.p[0]) == 1.0
