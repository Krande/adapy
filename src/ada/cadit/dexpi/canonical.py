"""Comparison oracles for the DEXPI round-trip tests.

Two different claims need two different oracles, and conflating them yields a test only a
byte-copier can pass:

* :func:`canonicalize` -- *did the document survive a write and re-read?* It renders a
  :class:`~ada.cadit.dexpi.model.DexpiDocument` to a plain, deterministically ordered dict.
  Everything that legitimately differs between two serializations of the same document is
  normalized away: attribute order, float formatting, and the export timestamp and originating
  system in the header. IDs are kept, because a write-then-read must preserve them.

* :func:`graph_signature` -- *is this the same P&ID?* Same document, expressed without IDs, so a
  Proteus file and its DEXPI 2.0 conversion compare equal. Items are keyed by their path through
  the composition hierarchy (``CentrifugalPump[P-100]/Nozzle[N1]``) and nodes by their position
  among the owner's **process** nodes, which are the only identities that survive a flavour
  conversion. The Proteus symbol anchor is deliberately excluded: it occupies an ordinal in a
  Proteus file and has no counterpart at all in DEXPI 2.0, where the symbol placement lives in the
  diagram, so counting it would make the two flavours disagree on every nozzle in every document.

Never assert byte-identity between two DEXPI files. Attribute order, self-closing form, float
formatting and the unreliable ``@NumPoints``/``@Number`` counters all differ legitimately, and
``ElementTree`` does not preserve the source anyway.
"""

from __future__ import annotations

from .model import DexpiAttribute, DexpiDocument, DexpiItem, DexpiNode, DexpiPlacement

__all__ = ["canonicalize", "format_float", "graph_signature", "item_path"]

# Header fields that change on every export and say nothing about the content.
_VOLATILE_HEADER_FIELDS = ("export_date", "export_time", "originating_system")


def format_float(value: float | None) -> str | None:
    """Render a coordinate the way both writers will: six significant digits.

    Enough to distinguish anything a P&ID actually contains, coarse enough that 0.1 + 0.2 and 0.3
    do not compare unequal after a round trip through two serializers.
    """
    return None if value is None else f"{float(value):.6g}"


def _vector(value) -> list[str] | None:
    return None if value is None else [format_float(component) for component in value]


def _attribute(attribute: DexpiAttribute) -> dict:
    return {
        "name": attribute.name,
        "value": attribute.value,
        "uri": attribute.uri,
        "format": attribute.format,
        "units": attribute.units,
        "units_uri": attribute.units_uri,
        "value_uri": attribute.value_uri,
        "language": attribute.language,
        "set_name": attribute.set_name,
    }


def _attribute_sort_key(attribute: DexpiAttribute) -> tuple[str, str, str]:
    return (attribute.set_name or "", attribute.name or "", str(attribute.value))


def _node(node: DexpiNode) -> dict:
    return {
        "id": node.id,
        "ordinal": node.ordinal,
        "type": node.node_type,
        "is_anchor": node.is_anchor,
        "flow": node.flow,
        "tag": node.tag,
        "nominal_diameter": format_float(node.nominal_diameter),
        "position": _vector(node.position),
        "direction": _vector(node.direction),
    }


def _placement(placement: DexpiPlacement | None) -> dict | None:
    if placement is None:
        return None
    return {
        "location": _vector(placement.location),
        "axis": _vector(placement.axis),
        "reference": _vector(placement.reference),
        "extent_min": _vector(placement.extent_min),
        "extent_max": _vector(placement.extent_max),
        "polyline": [_vector(point) for point in placement.polyline],
    }


def _item(item: DexpiItem) -> dict:
    return {
        "id": item.id,
        "class_name": item.class_name,
        "kind": item.kind.value,
        "class_uri": item.class_uri,
        "parent_id": item.parent_id,
        "child_ids": sorted(item.child_ids),
        "composition_role": item.composition_role,
        "attributes": [_attribute(a) for a in sorted(item.attributes, key=_attribute_sort_key)],
        "nodes": [_node(n) for n in sorted(item.nodes, key=lambda n: n.ordinal)],
        "associations": [
            {"type": association.type, "targets": sorted(association.target_ids)}
            for association in sorted(item.associations, key=lambda a: (a.type, sorted(a.target_ids)))
        ],
        "placement": _placement(item.placement),
    }


