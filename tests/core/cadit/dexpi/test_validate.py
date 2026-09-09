"""Structural validation and the two round-trip oracles, on synthetic documents.

Everything here is built in Python rather than read from a file: the readers and the example
fixtures land in later PRs, and these checks are about the neutral model, not about parsing.
"""

from __future__ import annotations

import pytest

from ada.cadit.dexpi import (
    DexpiAttribute,
    DexpiConnection,
    DexpiDocument,
    DexpiItem,
    DexpiNode,
    DexpiPlacement,
    ItemKind,
    canonicalize,
    classify,
    graph_signature,
    validate_document,
)


def _item(doc, item_id, class_name, parent=None, tag=None, nodes=0, node_prefix=None):
    """Add an item with ``nodes`` process nodes plus an anchor at ordinal 1."""
    item = DexpiItem(id=item_id, class_name=class_name, kind=classify(class_name))
    if tag is not None:
        item.attributes.append(DexpiAttribute(name="TagNameAssignmentClass", value=tag))

    prefix = node_prefix or item_id
    if nodes:
        item.nodes.append(DexpiNode(id=f"{prefix}-DefaultNode", ordinal=1, owner_id=item_id, is_anchor=True))
        for ordinal in range(2, nodes + 2):
            item.nodes.append(DexpiNode(id=f"{prefix}#node{ordinal}", ordinal=ordinal, owner_id=item_id))

    doc.add(item, parent)
    return item


def _document():
    """A tank and a pump joined by one segment through a ball valve."""
    doc = DexpiDocument()
    _item(doc, "M1", "PlantModel")

    _item(doc, "E1", "Tank", parent="M1", tag="T-100")
    _item(doc, "N1", "Nozzle", parent="E1", tag="N1", nodes=1)
    _item(doc, "E2", "CentrifugalPump", parent="M1", tag="P-100")
    _item(doc, "N2", "Nozzle", parent="E2", tag="S", nodes=1)

    _item(doc, "S1", "PipingNetworkSystem", parent="M1", tag="100-PW-01")
    _item(doc, "G1", "PipingNetworkSegment", parent="S1")
    _item(doc, "V1", "BallValve", parent="G1", tag="HV-100", nodes=2)

    doc.connections = [
        DexpiConnection(from_item="N1", from_node="N1#node2", to_item="V1", to_node="V1#node2", owner_id="G1"),
        DexpiConnection(from_item="V1", from_node="V1#node3", to_item="N2", to_node="N2#node2", owner_id="G1"),
    ]
    return doc


def test_a_well_formed_document_has_no_problems():
    assert validate_document(_document()) == []


def test_kinds_are_classified_from_the_supertype_graph():
    doc = _document()
    assert doc.items["E1"].kind is ItemKind.EQUIPMENT
    assert doc.items["N1"].kind is ItemKind.NOZZLE
    assert doc.items["V1"].kind is ItemKind.PIPING_COMPONENT
    assert doc.items["S1"].kind is ItemKind.PIPING_SYSTEM
    assert doc.items["G1"].kind is ItemKind.PIPING_SEGMENT
    assert doc.items["M1"].kind is ItemKind.MODEL
    assert classify("FlowInPipeOffPageConnector") is ItemKind.OFF_PAGE_CONNECTOR
    assert classify("SomeVendorWidget") is ItemKind.OTHER


def test_process_nodes_exclude_the_anchor():
    valve = _document().items["V1"]
    assert len(valve.nodes) == 3
    assert [node.ordinal for node in valve.process_nodes] == [2, 3]


def test_connection_to_an_unknown_item_is_reported():
    doc = _document()
    doc.connections[0].to_item = "V9"
    problems = validate_document(doc)
    assert any("unknown item 'V9'" in message for message in problems)


def test_connection_to_a_node_the_item_lacks_is_reported():
    doc = _document()
    doc.connections[0].to_node = "V1#node9"
    problems = validate_document(doc)
    assert any("does not have" in message and "V1#node9" in message for message in problems)


def test_connection_without_an_endpoint_is_reported():
    doc = _document()
    doc.connections[0].to_item = None
    assert any("has no to item" in message for message in validate_document(doc))


def test_node_ordinals_must_be_contiguous_and_one_based():
    doc = _document()
    doc.items["V1"].nodes[2].ordinal = 4
    problems = validate_document(doc)
    assert any("node ordinals are not 1..3" in message for message in problems)


def test_duplicate_node_ordinals_are_reported():
    doc = _document()
    doc.items["V1"].nodes[2].ordinal = 2
    assert any("duplicate node ordinals" in message for message in validate_document(doc))


def test_a_node_id_shared_by_two_items_is_reported():
    doc = _document()
    doc.items["N2"].nodes[1].id = "N1#node2"
    problems = validate_document(doc)
    assert any("is used by both" in message for message in problems)


def test_a_node_reporting_the_wrong_owner_is_reported():
    doc = _document()
    doc.items["N1"].nodes[1].owner_id = "E1"
    assert any("reports owner 'E1'" in message for message in validate_document(doc))


def test_a_child_the_parent_does_not_list_is_reported():
    doc = _document()
    doc.items["E1"].child_ids.remove("N1")
    problems = validate_document(doc)
    assert any("is not among its children" in message for message in problems)


def test_an_unknown_child_id_is_reported():
    doc = _document()
    doc.items["E1"].child_ids.append("N9")
    assert any("unknown child 'N9'" in message for message in validate_document(doc))


