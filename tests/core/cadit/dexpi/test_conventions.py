"""The import conventions have one home, and the two direction spellings translate both ways."""

import pytest

from ada.cadit.dexpi import equipment_defaults, nozzle_placers
from ada.cadit.dexpi.read import conventions


@pytest.mark.parametrize(
    "flow, expected",
    [
        (None, "INOUT"),
        ("", "INOUT"),
        ("in", "IN"),
        (" FlowIn ", "IN"),
        ("inlet", "IN"),
        ("Source", "IN"),
        ("out", "OUT"),
        ("FlowOut", "OUT"),
        ("outlet", "OUT"),
        ("target", "OUT"),
        ("bidirectional", "INOUT"),
    ],
)
def test_direction_for_accepts_every_spelling_the_documents_use(flow, expected):
    assert conventions.direction_for(flow) == expected
    # The placer module re-exports the same function; it is one normaliser, not two.
    assert nozzle_placers.direction_for is conventions.direction_for


@pytest.mark.parametrize("token", ["in", "out"])
def test_flow_token_is_the_inverse_of_direction_for(token):
    assert conventions.flow_token(conventions.direction_for(token)) == token


def test_flow_token_accepts_the_enum_its_value_and_nothing():
    from ada.api.systems.ports import PortDirection

    assert conventions.flow_token(PortDirection.IN) == "in"
    assert conventions.flow_token("OUT") == "out"
    assert conventions.flow_token(PortDirection.INOUT) is None
    assert conventions.flow_token(None) is None


def test_the_class_defaults_fall_back_to_the_convention_entry():
    entry = equipment_defaults.resolve_defaults("NotADexpiClassAtAll")
    assert entry["source"] == "fallback"
    assert entry["ifc_element_class"] in (
        conventions.FALLBACK_EQUIPMENT_ENTRY["ifc"],
        equipment_defaults.defaults()[conventions.ROOT_EQUIPMENT_CLASS]["ifc"],
    )


def test_synthesised_equipment_envelopes_are_three_positive_lengths():
    for bbox in (conventions.INLINE_BBOX, conventions.INSTRUMENT_BBOX):
        assert len(bbox) == 3 and all(v > 0 for v in bbox)
    assert conventions.SITE_INLET_FACE != conventions.SITE_OUTLET_FACE
    assert 0 < conventions.SITE_ELEVATION_FRACTION < 1
