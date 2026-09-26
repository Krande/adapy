"""``attributes.json`` -- the document, and the three reading rules it inherits from the spine."""

from __future__ import annotations

import json

import pytest

from ada.assets.attributes import (
    ATTRIBUTES_SCHEMA,
    AttributesError,
    NodeAttributes,
    build_attributes,
    parse_attributes,
)


def _doc(**nodes):
    return build_attributes(
        provider="ifc", collection="plant-a", root="r", produced_at="2026-01-01T00:00:00Z", nodes=nodes
    )


def test_a_document_round_trips_through_its_own_reader():
    doc = _doc(
        beam=NodeAttributes(
            kind="IfcBeam",
            own={"Name": "a1-bm0", "Material": "IPE200"},
            groups={"Pset_BeamCommon": {"Reference": "IPE200", "IsExternal": False}},
            quantities={"Qto_BeamBaseQuantities": {"Length": 3.0}},
        )
    )
    back = parse_attributes(doc.to_json())
    node = back.node("beam")
    assert node.kind == "IfcBeam"
    assert node.own == {"Name": "a1-bm0", "Material": "IPE200"}
    assert node.groups["Pset_BeamCommon"]["IsExternal"] is False
    assert node.quantities["Qto_BeamBaseQuantities"]["Length"] == 3.0


def test_every_input_shape_a_caller_has_is_accepted():
    """Bytes off a blob fetch, decoded text, and an already-parsed mapping all read the same."""
    raw = _doc(beam=NodeAttributes(kind="IfcBeam", own={"Name": "b"})).to_json()
    assert parse_attributes(raw).node("beam").own == {"Name": "b"}
    assert parse_attributes(raw.decode()).node("beam").own == {"Name": "b"}
    assert parse_attributes(json.loads(raw)).node("beam").own == {"Name": "b"}


def test_an_unknown_schema_is_refused_rather_than_read_for_what_it_recognises():
    """Rule 1. A future document read for its familiar keys is subtly wrong, not obviously broken."""
    doc = json.loads(_doc(beam=NodeAttributes(own={"Name": "b"})).to_json())
    doc["schema"] = "ada.assets/attributes@2"
    with pytest.raises(AttributesError, match="schema"):
        parse_attributes(json.dumps(doc))


def test_an_absent_node_is_an_answer_and_not_an_error():
    """Rule 3. A subject covers nodes its provider had nothing to say about."""
    assert parse_attributes(_doc(beam=NodeAttributes(own={"Name": "b"})).to_json()).node("nope") is None


def test_a_node_with_nothing_to_say_is_never_written():
    """An empty entry and an absent one read identically, so the empty one is pure bytes.

    On a spatial-heavy subtree that is most of the nodes, which is the difference between a
    document that is proportional to the products and one proportional to the tree.
    """
    doc = _doc(
        beam=NodeAttributes(kind="IfcBeam", own={"Name": "b"}),
        empty=NodeAttributes(),
        kind_only=NodeAttributes(kind="IfcBuildingStorey"),
    )
    assert sorted(doc.nodes) == ["beam"]
    assert b"kind_only" not in doc.to_json()


def test_a_malformed_group_is_named_by_where_it_is():
    """A document that cannot be read says which node and which group, or it cannot be fixed."""
    doc = json.loads(_doc(beam=NodeAttributes(own={"Name": "b"})).to_json())
    doc["nodes"]["beam"]["groups"] = {"Pset_X": ["not", "an", "object"]}
    with pytest.raises(AttributesError, match=r"beam.*Pset_X"):
        parse_attributes(json.dumps(doc))


def test_the_schema_is_declared_on_every_document_written():
    assert json.loads(_doc(beam=NodeAttributes(own={"N": 1})).to_json())["schema"] == ATTRIBUTES_SCHEMA