def test_a_composition_cycle_is_reported_once():
    doc = _document()
    doc.items["M1"].parent_id = "E1"
    doc.items["E1"].child_ids.append("M1")
    doc.root_ids.remove("M1")
    problems = validate_document(doc)
    assert len([message for message in problems if message.startswith("composition cycle")]) == 1


def test_an_orphan_that_is_not_a_root_is_reported():
    doc = _document()
    doc.items["X1"] = DexpiItem(id="X1", class_name="Tank", kind=ItemKind.EQUIPMENT)
    problems = validate_document(doc)
    assert any("is not listed as a root" in message for message in problems)


def test_an_unknown_class_is_reported():
    doc = _document()
    doc.items["E1"].class_name = "SomeVendorWidget"
    problems = validate_document(doc)
    assert any("unknown to the DEXPI class table" in message for message in problems)


def test_a_mislabelled_kind_is_reported():
    doc = _document()
    doc.items["E1"].kind = ItemKind.NOZZLE
    problems = validate_document(doc)
    assert any("is classified 'nozzle'" in message for message in problems)


# -- canonicalize -------------------------------------------------------------------------


def test_canonicalize_is_stable_under_attribute_order():
    left = _document()
    right = _document()
    extra = [
        DexpiAttribute(name="FluidCodeAssignmentClass", value="PW"),
        DexpiAttribute(name="EquipmentDescriptionAssignmentClass", value="feed tank"),
    ]
    left.items["E1"].attributes.extend(extra)
    right.items["E1"].attributes.extend(reversed(extra))
    assert canonicalize(left) == canonicalize(right)


def test_canonicalize_ignores_the_volatile_header_fields():
    left = _document()
    right = _document()
    left.header.export_date = "2026-09-08"
    left.header.export_time = "12:00:00"
    left.header.originating_system = "PID Kit"
    right.header.export_date = "1999-01-01"
    right.header.originating_system = "Some Other Tool"
    assert canonicalize(left) == canonicalize(right)

    right.header.project = "changed"
    assert canonicalize(left) != canonicalize(right)


def test_canonicalize_rounds_floats_to_six_significant_digits():
    left = _document()
    right = _document()
    left.items["E1"].placement = DexpiPlacement(location=(0.1 + 0.2, 1.0, 0.0))
    right.items["E1"].placement = DexpiPlacement(location=(0.3, 1.0, 0.0))
    assert canonicalize(left) == canonicalize(right)

    right.items["E1"].placement = DexpiPlacement(location=(0.3001, 1.0, 0.0))
    assert canonicalize(left) != canonicalize(right)


def test_canonicalize_notices_a_changed_value():
    left = _document()
    right = _document()
    right.items["E1"].attributes[0].value = "T-200"
    assert canonicalize(left) != canonicalize(right)


def test_canonicalize_is_stable_under_connection_order():
    left = _document()
    right = _document()
    right.connections.reverse()
    assert canonicalize(left) == canonicalize(right)


# -- graph_signature ----------------------------------------------------------------------


def _renamed(doc, suffix):
    """The same P&ID with every ID rewritten -- what a flavour conversion does."""
    out = DexpiDocument()
    remap = {item_id: f"{item_id}{suffix}" for item_id in doc.items}

    for item_id, item in doc.items.items():
        clone = DexpiItem(
            id=remap[item_id],
            class_name=item.class_name,
            kind=item.kind,
            attributes=list(item.attributes),
            nodes=[
                DexpiNode(
                    id=f"{node.id}{suffix}", ordinal=node.ordinal, owner_id=remap[item_id], is_anchor=node.is_anchor
                )
                for node in item.nodes
            ],
        )
        out.add(clone, remap[item.parent_id] if item.parent_id else None)

    out.connections = [
        DexpiConnection(
            from_item=remap[c.from_item],
            from_node=f"{c.from_node}{suffix}",
            to_item=remap[c.to_item],
            to_node=f"{c.to_node}{suffix}",
            owner_id=remap[c.owner_id] if c.owner_id else None,
        )
        for c in doc.connections
    ]
    return out


def test_graph_signature_is_id_independent():
    doc = _document()
    assert graph_signature(doc) == graph_signature(_renamed(doc, "-x"))
    # ... and canonicalize is not, because a write-then-read must preserve IDs.
    assert canonicalize(doc) != canonicalize(_renamed(doc, "-x"))


def test_graph_signature_paths_carry_the_composition_hierarchy():
    signature = graph_signature(_document())
    assert "PlantModel[]/Tank[T-100]/Nozzle[N1]" in signature["items"]
    # The nozzle's single process node is "#1" even though it sits at ordinal 2 behind the anchor:
    # a DEXPI 2.0 document has no anchor to sit behind, so counting it would put the two flavours
    # one apart on every nozzle in every document. See graph_signature's docstring.
    assert "PlantModel[]/Tank[T-100]/Nozzle[N1]#1" in signature["nodes"]
    assert "PlantModel[]/Tank[T-100]/Nozzle[N1]#2" not in signature["nodes"]


def test_graph_signature_notices_a_rewired_connection():
    doc = _document()
    other = _document()
    other.connections[0].to_node = "V1#node3"
    assert graph_signature(doc) != graph_signature(other)


def test_graph_signature_notices_a_missing_item():
    doc = _document()
    other = _document()
    other.items.pop("V1")
    other.items["G1"].child_ids.remove("V1")
    assert graph_signature(doc) != graph_signature(other)


@pytest.mark.parametrize("value, expected", [(1 / 3, "0.333333"), (1000000.0, "1e+06"), (None, None)])
def test_format_float(value, expected):
    from ada.cadit.dexpi.canonical import format_float

    assert format_float(value) == expected