def canonicalize(doc: DexpiDocument) -> dict:
    """Render ``doc`` to a deterministically ordered dict for equality comparison.

    Two documents that mean the same thing compare equal here even if their XML differs. The
    flavour is deliberately *not* part of the result -- a Proteus document and its DEXPI 2.0
    conversion should differ in their IDs, not in what they say -- see :func:`graph_signature` for
    the cross-flavour claim.
    """
    header = {
        field: getattr(doc.header, field)
        for field in (
            "originating_system_vendor",
            "originating_system_version",
            "schema_version",
            "units",
            "project",
            "model_uri",
            "imports",
        )
    }

    return {
        "header": header,
        "root_ids": sorted(doc.root_ids),
        "items": [_item(doc.items[item_id]) for item_id in sorted(doc.items)],
        # Missing endpoints render as "" rather than None so the rows stay sortable.
        "connections": sorted(
            [
                connection.from_item or "",
                connection.from_node or "",
                connection.to_item or "",
                connection.to_node or "",
                connection.owner_id or "",
            ]
            for connection in doc.connections
        ),
    }


def item_path(doc: DexpiDocument, item_id: str) -> str:
    """An ID-free identity for an item: its class and tag, prefixed by its ancestors'.

    ``ProcessEquipment[V-201]/Chamber[boot]/Nozzle[N3]``. Two documents describing the same P&ID
    agree on this; they do not agree on IDs.
    """
    item = doc.items.get(item_id)
    if item is None:
        return f"<missing:{item_id}>"

    parts = [f"{step.class_name}[{step.tag or ''}]" for step in reversed(doc.ancestors(item_id))]
    parts.append(f"{item.class_name}[{item.tag or ''}]")
    return "/".join(parts)


def _process_position(item: DexpiItem, node: DexpiNode) -> int | None:
    """1-based position of ``node`` among ``item``'s process nodes, or None for the anchor.

    A Proteus anchor sits at ordinal 1 and shifts every real connection point after it; a DEXPI 2.0
    document has no such node. Numbering the process nodes on their own is what makes the two
    flavours agree.
    """
    if node.is_anchor:
        return None

    position = 0
    for candidate in sorted(item.nodes, key=lambda n: n.ordinal):
        if candidate.is_anchor:
            continue
        position += 1
        if candidate is node:
            return position
    return None


def _node_ref(doc: DexpiDocument, item_id: str | None, node_id: str | None) -> str:
    if item_id is None:
        return "<none>"
    path = item_path(doc, item_id)
    if node_id is None:
        return path

    item = doc.items.get(item_id)
    node = item.node_by_id(node_id) if item is not None else None
    if node is None:
        return f"{path}#<missing:{node_id}>"

    position = _process_position(item, node)
    return f"{path}#{position}" if position is not None else f"{path}#anchor"


def graph_signature(doc: DexpiDocument) -> dict:
    """An ID-free structural signature: what is in the document and how it is wired.

    Equal signatures for the same logical P&ID read from either flavour is the convergence contract
    the two readers are held to.
    """
    items: list[str] = []
    nodes: list[str] = []
    for item_id, item in doc.items.items():
        path = item_path(doc, item_id)
        items.append(path)
        for node in item.nodes:
            position = _process_position(item, node)
            if position is not None:
                nodes.append(f"{path}#{position}")

    connections = [
        (
            _node_ref(doc, connection.from_item, connection.from_node),
            _node_ref(doc, connection.to_item, connection.to_node),
        )
        for connection in doc.connections
    ]

    return {
        "items": sorted(items),
        "nodes": sorted(nodes),
        "connections": sorted(connections),
    }
