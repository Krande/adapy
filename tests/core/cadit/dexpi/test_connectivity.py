"""``ConnectionIndex`` answers exactly what the walks it replaced answered."""

from ada.cadit.dexpi.equipment_list import connection_flow
from ada.cadit.dexpi.model import DexpiConnection, DexpiDocument, DexpiItem, ItemKind
from ada.cadit.dexpi.read.connectivity import ConnectionIndex


def _doc() -> DexpiDocument:
    doc = DexpiDocument()
    for item_id, kind in (("V1", ItemKind.EQUIPMENT), ("P1", ItemKind.EQUIPMENT), ("S1", ItemKind.PIPING_SEGMENT)):
        doc.add(DexpiItem(id=item_id, class_name="X", kind=kind))
    doc.connections = [
        DexpiConnection(from_item="V1", from_node="V1-n1", to_item="P1", to_node="P1-n1", owner_id="S1"),
        # The same node at the other end of a second edge: first statement wins for flow.
        DexpiConnection(from_item="P1", from_node="P1-n1", to_item="V1", to_node="V1-n2", owner_id="S1"),
        # Owned by nothing the document knows, and one end missing.
        DexpiConnection(from_item="V1", from_node=None, to_item=None, to_node=None, owner_id="ghost"),
        DexpiConnection(from_item="P1", from_node="P1-n2", to_item="V1", to_node="V1-n1", owner_id=None),
    ]
    return doc


def test_owned_by_keeps_document_order_and_exact_owner_match():
    index = ConnectionIndex.from_document(_doc())
    assert [c.to_node for c in index.owned_by("S1")] == ["P1-n1", "V1-n2"]
    assert index.owned_by("ghost")[0].from_item == "V1"
    assert index.owned_by("nope") == []


def test_grouped_by_owner_files_unknown_owners_under_none():
    doc = _doc()
    grouped = ConnectionIndex.from_document(doc).grouped_by_owner()
    assert set(grouped) == {"S1", None}
    assert len(grouped[None]) == 2
    # The former writer walk, verbatim.
    expected: dict = {}
    for connection in doc.connections:
        expected.setdefault(connection.owner_id if connection.owner_id in doc.items else None, []).append(connection)
    assert grouped == expected


def test_ends_of_lists_both_ends_from_first_and_skips_a_missing_item():
    index = ConnectionIndex.from_document(_doc())
    assert index.ends_of("S1") == [
        ("V1", "V1-n1", "from"),
        ("P1", "P1-n1", "to"),
        ("P1", "P1-n1", "from"),
        ("V1", "V1-n2", "to"),
    ]
    assert index.ends_of("ghost") == [("V1", None, "from")]


def test_touching_and_at_node_match_the_naive_filters():
    doc = _doc()
    index = ConnectionIndex.from_document(doc)
    assert index.touching("V1") == [c for c in doc.connections if "V1" in (c.from_item, c.to_item)]
    assert index.touching("P1") == [c for c in doc.connections if "P1" in (c.from_item, c.to_item)]
    assert index.at_node("V1-n1") == [c for c in doc.connections if "V1-n1" in (c.from_node, c.to_node)]
    assert index.at_node("absent") == []


def test_flows_are_first_statement_wins_and_keyed_by_item_and_node():
    doc = _doc()
    index = ConnectionIndex.from_document(doc)
    flows = index.flows
    # V1 is a source on the first edge, P1-n1 a target there -- the second edge does not overturn either.
    assert flows["V1"] == "out" and flows["V1-n1"] == "out"
    assert flows["P1"] == "in" and flows["P1-n1"] == "in"
    assert flows["V1-n2"] == "in" and flows["P1-n2"] == "out"
    assert index.flow("missing", "P1-n2") == "out"
    assert index.flow("missing", None) is None
    # The public helper is the same answer.
    assert connection_flow(doc) == flows
    flows["V1"] = "in"
    assert index.flows["V1"] == "out", "flows is a copy"
