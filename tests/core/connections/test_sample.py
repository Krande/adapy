import math

import pytest

from ada import Beam, Plate
from ada.api.connections import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    build_sample,
)
from ada.api.connections.sample import default_inputs


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


def _beam_on_plate_spec() -> ConnectionSpec:
    return ConnectionSpec(
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


def _plate_on_box_spec() -> ConnectionSpec:
    return ConnectionSpec(
        name="test.plate_on_box",
        roles=(
            MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.PLATE),
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=frozenset({"BOX"})),
        ),
    )


def test_plate_anchor_lies_below_the_origin():
    """A plate anchor is flat in XY with its top face at z=0; the beam rises from the origin."""
    inputs = {
        "incoming": {"section": "BOX200x200x10x10", "angle_deg": 90.0},
        "landing": {"thickness": 0.025, "width": 0.5, "height": 0.4},
    }
    members = build_sample(_beam_on_plate_spec(), inputs)

    plate = members[MemberRole.LANDING]
    assert isinstance(plate, Plate)
    assert plate.t == pytest.approx(0.025)
    bb = plate.bbox()
    assert bb.p2[2] == pytest.approx(0.0, abs=1e-9)
    assert bb.p1[2] == pytest.approx(-0.025)
    assert bb.p2[0] - bb.p1[0] == pytest.approx(0.5)

    beam = members[MemberRole.INCOMING]
    assert tuple(beam.n1.p) == pytest.approx((0.0, 0.0, 0.0))
    assert tuple(beam.n2.p) == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)


def test_plate_stands_on_the_anchor_beam_top_face():
    inputs = {
        "incoming": {"thickness": 0.02, "width": 0.4, "height": 0.3},
        "landing": {"section": "BOX300x300x12x12"},
    }
    members = build_sample(_plate_on_box_spec(), inputs)

    plate = members[MemberRole.INCOMING]
    bb = plate.bbox()
    assert bb.p1[2] == pytest.approx(0.15)  # half the box depth
    assert bb.p2[2] == pytest.approx(0.45)
    # 90° by default: across the beam, i.e. spanning Y, thickness along X
    assert bb.p2[1] - bb.p1[1] == pytest.approx(0.4)
    assert bb.p2[0] - bb.p1[0] == pytest.approx(0.02)


def test_plate_inputs_are_validated():
    with pytest.raises(ValueError, match="missing 'thickness'"):
        build_sample(_plate_on_box_spec(), {"incoming": {}, "landing": {"section": "BOX300x300x12x12"}})
    with pytest.raises(ValueError, match="'thickness' must be positive"):
        build_sample(_plate_on_box_spec(), {"incoming": {"thickness": 0}, "landing": {"section": "BOX300x300x12x12"}})


def test_default_inputs_fill_what_the_spec_leaves_out():
    spec = _beam_on_plate_spec()
    defaults = default_inputs(spec)
    assert defaults == {
        "incoming": {"section": "BOX300x300x12x12", "angle_deg": 90.0},
        "landing": {"thickness": 0.02, "width": 0.4, "height": 0.3},
    }
    # ... and they build.
    build_sample(spec, defaults)


def test_default_inputs_keep_declared_defaults():
    spec = ConnectionSpec(
        name="test.declared",
        roles=(
            MemberCriteria(
                role=MemberRole.INCOMING,
                kind=MemberKind.BEAM,
                angle_to_role=MemberRole.LANDING,
                angle_range=AngleRange(20.0, 60.0),
            ),
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM),
        ),
        defaults={"landing": {"section": "HEB300"}},
    )
    assert default_inputs(spec) == {
        "incoming": {"section": "IPE300", "angle_deg": 40.0},  # 90° is out of range: the middle
        "landing": {"section": "HEB300"},
    }


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
