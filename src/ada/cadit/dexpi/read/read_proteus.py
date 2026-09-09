"""Read a Proteus XML document (DEXPI 1.3/1.4) into the neutral model.

Proteus files carry no XML namespaces, so every lookup here is on a plain tag name.

The hard part is not the element vocabulary, it is that **Proteus addresses connection points by
position, not by identity**. ``<Connection FromID="Nozzle-1" FromNode="1"/>`` means "the node at
index 1 of Nozzle-1's ``<ConnectionPoints>``", and index 0 of that list is the symbol's own anchor
point rather than a process connection. Nothing downstream should have to know that, so the reader
resolves every positional reference to a node ID once, here, in two passes:

*Pass 1* walks every element that owns a ``<ConnectionPoints>``, at any depth -- equipment, nested
chambers, nozzles, piping components, off-page connectors -- and records its nodes by ordinal. The
node **count** is what matters; ``@NumPoints`` is advisory and is ignored, as is
``GenericAttributes/@Number``.

*Pass 2* resolves ``@FromNode``/``@ToNode`` (and ``@FlowIn``/``@FlowOut``, which are the same kind
of index) through that record. The index is **0-based**, counting the anchor as 0: every one of the
35 official DEXPI 1.3 test cases writes ``FromNode="1"`` for the first *process* node of a
two-node nozzle, and a ``<ConnectionPoints FlowIn="1" FlowOut="2">`` over the nodes
``X-Node-0``/``X-Node-1``/``X-Node-2`` leaves no other reading. Emitters do disagree, though, so
the resolver tries three readings in turn and warns on anything but the first. A single off-by-one
connector in a 400 KB export must not cost the caller the other 99% of the file, so failures are
collected on :attr:`~ada.cadit.dexpi.model.DexpiDocument.warnings` rather than raised.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from ada.config import logger

from .. import attributes as attribute_lookup
from .. import class_table
from ..model import (
    DexpiAssociation,
    DexpiAttribute,
    DexpiConnection,
    DexpiDocument,
    DexpiFlavour,
    DexpiHeader,
    DexpiItem,
    DexpiNode,
    DexpiPlacement,
    classify,
)

__all__ = ["read_proteus"]

# The document header. Everything it says lives on DexpiHeader, not in extras.
_HEADER_TAG = "PlantInformation"

# Tags that never become an item, however many IDs and classes they carry. Geometry, presentation
# and the sub-structures an item is parsed *from*; they survive on the owning item's ``raw``.
_NON_ITEM_TAGS = frozenset(
    {
        "Association",
        "Axis",
        "CenterLine",
        "Circle",
        "Connection",
        "ConnectionPoints",
        "Coordinate",
        "Curve",
        "Drawing",
        "Ellipse",
        "Extent",
        "GenericAttribute",
        "GenericAttributes",
        "InsulationSymbol",
        "Label",
        "Line",
        "Location",
        "Max",
        "Min",
        "Node",
        "ObjectAttributesReference",
        "PersistentID",
        "PipeFlowArrow",
        "PipeSlopeSymbol",
        "PlantInformation",
        "PolyLine",
        "Position",
        "Presentation",
        "Reference",
        "Scale",
        "Shape",
        "ShapeCatalogue",
        "Symbol",
        "Text",
        "TextStringFormatSpecification",
        "TrimmedCurve",
        "UnitsOfMeasure",
    }
)

# Subtrees the reader does not descend into at all: a symbol library and a sheet layout say
# nothing about the plant, and both are echoed verbatim from ``extras`` on write.
_OPAQUE_TAGS = frozenset({"Drawing", "ShapeCatalogue"})

# How a positional node reference was read. Only the first is the convention every file in the
# official DEXPI 1.3 corpus uses; the other two exist because emitters in the wild do not agree.
_BY_POSITION = "0-based position"
_BY_NODE_ID = "node ID in a positional slot"
_BY_ONE_BASED = "1-based position"


def read_proteus(source: str | pathlib.Path | ET.Element | ET.ElementTree) -> DexpiDocument:
    """Parse a Proteus ``<PlantModel>`` into a :class:`DexpiDocument`.

    ``source`` is a path, an already-parsed element, or a tree. Structural problems are collected
    on ``doc.warnings`` and logged; only a document that is not Proteus at all raises.
    """
    root, origin = _root_of(source)
    if root.tag != "PlantModel":
        raise ValueError(f"not a Proteus document: root element is <{root.tag}>, expected <PlantModel>")

    doc = DexpiDocument(flavour=DexpiFlavour.PROTEUS, source=origin, raw_root=root)
    doc.header = _read_header(root.find(_HEADER_TAG))

    node_index, connection_elements = _scan(root, doc)

    for child in root:
        if child.tag == _HEADER_TAG:
            continue
        if _is_item(child):
            _read_item(child, None, doc, node_index)
        else:
            doc.extras.append(child)

    _read_connections(connection_elements, node_index, doc)
    return doc


# -- sources ------------------------------------------------------------------------------------


def _root_of(source: str | pathlib.Path | ET.Element | ET.ElementTree) -> tuple[ET.Element, str | None]:
    if isinstance(source, ET.ElementTree):
        return source.getroot(), None
    if isinstance(source, ET.Element):
        return source, None

    path = pathlib.Path(source)
    if not path.exists():
        raise FileNotFoundError(path)
    return ET.parse(path).getroot(), str(path)


def _read_header(element: ET.Element | None) -> DexpiHeader:
    if element is None:
        return DexpiHeader()
    return DexpiHeader(
        originating_system=element.get("OriginatingSystem"),
        originating_system_vendor=element.get("OriginatingSystemVendor"),
        originating_system_version=element.get("OriginatingSystemVersion"),
        export_date=element.get("Date"),
        export_time=element.get("Time"),
        schema_version=element.get("SchemaVersion"),
        units=element.get("Units"),
        project=element.get("ProjectName") or element.get("Project"),
        raw=element,
    )


# -- pass 1: the node index ---------------------------------------------------------------------


def _walk(element: ET.Element):
    """Depth-first over ``element`` and its descendants, without entering an opaque subtree."""
    yield element
    for child in element:
        if child.tag in _OPAQUE_TAGS:
            continue
        yield from _walk(child)


def _scan(
    root: ET.Element, doc: DexpiDocument
) -> tuple[dict[str, list[DexpiNode]], list[tuple[str | None, ET.Element]]]:
    """Index every owner's connection points, and collect every ``<Connection>`` with its owner.

    Both are gathered in one walk over the whole document rather than while reading items, because
    a Proteus connection may name an owner at any nesting depth and may itself sit under an element
    the item reader does not model.
    """
    node_index: dict[str, list[DexpiNode]] = {}
    connections: list[tuple[str | None, ET.Element]] = []

    for element in _walk(root):
        owner_id = element.get("ID")

        for connection in element.findall("Connection"):
            connections.append((owner_id, connection))

        points = element.find("ConnectionPoints")
        if points is None:
            continue
        if owner_id is None:
            doc.warnings.append(f"<{element.tag}> has connection points but no ID; its nodes cannot be addressed")
            logger.warning(doc.warnings[-1])
            continue
        if owner_id in node_index:
            doc.warnings.append(f"two elements share the ID {owner_id!r}; keeping the first set of nodes")
            logger.warning(doc.warnings[-1])
            continue

        node_index[owner_id] = _read_nodes(points, owner_id, doc)

    return node_index, connections


def _read_nodes(points: ET.Element, owner_id: str, doc: DexpiDocument) -> list[DexpiNode]:
    """The owner's nodes, in document order, ordinal 1..N.

    ``@NumPoints`` is deliberately not trusted -- exports disagreeing with their own node count are
    common enough that believing them would drop real connection points.
    """
    elements = points.findall("Node")

    declared = points.get("NumPoints")
    if declared is not None and declared.strip().isdigit() and int(declared) != len(elements):
        logger.debug(
            "%s declares NumPoints=%s but has %d <Node> children; using the count", owner_id, declared, len(elements)
        )

    # An emitter that omits Type on every node is telling us nothing, so fall back to the
    # convention that the anchor is the first node rather than calling them all anchors.
    all_untyped = all(node.get("Type") is None for node in elements)

    nodes: list[DexpiNode] = []
    for position, element in enumerate(elements):
        ordinal = position + 1
        node_id = element.get("ID") or f"{owner_id}#node{ordinal}"
        node_type = element.get("Type")
        attributes = _read_attributes(element)
        carrier = SimpleNamespace(attributes=attributes)

        nodes.append(
            DexpiNode(
                id=node_id,
                ordinal=ordinal,
                owner_id=owner_id,
                node_type=node_type,
                is_anchor=_is_anchor(position, node_id, node_type, all_untyped),
                position=_point(element.find("Position/Location")),
                direction=_point(element.find("Position/Reference")),
                tag=attribute_lookup.tag_of(carrier),
                nominal_diameter=attribute_lookup.nominal_diameter_of(carrier),
                raw=element,
            )
        )

    for attribute, flow in (("FlowIn", "in"), ("FlowOut", "out")):
        token = points.get(attribute)
        if token is None:
            continue
        node = _resolve(nodes, token, doc, f"{owner_id}/@{attribute}")
        if node is not None:
            node.flow = flow

    return nodes


def _is_anchor(position: int, node_id: str, node_type: str | None, all_untyped: bool) -> bool:
    """Is this the symbol's own anchor point rather than a process connection?

    Belt and braces: the anchor is conventionally named ``<owner>-DefaultNode`` and conventionally
    carries no ``Type``, and emitters honour one convention or the other. The anchor keeps its
    ordinal either way -- it is excluded when generating ports, never renumbered away.
    """
    if node_id.endswith("-DefaultNode"):
        return True
    if node_type is None:
        return position == 0 if all_untyped else True
    return False


# -- pass 2: positional references ---------------------------------------------------------------


def _resolve(nodes: list[DexpiNode], token: str, doc: DexpiDocument, context: str) -> DexpiNode | None:
    """Resolve one positional node reference, tolerating the ways emitters get it wrong.

    In order: the 0-based position the DEXPI 1.3 corpus actually uses; a node ID written into the
    positional slot; a 1-based position. Anything but the first is logged, because a document that
    needs a fallback is a document whose connectivity deserves a second look.
    """
    text = token.strip()
    if not text:
        return None

    node, how = _lookup(nodes, text)
    if node is None:
        message = f"{context}: node reference {token!r} does not resolve against {len(nodes)} node(s)"
        doc.warnings.append(message)
        logger.warning(message)
        return None

    if how is not _BY_POSITION:
        message = f"{context}: node reference {token!r} resolved by {how}, not by {_BY_POSITION}"
        doc.warnings.append(message)
        logger.warning(message)
    return node


def _lookup(nodes: list[DexpiNode], token: str) -> tuple[DexpiNode | None, str | None]:
    try:
        value: int | None = int(token)
    except ValueError:
        value = None

    if value is not None and 0 <= value < len(nodes):
        return nodes[value], _BY_POSITION

    for node in nodes:
        if node.id == token:
            return node, _BY_NODE_ID

    if value is not None and 1 <= value <= len(nodes):
        return nodes[value - 1], _BY_ONE_BASED

    return None, None


def _read_connections(
    elements: list[tuple[str | None, ET.Element]],
    node_index: dict[str, list[DexpiNode]],
    doc: DexpiDocument,
) -> None:
    for owner_id, element in elements:
        from_item, from_node = _endpoint(element, "From", owner_id, node_index, doc)
        to_item, to_node = _endpoint(element, "To", owner_id, node_index, doc)
        if from_item is None and to_item is None:
            continue
        doc.connections.append(
            DexpiConnection(
                from_item=from_item,
                from_node=from_node,
                to_item=to_item,
                to_node=to_node,
                owner_id=owner_id,
                raw=element,
            )
        )


def _endpoint(
    element: ET.Element,
    end: str,
    owner_id: str | None,
    node_index: dict[str, list[DexpiNode]],
    doc: DexpiDocument,
) -> tuple[str | None, str | None]:
    """One end of a ``<Connection>``: the item it names, and the node ID its index resolves to.

    A dangling end -- a segment that stops in mid-air -- is legal Proteus and yields ``(None, None)``
    without a warning.
    """
    item_id = element.get(f"{end}ID")
    token = element.get(f"{end}Node")
    if item_id is None:
        return None, None

    nodes = node_index.get(item_id)
    if nodes is None:
        message = f"connection in {owner_id or '<document>'}: {end}ID {item_id!r} has no connection points"
        doc.warnings.append(message)
        logger.warning(message)
        return item_id, None

    if token is None:
        return item_id, None

    node = _resolve(nodes, token, doc, f"connection in {owner_id or '<document>'}/@{end}Node")
    return item_id, node.id if node is not None else None


# -- items ----------------------------------------------------------------------------------------


def _is_item(element: ET.Element) -> bool:
    """Every addressable DEXPI object writes both an ``ID`` and a ``ComponentClass``.

    Elements that carry an ID but no class are symbols, flow arrows and shape-catalogue templates:
    presentation, not plant, and kept on the owning item's ``raw`` instead.
    """
    return (
        element.tag not in _NON_ITEM_TAGS
        and element.get("ID") is not None
        and element.get("ComponentClass") is not None
    )


def _read_item(
    element: ET.Element,
    parent_id: str | None,
    doc: DexpiDocument,
    node_index: dict[str, list[DexpiNode]],
) -> DexpiItem:
    """Build the item for ``element`` and, recursively, for the items nested in it.

    ``composition_role`` records the Proteus element tag, which is what distinguishes an
    ``<Equipment ComponentClass="Chamber">`` nested in an ``<Equipment>`` -- a chamber of its owner,
    not a plant asset of its own -- from the top-level equipment it sits inside.
    """
    item_id = element.get("ID")
    class_name = element.get("ComponentClass") or element.tag

    if item_id in doc.items:
        message = f"duplicate item ID {item_id!r}; keeping the first <{doc.items[item_id].composition_role}>"
        doc.warnings.append(message)
        logger.warning(message)
        return doc.items[item_id]

    item = DexpiItem(
        id=item_id,
        class_name=class_table.resolve(class_name),
        kind=classify(class_name),
        class_uri=element.get("ComponentClassURI"),
        composition_role=element.tag,
        nodes=node_index.get(item_id, []),
        attributes=_read_attributes(element),
        associations=_read_associations(element, item_id),
        placement=_read_placement(element),
        metadata=_read_metadata(element),
        raw=element,
    )
    doc.add(item, parent_id)

    for child in element:
        if _is_item(child):
            _read_item(child, item_id, doc, node_index)

    return item


def _read_attributes(element: ET.Element) -> list[DexpiAttribute]:
    """Every ``<GenericAttribute>`` under ``element``'s own ``<GenericAttributes>`` sets.

    The set name is carried onto each attribute, because a Proteus document may hold several sets
    side by side -- the DEXPI ones and a vendor's own -- and the writer has to put them back.
    ``@Number`` is ignored for the same reason ``@NumPoints`` is.
    """
    out: list[DexpiAttribute] = []
    for group in element.findall("GenericAttributes"):
        set_name = group.get("Set")
        for attribute in group.findall("GenericAttribute"):
            out.append(
                DexpiAttribute(
                    name=attribute.get("Name") or "",
                    value=attribute.get("Value"),
                    uri=attribute.get("AttributeURI"),
                    format=attribute.get("Format"),
                    units=attribute.get("Units"),
                    units_uri=attribute.get("UnitsURI"),
                    value_uri=attribute.get("ValueURI"),
                    language=attribute.get("Language"),
                    set_name=set_name,
                )
            )
    return out


def _read_associations(element: ET.Element, owner_id: str) -> list[DexpiAssociation]:
    """One :class:`DexpiAssociation` per ``<Association>``, kept separate rather than grouped by
    type, so a re-write can put each element back where it was."""
    return [
        DexpiAssociation(
            type=association.get("Type") or "",
            owner_id=owner_id,
            target_ids=[association.get("ItemID")] if association.get("ItemID") else [],
            raw=association,
        )
        for association in element.findall("Association")
    ]


def _read_placement(element: ET.Element) -> DexpiPlacement | None:
    """The item's own schematic geometry: where its symbol sits on the sheet.

    Only direct children count -- an equipment's ``<Position>`` is its own, while the ones further
    down belong to its nozzles and labels. Values stay in the document's units; this is drawing
    geometry and never becomes plant geometry.
    """
    position = element.find("Position")
    extent = element.find("Extent")
    polylines = element.findall("PolyLine")
    if position is None and extent is None and not polylines:
        return None

    points: list[tuple[float, float, float]] = []
    for polyline in polylines:
        for coordinate in polyline.findall("Coordinate"):
            point = _point(coordinate)
            if point is not None:
                points.append(point)

    return DexpiPlacement(
        location=_point(position.find("Location")) if position is not None else None,
        axis=_point(position.find("Axis")) if position is not None else None,
        reference=_point(position.find("Reference")) if position is not None else None,
        extent_min=_point(extent.find("Min")) if extent is not None else None,
        extent_max=_point(extent.find("Max")) if extent is not None else None,
        polyline=points,
    )


def _read_metadata(element: ET.Element) -> dict:
    """The item facts that have a home in neither the model nor the attribute list.

    ``PersistentID`` is the emitter's own stable identity for the item, which a later import needs
    in order to recognise the same object again.
    """
    metadata: dict = {}

    component_name = element.get("ComponentName")
    if component_name is not None:
        metadata["component_name"] = component_name

    persistent = element.find("PersistentID")
    if persistent is not None:
        metadata["persistent_id"] = {
            "identifier": persistent.get("Identifier"),
            "context": persistent.get("Context"),
        }

    extra = {
        key: value
        for key, value in element.attrib.items()
        if key not in ("ID", "ComponentClass", "ComponentClassURI", "ComponentName")
    }
    if extra:
        metadata["proteus_attributes"] = extra

    return metadata


def _point(element: ET.Element | None) -> tuple[float, float, float] | None:
    """An ``X``/``Y``/``Z`` triple. ``Z`` is routinely omitted on a 2D sheet and reads as zero."""
    if element is None:
        return None
    try:
        return (
            float(element.get("X", 0.0)),
            float(element.get("Y", 0.0)),
            float(element.get("Z", 0.0)),
        )
    except (TypeError, ValueError):
        return None
