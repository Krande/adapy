"""Write a :class:`~ada.cadit.dexpi.model.DexpiDocument` back out as Proteus XML (DEXPI 1.3/1.4).

The exact inverse of :mod:`ada.cadit.dexpi.read.read_proteus`, and it inherits that module's one
load-bearing detail.

.. warning::

   **``Connection/@FromNode``, ``@ToNode``, ``ConnectionPoints/@FlowIn`` and ``@FlowOut`` are
   0-BASED positions, counting the symbol anchor as slot 0.** A nozzle whose connection points are
   ``[Nozzle-1-DefaultNode, PipingNode-1]`` is wired through ``FromNode="1"``.

   This is not a detail that a round-trip test can protect. Emitting 1-based indices would produce
   files that parse back through adapy's own reader unchanged -- index 1 is in range for every node
   list -- while silently wiring every connection to the symbol anchor for every other consumer in
   the world. :func:`_node_index` is the only place the conversion happens, and
   ``test_write_proteus.py`` asserts the literal integer in the output.

Everything the neutral model does not hold is echoed verbatim from :attr:`DexpiItem.raw` and
:attr:`DexpiDocument.extras` -- labels, symbols, presentation, centre lines, the shape catalogue and
the drawing -- so a re-write drops nothing. That echo is only possible when the document was *read*
from Proteus; converting a DEXPI 2.0 document loses whatever its unmodelled elements said, because
they are elements of a different vocabulary.

Two counters are recomputed from the children rather than echoed: ``ConnectionPoints/@NumPoints``
and ``GenericAttributes/@Number``. Exports in the wild disagree with their own contents often
enough that the reader ignores both, and a writer that copied a wrong count forward would be
propagating the error.

``ComponentClassURI`` is echoed on a Proteus source and **omitted otherwise**. Those are POSC
Caesar RDL URIs; the vendored class table has no RDL URIs in it (the spec references RDL
symbolically and the symbol table ships elsewhere), so there is nothing to derive one from, and a
plausible-looking wrong URI is worse than none.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ada.config import logger

from .. import attributes as attribute_lookup
from .. import class_table
from ..model import (
    DexpiAttribute,
    DexpiConnection,
    DexpiDocument,
    DexpiFlavour,
    DexpiItem,
    DexpiNode,
    DexpiPlacement,
    ItemKind,
)
from ..read.connectivity import ConnectionIndex
from . import xml_utils

__all__ = ["write_proteus"]

# What a from-scratch document says it was written by. Deliberately the library name and nothing
# else -- a fixture must not carry the name of the machine or the organisation that generated it.
ORIGINATING_SYSTEM = "adapy"

# The attribute set a synthesized <GenericAttributes> goes into, which is what every DEXPI emitter
# uses for the specification's own attributes.
DEXPI_ATTRIBUTE_SET = "DexpiAttributes"

# <PlantInformation> attributes the header models, in the order they are written. Anything else the
# source put there is echoed after them.
_HEADER_ATTRIBUTES = (
    "OriginatingSystem",
    "OriginatingSystemVendor",
    "OriginatingSystemVersion",
    "Date",
    "Time",
    "SchemaVersion",
    "Units",
    "ProjectName",
    "Project",
)

# Item attributes the writer produces itself; the rest of the source element's attributes ride on
# ``metadata["proteus_attributes"]`` and are echoed after them.
_ITEM_ATTRIBUTES = ("ID", "ComponentClass", "ComponentClassURI", "ComponentName")

# Children of a source item that the writer regenerates from the model rather than echoing. A child
# with any other tag is copied across verbatim.
_REGENERATED_TAGS = frozenset(
    {
        "Association",
        "Connection",
        "ConnectionPoints",
        "Extent",
        "GenericAttributes",
        "PersistentID",
        "PolyLine",
        "Position",
    }
)

# <ConnectionPoints> attributes recomputed from the actual nodes rather than echoed.
_RECOMPUTED_POINT_ATTRIBUTES = frozenset({"FlowIn", "FlowOut", "NumPoints"})

# The Proteus element tag for an item, by kind. Proteus names the concept in the tag and the class
# in ``@ComponentClass``, so a chamber is an <Equipment ComponentClass="Chamber"> nested in another
# <Equipment>. Verified against the 220 official test files: these are the tags they use.
_TAG_BY_KIND = {
    ItemKind.EQUIPMENT: "Equipment",
    ItemKind.CHAMBER: "Equipment",
    ItemKind.NOZZLE: "Nozzle",
    ItemKind.PIPING_SYSTEM: "PipingNetworkSystem",
    ItemKind.PIPING_SEGMENT: "PipingNetworkSegment",
    ItemKind.PIPING_COMPONENT: "PipingComponent",
    ItemKind.OFF_PAGE_CONNECTOR: "PipeOffPageConnector",
    ItemKind.PLANT_STRUCTURE: "PlantStructureItem",
}

# Where the tag is not a function of the kind. Instrumentation is the interesting case: the corpus
# writes the class name as the tag for almost all of it (<ActuatingSystem>, <ActuatingFunction>,
# <InstrumentationLoopFunction>), which is what the fallback does, and a signal line is the one
# that disagrees.
_TAG_BY_CLASS = {"SignalConveyingFunction": "SignalLine"}


def write_proteus(doc: DexpiDocument) -> ET.Element:
    """Render ``doc`` as a Proteus ``<PlantModel>`` element.

    The element is returned rather than written, so a caller can post-process it;
    :func:`ada.cadit.dexpi.write.write_dexpi` is the path that puts it on disk.
    """
    echo = doc.flavour is DexpiFlavour.PROTEUS
    owned = _connections_by_owner(doc, echo)

    root = xml_utils.element("PlantModel")
    root.append(_header_element(doc, echo))

    for item in _items(doc, doc.root_ids):
        root.append(_item_element(doc, item, echo, owned))

    if echo:
        xml_utils.append_all(root, doc.extras)

    # A connection whose owner is not an item of this document -- one the source wrote at document
    # level, or one an edit orphaned -- goes back where Proteus also allows it: on the root.
    for connection in owned.get(None, ()):
        root.append(_connection_element(doc, connection))

    return root


# -- document scaffolding ---------------------------------------------------------------------------


def _items(doc: DexpiDocument, ids: list[str]) -> list[DexpiItem]:
    """The items behind ``ids``, in order, skipping any the document no longer holds."""
    return [doc.items[item_id] for item_id in ids if item_id in doc.items]


def _echoed_elements(doc: DexpiDocument, echo: bool) -> set[int]:
    """``id()`` of every source element that reaches the output inside a verbatim echo.

    Mirrors what :func:`_append_unmodelled` and the ``doc.extras`` pass actually copy, walking the
    same items in the same order the writer emits them. Identity is the right test because the echo
    copies the *source* elements this document was parsed from, so the objects are the same ones
    :attr:`DexpiConnection.raw` points at. The document holds a reference to every element in play
    for as long as the write runs, so no ``id()`` here can be a recycled one.
    """
    if not echo:
        return set()

    echoed: set[int] = set()

    def collect(element: ET.Element) -> None:
        echoed.add(id(element))
        for child in element:
            collect(child)

    def visit(item: DexpiItem) -> None:
        if item.raw is not None:
            for child in item.raw:
                if child.tag in _REGENERATED_TAGS or child.get("ID") in doc.items:
                    continue
                collect(child)
        for child_item in _items(doc, item.child_ids):
            visit(child_item)

    for item in _items(doc, doc.root_ids):
        visit(item)
    for extra in doc.extras:
        collect(extra)

    return echoed


def _connections_by_owner(doc: DexpiDocument, echo: bool) -> dict[str | None, list[DexpiConnection]]:
    """Group the connectivity graph by the item each edge is written inside.

    An owner that is not an item of the document is keyed under None and emitted at document level,
    which is where Proteus puts a connection that belongs to no segment.

    A connection nested inside a construct the model does not carry -- the ``<Connection>`` in an
    ``<InformationFlow>``, say -- is a different case, and it is skipped entirely: the echo copies
    its owner out whole, connection and all, so emitting it a second time from the model would
    write it twice. That was 14 of the 220 files in the official corpus, DEXPI 1.2 signal
    connectivity above all, and it is invisible to a fixture set that has no such construct in it.
    """
    echoed = _echoed_elements(doc, echo)
    emitted = (c for c in doc.connections if c.raw is None or id(c.raw) not in echoed)
    return ConnectionIndex(emitted, doc.items).grouped_by_owner()


def _header_element(doc: DexpiDocument, echo: bool) -> ET.Element:
    """``<PlantInformation>``: the modelled provenance first, then whatever else the source said.

    ``OriginatingSystem`` falls back to the library name for a document adapy built itself. The
    other fields do not fall back -- inventing a vendor or a schema version would be a claim the
    document cannot support, and the canonical comparison would then disagree with the source.
    """
    header = doc.header
    raw = header.raw if echo else None

    attributes: dict[str, object] = {
        "OriginatingSystem": header.originating_system or ORIGINATING_SYSTEM,
        "OriginatingSystemVendor": header.originating_system_vendor,
        "OriginatingSystemVersion": header.originating_system_version,
        "Date": header.export_date,
        "Time": header.export_time,
        "SchemaVersion": header.schema_version,
        "Units": header.units,
        "ProjectName": header.project,
    }
    if raw is not None:
        for key in sorted(raw.attrib):
            if key not in _HEADER_ATTRIBUTES:
                attributes[key] = raw.get(key)

    element = xml_utils.element("PlantInformation", attributes)
    if raw is not None:
        xml_utils.append_all(element, list(raw))
    return element


# -- items ------------------------------------------------------------------------------------------


def _element_tag(item: DexpiItem, echo: bool) -> str:
    """The Proteus element name to write ``item`` as.

    A document read from Proteus already knows: the reader recorded the source tag on
    ``composition_role``, and echoing it is what keeps a re-write faithful. A document that came
    from DEXPI 2.0 or was built in Python carries a composition *property* there instead
    (``TaggedPlantItems``, ``Nozzles``), which is not an element name, so the tag is derived from
    what the item is.
    """
    if echo and item.composition_role:
        return item.composition_role

    name = class_table.resolve(item.class_name)
    if name in _TAG_BY_CLASS:
        return _TAG_BY_CLASS[name]
    if class_table.is_a(name, "SignalOffPageConnector"):
        return "SignalOffPageConnector"
    return _TAG_BY_KIND.get(item.kind, name)


def _item_element(
    doc: DexpiDocument,
    item: DexpiItem,
    echo: bool,
    owned: dict[str | None, list[DexpiConnection]],
) -> ET.Element:
    """One item, with everything nested in it."""
    attributes: dict[str, object] = {
        "ID": item.id,
        "ComponentClass": item.class_name,
        # Only a Proteus source has a real RDL URI here; see the module docstring.
        "ComponentClassURI": item.class_uri if echo else None,
        "ComponentName": item.metadata.get("component_name"),
    }
    for key in sorted(item.metadata.get("proteus_attributes") or {}):
        if key not in _ITEM_ATTRIBUTES:
            attributes.setdefault(key, item.metadata["proteus_attributes"][key])

    element = xml_utils.element(_element_tag(item, echo), attributes)

    _append_generic_attributes(element, item.attributes)
    _append_persistent_id(element, item.metadata)
    _append_placement(element, item.placement, item.raw if echo else None)
    _append_connection_points(element, item, echo)

    for child in _items(doc, item.child_ids):
        element.append(_item_element(doc, child, echo, owned))

    _append_associations(element, item, echo)
    _append_unmodelled(element, doc, item, echo)

    for connection in owned.get(item.id, ()):
        element.append(_connection_element(doc, connection))

    return element


def _append_unmodelled(element: ET.Element, doc: DexpiDocument, item: DexpiItem, echo: bool) -> None:
    """Echo the children of the source element that the model does not carry.

    Labels, symbols, presentation, centre lines, insulation symbols: Proteus constructs adapy has
    no concept of, which would otherwise be silently dropped by every re-write. A child that *is*
    an item of this document is skipped, because it has already been regenerated above.
    """
    if not echo or item.raw is None:
        return
    for child in item.raw:
        if child.tag in _REGENERATED_TAGS or child.get("ID") in doc.items:
            continue
        element.append(xml_utils.copy_of(child))


# -- attributes -------------------------------------------------------------------------------------


def _append_generic_attributes(element: ET.Element, attributes: list[DexpiAttribute]) -> None:
    """One ``<GenericAttributes>`` per attribute set, in the order the sets were first seen.

    ``@Number`` is the actual count of the children written, never the number the source claimed.
    """
    groups: dict[str | None, list[DexpiAttribute]] = {}
    for attribute in attributes:
        groups.setdefault(attribute.set_name, []).append(attribute)

    for set_name, entries in groups.items():
        group = xml_utils.sub_element(element, "GenericAttributes", {"Set": set_name, "Number": len(entries)})
        for attribute in entries:
            xml_utils.sub_element(
                group,
                "GenericAttribute",
                {
                    "Name": attribute.name,
                    "AttributeURI": attribute.uri,
                    "Format": attribute.format,
                    "Value": attribute.value,
                    "Units": attribute.units,
                    "UnitsURI": attribute.units_uri,
                    "ValueURI": attribute.value_uri,
                    "Language": attribute.language,
                },
            )


def _append_persistent_id(element: ET.Element, metadata: dict) -> None:
    persistent = metadata.get("persistent_id")
    if not persistent:
        return
    xml_utils.sub_element(
        element,
        "PersistentID",
        {"Identifier": persistent.get("identifier"), "Context": persistent.get("context")},
    )


def _append_placement(element: ET.Element, placement: DexpiPlacement | None, raw: ET.Element | None) -> None:
    """The item's schematic geometry: where its symbol sits on the sheet.

    ``<Position>`` and ``<Extent>`` are regenerated -- the model holds everything they contain --
    while a ``<PolyLine>`` is echoed from the source when there is one, because a real polyline
    carries its own extent and presentation and the model keeps only the coordinates.
    """
    if placement is None:
        return

    if placement.location is not None or placement.axis is not None or placement.reference is not None:
        position = xml_utils.sub_element(element, "Position")
        xml_utils.point_element(position, "Location", placement.location)
        xml_utils.point_element(position, "Axis", placement.axis)
        xml_utils.point_element(position, "Reference", placement.reference)

    if placement.extent_min is not None or placement.extent_max is not None:
        extent = xml_utils.sub_element(element, "Extent")
        xml_utils.point_element(extent, "Min", placement.extent_min)
        xml_utils.point_element(extent, "Max", placement.extent_max)

    source_polylines = raw.findall("PolyLine") if raw is not None else []
    if source_polylines:
        xml_utils.append_all(element, source_polylines)
    elif placement.polyline:
        polyline = xml_utils.sub_element(element, "PolyLine", {"NumPoints": len(placement.polyline)})
        for point in placement.polyline:
            xml_utils.point_element(polyline, "Coordinate", point)


def _append_associations(element: ET.Element, item: DexpiItem, echo: bool) -> None:
    """``<Association>`` links, one element per target.

    The source element is echoed when there is one, because an association may carry a ``TagName``
    the model does not keep. A model-only association with several targets becomes several
    elements, which is the only shape Proteus has for it.
    """
    for association in item.associations:
        if echo and association.raw is not None and association.raw.tag == "Association":
            element.append(xml_utils.copy_of(association.raw))
            continue
        for target in association.target_ids or [None]:
            xml_utils.sub_element(element, "Association", {"Type": association.type, "ItemID": target})


# -- connection points ------------------------------------------------------------------------------


def _append_connection_points(element: ET.Element, item: DexpiItem, echo: bool) -> None:
    """``<ConnectionPoints>``, with ``@NumPoints`` and the flow indices recomputed.

    ``@FlowIn``/``@FlowOut`` are **0-based positions in the node list**, exactly like a connection's
    node references -- see :func:`_node_index`. The symbol anchor keeps slot 0 and is written out
    like any other node; dropping it would renumber every process connection on the item.
    """
    if not item.nodes:
        return

    nodes = sorted(item.nodes, key=lambda node: node.ordinal)
    # Proteus states one inlet and one outlet per item and has no way to say more, so a tee whose
    # model carries two outlets keeps the first and loses the second. Saying so beats losing it
    # silently -- the connections still hold the whole picture.
    flow: dict[str, int] = {}
    for index, node in enumerate(nodes):
        if node.flow not in ("in", "out"):
            continue
        if node.flow in flow:
            logger.warning(
                "%s: node %r is a second %r node; Proteus @Flow%s can only name one",
                item.id,
                node.id,
                node.flow,
                node.flow.capitalize(),
            )
            continue
        flow[node.flow] = index

    attributes: dict[str, object] = {
        "NumPoints": len(nodes),
        "FlowIn": flow.get("in"),
        "FlowOut": flow.get("out"),
    }

    source = item.raw.find("ConnectionPoints") if (echo and item.raw is not None) else None
    if source is not None:
        for key in sorted(source.attrib):
            if key not in _RECOMPUTED_POINT_ATTRIBUTES:
                attributes[key] = source.get(key)

    points = xml_utils.sub_element(element, "ConnectionPoints", attributes)
    for node in nodes:
        points.append(_node_element(node, echo))

    # A source <ConnectionPoints> may carry an extent, a presentation or attributes of its own.
    if source is not None:
        xml_utils.append_all(points, [child for child in source if child.tag != "Node"])


def _node_element(node: DexpiNode, echo: bool) -> ET.Element:
    """One ``<Node>``.

    Echoed from the source when the document came from Proteus, because a node may carry a nominal
    diameter element, a name and attributes the model reduces to a tag and a diameter. The ID is
    written explicitly even where the source omitted it: the reader had to synthesize one, and
    stating it is what lets the next reader agree.
    """
    if echo and node.raw is not None and node.raw.tag == "Node":
        element = xml_utils.copy_of(node.raw)
        element.set("ID", node.id)
        if node.node_type is not None:
            element.set("Type", node.node_type)
        return element

    element = xml_utils.element("Node", {"ID": node.id, "Type": node.node_type})
    _append_generic_attributes(element, _node_attributes(node))
    if node.position is not None or node.direction is not None:
        position = xml_utils.sub_element(element, "Position")
        xml_utils.point_element(position, "Location", node.position)
        xml_utils.point_element(position, "Reference", node.direction)
    return element


def _node_attributes(node: DexpiNode) -> list[DexpiAttribute]:
    """The node facts the model keeps, back in the form the reader extracted them from.

    The diameter is written as a DN designator, which is how it was stored: the model holds metres
    and DN is the metric designator read as millimetres, so ``0.08`` goes back out as ``DN 80``.
    """
    out: list[DexpiAttribute] = []
    if node.tag:
        out.append(
            DexpiAttribute(
                name=attribute_lookup.SUB_TAG_NAME,
                value=node.tag,
                format="string",
                set_name=DEXPI_ATTRIBUTE_SET,
            )
        )
    if node.nominal_diameter is not None:
        out.append(
            DexpiAttribute(
                name=attribute_lookup.NOMINAL_DIAMETER_NUMERICAL_VALUE_REPRESENTATION,
                value=xml_utils.format_float(node.nominal_diameter * 1000.0),
                format="string",
                set_name=DEXPI_ATTRIBUTE_SET,
            )
        )
        out.append(
            DexpiAttribute(
                name=attribute_lookup.NOMINAL_DIAMETER_TYPE_REPRESENTATION,
                value="DN",
                format="string",
                set_name=DEXPI_ATTRIBUTE_SET,
            )
        )
    return out


# -- connections ------------------------------------------------------------------------------------


def _connection_element(doc: DexpiDocument, connection: DexpiConnection) -> ET.Element:
    return xml_utils.element(
        "Connection",
        {
            "FromID": connection.from_item,
            "FromNode": _node_index(doc, connection.from_item, connection.from_node),
            "ToID": connection.to_item,
            "ToNode": _node_index(doc, connection.to_item, connection.to_node),
        },
    )


def _node_index(doc: DexpiDocument, item_id: str | None, node_id: str | None) -> str | None:
    """The **0-based** position of ``node_id`` in ``item_id``'s connection points.

    Slot 0 is the symbol anchor. A nozzle whose nodes are ``[Nozzle-1-DefaultNode, PipingNode-1]``
    therefore addresses its process connection as ``1``, which is what every file in the official
    DEXPI 1.3 corpus writes and what :func:`ada.cadit.dexpi.read.read_proteus._lookup` reads first.

    Do not "fix" this to 1-based. The resulting files would round-trip through adapy's own reader
    without complaint -- index 1 resolves for any node list -- while pointing every connection in
    the document at a symbol anchor for everyone else.
    """
    if item_id is None or node_id is None:
        return None

    item = doc.items.get(item_id)
    if item is None:
        logger.warning("connection references item %r, which the document does not hold", item_id)
        return None

    for index, node in enumerate(sorted(item.nodes, key=lambda candidate: candidate.ordinal)):
        if node.id == node_id:
            return str(index)

    logger.warning("connection references node %r, which item %r does not have", node_id, item_id)
    return None
