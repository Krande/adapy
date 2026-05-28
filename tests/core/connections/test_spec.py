import pytest

from ada import Beam, Plate
from ada.api.connections.spec import (
    AngleRange,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    _clear_registry,
    all_registered,
    get_registered,
    get_spec,
    list_specs,
    register_connection,
    spec_to_form_schema,
)


@pytest.fixture(autouse=True)
def isolate_registry():
    _clear_registry()
    yield
    _clear_registry()


def _spec(name: str = "test.box_to_box", priority: int = 0, tags=frozenset()):
    return ConnectionSpec(
        name=name,
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
        tags=tags,
        priority=priority,
    )


def test_register_and_lookup_round_trip():
    spec = _spec()

    @register_connection(spec)
    def builder(**kwargs):
        return "built"

    assert list_specs() == [spec]
    assert get_spec(spec.name) is spec
    assert get_registered(spec.name).fn is builder
    assert builder() == "built"


def test_duplicate_registration_errors():
    spec = _spec()

    @register_connection(spec)
    def builder_a(**kwargs):
        pass

    with pytest.raises(ValueError, match="already registered"):

        @register_connection(spec)
        def builder_b(**kwargs):
            pass


def test_all_registered_returns_full_records():
    spec_a = _spec("test.a", priority=1)
    spec_b = _spec("test.b", priority=5)

    @register_connection(spec_a)
    def fa(**kwargs):
        pass

    @register_connection(spec_b)
    def fb(**kwargs):
        pass

    names = [r.spec.name for r in all_registered()]
    assert set(names) == {"test.a", "test.b"}


def test_angle_range_wrap_at_boundaries():
    r = AngleRange(-10.0, 10.0)
    assert r.contains(0.0)
    assert r.contains(-10.0)
    assert r.contains(10.0)
    assert not r.contains(11.0)
    assert not r.contains(-11.0)
    # 350 normalizes to -10 → on the lower bound (inclusive)
    assert r.contains(350.0)
    # 170 normalizes to 170 → outside
    assert not r.contains(170.0)


def test_angle_range_wide_positive():
    r = AngleRange(20.0, 165.0)
    assert r.contains(90.0)
    assert r.contains(20.0)
    assert r.contains(165.0)
    assert not r.contains(19.0)
    assert not r.contains(166.0)
    # -190 normalizes to 170 → outside
    assert not r.contains(-190.0)


def test_member_criteria_matches_beam_by_section():
    # SHS strings normalize to BOX internally in adapy; specs that want
    # to catch SHS members must list "BOX" in section_in.
    bm_box = Beam("b1", (0, 0, 0), (1, 0, 0), "SHS200x10")
    bm_ipe = Beam("b2", (0, 0, 0), (1, 0, 0), "IPE300")

    crit = MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.BEAM, section_in=frozenset({"BOX"}))
    assert crit.matches_single(bm_box) is True
    assert crit.matches_single(bm_ipe) is False


def test_member_criteria_matches_plate_pseudo_section():
    pl = Plate("p1", [(0, 0), (1, 0), (1, 1), (0, 1)], t=0.01)

    crit_plate = MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.PLATE, section_in=frozenset({"PLATE"}))
    crit_box = MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.PLATE, section_in=frozenset({"BOX"}))

    assert crit_plate.matches_single(pl) is True
    assert crit_box.matches_single(pl) is False


def test_member_criteria_kind_filter():
    bm = Beam("b1", (0, 0, 0), (1, 0, 0), "IPE300")
    crit_plate_only = MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.PLATE)
    assert crit_plate_only.matches_single(bm) is False


def test_member_kind_from_ada_type():
    bm = Beam("b1", (0, 0, 0), (1, 0, 0), "IPE300")
    pl = Plate("p1", [(0, 0), (1, 0), (1, 1), (0, 1)], t=0.01)
    assert MemberKind.from_ada_type(bm) is MemberKind.BEAM
    assert MemberKind.from_ada_type(pl) is MemberKind.PLATE


def test_spec_to_form_schema_shape():
    spec = _spec(tags=frozenset({"box", "beam-beam"}))
    schema = spec_to_form_schema(spec)

    assert schema["name"] == spec.name
    assert schema["priority"] == 0
    assert schema["tags"] == ["beam-beam", "box"]
    assert schema["defaults"] is None
    assert len(schema["roles"]) == 2

    incoming = schema["roles"][0]
    assert incoming["role"] == "incoming"
    assert incoming["kind"] == "BEAM"
    assert incoming["section_in"] == ["BOX"]
    assert incoming["angle_to_role"] == "landing"
    assert incoming["angle_range"] == {"min_deg": 20.0, "max_deg": 165.0}
    assert incoming["has_predicate"] is False

    landing = schema["roles"][1]
    assert landing["angle_to_role"] is None
    assert landing["angle_range"] is None


def test_spec_to_form_schema_handles_none_fields():
    spec = ConnectionSpec(
        name="bare",
        roles=(MemberCriteria(role=MemberRole.INCOMING),),
    )
    schema = spec_to_form_schema(spec)
    role = schema["roles"][0]
    assert role["kind"] is None
    assert role["section_in"] is None
    assert role["angle_to_role"] is None
    assert role["angle_range"] is None
    assert role["has_predicate"] is False


def test_predicate_flag_in_schema():
    spec = ConnectionSpec(
        name="with-pred",
        roles=(MemberCriteria(role=MemberRole.INCOMING, predicate=lambda binding: True),),
    )
    schema = spec_to_form_schema(spec)
    assert schema["roles"][0]["has_predicate"] is True


def test_defaults_round_trip_to_form_schema():
    defaults = {
        "incoming": {"section": "BOX300x300x12x12", "angle_deg": 90.0},
        "landing": {"section": "BOX300x300x12x12"},
    }
    spec = ConnectionSpec(
        name="with-defaults",
        roles=(
            MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.BEAM),
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM),
        ),
        defaults=defaults,
    )
    assert spec.defaults == defaults
    schema = spec_to_form_schema(spec)
    assert schema["defaults"] == defaults
