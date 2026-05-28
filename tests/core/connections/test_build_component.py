from types import SimpleNamespace

import pytest

from ada import Beam, Plate, PrimBox, Weld
from ada.api.connections import (
    AngleRange,
    Connection,
    ConnectionSpec,
    MemberCriteria,
    MemberKind,
    MemberRole,
    build_component,
    register_connection,
)
from ada.api.connections.spec import _clear_registry


@pytest.fixture(autouse=True)
def isolate_registry():
    _clear_registry()
    yield
    _clear_registry()


def _box_to_box_spec() -> ConnectionSpec:
    return ConnectionSpec(
        name="test.box_to_box",
        roles=(
            MemberCriteria(
                role=MemberRole.INCOMING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX", "SHS"}),
                angle_to_role=MemberRole.LANDING,
                angle_range=AngleRange(20.0, 165.0),
            ),
            MemberCriteria(
                role=MemberRole.LANDING,
                kind=MemberKind.BEAM,
                section_in=frozenset({"BOX", "SHS"}),
            ),
        ),
    )


def _fillet(name, p1, p2, members, xdir=(0, 1, 0)):
    return Weld(
        name,
        p1=p1,
        p2=p2,
        weld_type="FILLET",
        members=members,
        profile=[(0, 0), (-0.005, 0), (0, 0.005)],
        xdir=xdir,
    )


def _basic_inputs():
    return {
        "incoming": {"section": "SHS200x10", "angle_deg": 90.0},
        "landing": {"section": "BOX200x200x10x10"},
    }


def test_build_component_populates_connection():
    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming: Beam, landing: Beam, **_):
        stiffener = Plate("stiff", [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)], t=0.01)
        weld = _fillet("w1", incoming.n1.p, incoming.n2.p, [incoming, landing])
        return SimpleNamespace(welds=[weld], stiffeners=[stiffener], negative_booleans=[])

    conn = build_component(spec.name, _basic_inputs())

    assert isinstance(conn, Connection)
    assert conn.spec_name == spec.name
    assert conn.spec_inputs == _basic_inputs()
    assert len(conn.beams) == 2
    assert {b.name for b in conn.beams} == {"sample_incoming", "sample_landing"}
    assert len(conn.plates) == 1
    assert conn.plates[0].name == "stiff"
    assert len(conn.welds) == 1
    assert conn.welds[0].name == "w1"


def test_build_component_handler_returns_none():
    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming, landing, **_):
        return None

    conn = build_component(spec.name, _basic_inputs())
    assert len(conn.beams) == 2
    assert len(conn.welds) == 0
    assert len(conn.plates) == 0


def test_build_component_handler_attaches_boolean_in_place():
    """Booleans attached via beam.add_boolean inside the handler survive the wrap."""
    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming: Beam, landing: Beam, **_):
        cut = PrimBox("notch", (-0.05, -0.05, -0.05), (0.05, 0.05, 0.05))
        incoming.add_boolean(cut)
        return None

    conn = build_component(spec.name, _basic_inputs())
    incoming_beam = next(b for b in conn.beams if b.name == "sample_incoming")
    assert len(incoming_beam.booleans) == 1
    assert incoming_beam.booleans[0].primitive.name == "notch"


def test_build_component_custom_name():
    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming, landing, **_):
        return None

    conn = build_component(spec.name, _basic_inputs(), name="my_corner_joint")
    assert conn.name == "my_corner_joint"


def test_build_component_unknown_spec_raises():
    with pytest.raises(KeyError, match="no registered connection spec"):
        build_component("nope.does_not_exist", {})


def test_build_component_propagates_validation_errors():
    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming, landing, **_):
        return None

    bad_inputs = _basic_inputs()
    bad_inputs["incoming"]["angle_deg"] = 5.0  # below min=20
    with pytest.raises(ValueError, match="outside angle_range"):
        build_component(spec.name, bad_inputs)


def test_build_component_welds_visible_via_graph():
    """The Stage 2c weld graph should work on the built Connection."""
    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming: Beam, landing: Beam, **_):
        weld = _fillet("w1", incoming.n1.p, incoming.n2.p, [incoming, landing])
        return SimpleNamespace(welds=[weld], stiffeners=[])

    conn = build_component(spec.name, _basic_inputs())
    incoming_beam = next(b for b in conn.beams if b.name == "sample_incoming")
    landing_beam = next(b for b in conn.beams if b.name == "sample_landing")

    assert [w.name for w in incoming_beam.welds] == ["w1"]
    partners = incoming_beam.connected_members()
    assert [p.name for p in partners] == [landing_beam.name]


def test_build_component_inside_assembly():
    """Built Connection should slot into a parent Assembly cleanly."""
    from ada import Assembly

    spec = _box_to_box_spec()

    @register_connection(spec)
    def handler(*, incoming, landing, **_):
        return None

    conn = build_component(spec.name, _basic_inputs())
    a = Assembly("root") / conn
    assert conn.name in a.parts
    assert a.parts[conn.name].spec_name == spec.name
