"""Structural validation of a parsed DEXPI document.

Deliberately *not* schema validation. XSD validation of DEXPI 2.0 would need lxml or xmlschema,
and adapy carries neither; the checks here run on the neutral model instead and catch the failures
that actually bite -- a connection pointing at a node that does not exist, a composition cycle, a
node ordinal that got renumbered.

Returns a list of messages rather than raising. A file from the wild is usually 95% usable and the
importer should say what it could not make sense of, not refuse the whole document.
"""

from __future__ import annotations

from . import class_table
from .model import DexpiDocument, DexpiItem, classify

__all__ = ["validate_document"]


def validate_document(doc: DexpiDocument) -> list[str]:
    """Check ``doc`` for structural problems. An empty list means it is internally consistent."""
    problems: list[str] = []

    _check_index(doc, problems)
    _check_hierarchy(doc, problems)
    _check_nodes(doc, problems)
    _check_connections(doc, problems)
    _check_classes(doc, problems)

    return problems


def _check_index(doc: DexpiDocument, problems: list[str]) -> None:
    for key, item in doc.items.items():
        if item.id != key:
            problems.append(f"item indexed under {key!r} reports id {item.id!r}")

    for root_id in doc.root_ids:
        item = doc.items.get(root_id)
        if item is None:
            problems.append(f"root id {root_id!r} is not in items")
        elif item.parent_id is not None:
            problems.append(f"root item {root_id!r} has parent {item.parent_id!r}")

    unreachable = [
        item.id for item in doc.items.values() if item.parent_id is None and item.id not in set(doc.root_ids)
    ]
    for item_id in sorted(unreachable):
        problems.append(f"item {item_id!r} has no parent and is not listed as a root")


def _check_hierarchy(doc: DexpiDocument, problems: list[str]) -> None:
    for item in doc.items.values():
        if item.parent_id is not None:
            parent = doc.items.get(item.parent_id)
            if parent is None:
                problems.append(f"item {item.id!r} has unknown parent {item.parent_id!r}")
            elif item.id not in parent.child_ids:
                problems.append(f"item {item.id!r} names parent {item.parent_id!r} but is not among its children")

        seen_children: set[str] = set()
        for child_id in item.child_ids:
            if child_id in seen_children:
                problems.append(f"item {item.id!r} lists child {child_id!r} more than once")
            seen_children.add(child_id)

            child = doc.items.get(child_id)
            if child is None:
                problems.append(f"item {item.id!r} has unknown child {child_id!r}")
            elif child.parent_id != item.id:
                problems.append(f"item {item.id!r} lists child {child_id!r}, which names parent {child.parent_id!r}")

    for item_id in sorted(doc.items):
        cycle = _cycle_from(doc, item_id)
        if cycle is not None:
            problems.append(f"composition cycle: {' -> '.join(cycle)}")
            break  # one report is enough; every member of the cycle would produce the same one


def _cycle_from(doc: DexpiDocument, item_id: str) -> list[str] | None:
    seen = [item_id]
    current = doc.items.get(item_id)
    while current is not None and current.parent_id is not None:
        seen.append(current.parent_id)
        if current.parent_id in seen[:-1]:
            return seen
        current = doc.items.get(current.parent_id)
    return None


def _check_nodes(doc: DexpiDocument, problems: list[str]) -> None:
    seen_ids: dict[str, str] = {}

    for item in doc.items.values():
        ordinals = [node.ordinal for node in item.nodes]
        if len(set(ordinals)) != len(ordinals):
            problems.append(f"item {item.id!r} has duplicate node ordinals {sorted(ordinals)}")
        elif ordinals and sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            # Proteus addresses nodes by position, so a gap means a connection resolves to the
            # wrong node rather than to nothing.
            problems.append(f"item {item.id!r} node ordinals are not 1..{len(ordinals)}: {sorted(ordinals)}")

        for node in item.nodes:
            if node.owner_id is not None and node.owner_id != item.id:
                problems.append(f"node {node.id!r} on item {item.id!r} reports owner {node.owner_id!r}")
            owner = seen_ids.get(node.id)
            if owner is not None:
                problems.append(f"node id {node.id!r} is used by both {owner!r} and {item.id!r}")
            else:
                seen_ids[node.id] = item.id


def _check_connections(doc: DexpiDocument, problems: list[str]) -> None:
    for index, connection in enumerate(doc.connections):
        label = f"connection {index}"
        _check_endpoint(doc, problems, label, "from", connection.from_item, connection.from_node)
        _check_endpoint(doc, problems, label, "to", connection.to_item, connection.to_node)

        if connection.owner_id is not None and connection.owner_id not in doc.items:
            problems.append(f"{label} is owned by unknown item {connection.owner_id!r}")


def _check_endpoint(
    doc: DexpiDocument,
    problems: list[str],
    label: str,
    end: str,
    item_id: str | None,
    node_id: str | None,
) -> None:
    if item_id is None:
        problems.append(f"{label} has no {end} item")
        return

    item = doc.items.get(item_id)
    if item is None:
        problems.append(f"{label} {end} references unknown item {item_id!r}")
        return

    if node_id is None:
        return
    if item.node_by_id(node_id) is None:
        problems.append(f"{label} {end} references node {node_id!r}, which item {item_id!r} does not have")


def _check_classes(doc: DexpiDocument, problems: list[str]) -> None:
    for item in sorted(doc.items.values(), key=lambda i: i.id):
        if not item.class_name:
            problems.append(f"item {item.id!r} has no class name")
            continue
        if class_table.get(item.class_name) is None:
            problems.append(f"item {item.id!r} has class {item.class_name!r}, unknown to the DEXPI class table")
            continue

        _check_kind(item, problems)


def _check_kind(item: DexpiItem, problems: list[str]) -> None:
    expected = classify(item.class_name)
    if item.kind is not expected:
        problems.append(
            f"item {item.id!r} is classified {item.kind.value!r}, but {item.class_name!r} is {expected.value!r}"
        )
