"""Write a :class:`~ada.cadit.dexpi.model.DexpiDocument` back out as DEXPI XML (DEXPI 2.0.0).

The exact inverse of :mod:`ada.cadit.dexpi.read.read_dexpi20`. Where Proteus names a plant concept
in the element tag, DEXPI 2.0 writes one generic ``<Object type>`` and hangs composition
(``<Components property>``), attributes (``<Data property>``) and links (``<References property
objects>``) off it, so almost all of the work here is choosing the right ``property`` names.

Four things the reader established, which the writer has to honour:

* **``Object/@id`` is optional and most objects have none** -- 1314 of the 1612 in the
  specification's own reference P&ID. The reader mints ``{Class}#auto{n}`` for those and flags them
  with ``metadata["synthetic_id"]``; this writer omits the id again rather than publishing a minted
  one as though the source had authored it. The mint is deterministic in document order and this
  writer preserves that order, so the next read produces the same IDs.
* **``References/@objects`` is a space-separated list**, not a single reference.
* **A ``PipingConnection`` is an edge, never an item.** ``Pipe`` and ``DirectPipingConnection``
  carry nothing but their four endpoint references, so the connectivity graph is written back as
  ``<Object type="Plant/Piping.Pipe">`` under the owning segment's ``Connections``. The segment's
  own repeated ``SourceItem``/``TargetItem`` references stay associations and are written as such;
  emitting them as edges too would double every connection in the document.
* **A ``SignalConveyingFunction`` is both item and edge**, and its endpoints are written as
  ``Source``/``Target`` references on the object itself rather than as a separate connection.

The model envelope -- ``Core/EngineeringModel`` wrapping ``Plant/PlantModel`` -- is synthesized
here, because the reader walks through it without making items of it. Its ``Components`` properties
come back from each root item's ``composition_role``.

There is no anchor node in DEXPI 2.0: what Proteus folds into the ``-DefaultNode`` that opens every
``<ConnectionPoints>`` is a symbol placement, which this format keeps in the diagram. Anchors are
therefore dropped on the way out, which is why a converted document has one fewer node per item and
why ``graph_signature`` numbers process nodes rather than raw ordinals.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .. import attributes as attribute_lookup
from .. import class_table
from ..model import (
    DexpiAttribute,
    DexpiConnection,
    DexpiDocument,
    DexpiFlavour,
    DexpiItem,
    DexpiNode,
    ItemKind,
)
from ..read.connectivity import ConnectionIndex
from . import xml_utils

__all__ = ["write_dexpi20"]

# What a from-scratch document says it was written by; the library name and nothing else.
ORIGINATING_SYSTEM = "adapy"

# The models a plant document is written against. Recorded, never fetched -- the prefixes are
# static names answered by the vendored class table.
DEFAULT_IMPORTS = {
    "Core": "https://data.dexpi.org/models/2.0.0/Core.xml",
    "Plant": "https://data.dexpi.org/models/2.0.0/Plant.xml",
}

_ENGINEERING_MODEL = "Core/EngineeringModel"
_PLANT_MODEL = "Plant/PlantModel"
_PIPING_NODE = "Plant/Piping.PipingNode"
_PIPE = "Plant/Piping.Pipe"

# Aggregate value types, spelled as the specification's reference P&ID spells them.
_PHYSICAL_QUANTITY = "Core/PhysicalQuantities.PhysicalQuantity"
_SINGLE_LANGUAGE_STRING = "Core/DataTypes.SingleLanguageString"

# Header provenance, which DEXPI 2.0 keeps as ordinary <Data> on the model envelope.
_HEADER_PROPERTIES = (
    ("OriginatingSystemName", "originating_system"),
    ("OriginatingSystemVendorName", "originating_system_vendor"),
    ("OriginatingSystemVersion", "originating_system_version"),
)

# The Proteus ``Format`` a value was read with -> the DEXPI 2.0 literal element that says the same.
_LITERAL_TAGS = {
    "string": "String",
    "double": "Double",
    "integer": "Integer",
    "boolean": "Boolean",
    "dateTime": "DateTime",
}

# The composition property an item hangs off, when the document did not come from DEXPI 2.0 and so
# has no property name of its own to echo. Taken from the reference P&ID's own vocabulary.
_ROLE_BY_KIND = {
    ItemKind.EQUIPMENT: "TaggedPlantItems",
    ItemKind.CHAMBER: "Chambers",
    ItemKind.NOZZLE: "Nozzles",
    ItemKind.PIPING_SYSTEM: "PipingNetworkSystems",
    ItemKind.PIPING_SEGMENT: "Segments",
    ItemKind.PIPING_COMPONENT: "Items",
    ItemKind.OFF_PAGE_CONNECTOR: "Items",
    ItemKind.INSTRUMENTATION: "ProcessInstrumentationFunctions",
    ItemKind.PLANT_STRUCTURE: "TaggedPlantItems",
    ItemKind.PRESENTATION: "Diagram",
    ItemKind.OTHER: "TaggedPlantItems",
}

# The reference properties an edge owns. They are skipped when a source ``Pipe`` is echoed, because
# they are regenerated from the model.
_ENDPOINT_PROPERTIES = frozenset({"SourceItem", "SourceNode", "TargetItem", "TargetNode"})
_SIGNAL_ENDPOINT_PROPERTIES = frozenset({"Source", "Target"})


def write_dexpi20(doc: DexpiDocument) -> ET.Element:
    """Render ``doc`` as a DEXPI 2.0 ``<Model>`` element.

    The element is returned rather than written, so a caller can post-process it;
    :func:`ada.cadit.dexpi.write.write_dexpi` is the path that puts it on disk.
    """
    echo = doc.flavour is DexpiFlavour.DEXPI20
    owned = _connections_by_owner(doc)
    header = doc.header

    root = xml_utils.element("Model", {"name": header.project, "uri": header.model_uri})
    for prefix, source in (header.imports or DEFAULT_IMPORTS).items():
        xml_utils.sub_element(root, "Import", {"prefix": prefix, "source": source})

    engineering = xml_utils.sub_element(root, "Object", {"type": _ENGINEERING_MODEL})
    for property_name, field in _HEADER_PROPERTIES:
        value = getattr(header, field) or (ORIGINATING_SYSTEM if field == "originating_system" else None)
        if value is not None:
            _literal_data(engineering, property_name, value)

    conceptual = xml_utils.sub_element(engineering, "Components", {"property": "ConceptualModel"})
    plant = xml_utils.sub_element(conceptual, "Object", {"id": "PlantModel1", "type": _PLANT_MODEL})

    _append_children(plant, doc, doc.root_ids, echo, owned)
    _append_connections(plant, doc, owned.get(None, ()), echo)

    # Whatever the reader could not place -- the diagram, the shape catalogue, an object with no
    # type. It goes back inside a <Components>, which is where the reader will send it to extras
    # again; a presentation object written at document level would be read back as a plant item.
    if echo and doc.extras:
        diagram = xml_utils.sub_element(plant, "Components", {"property": "Diagram"})
        xml_utils.append_all(diagram, doc.extras)

    return root


# -- document scaffolding ---------------------------------------------------------------------------


def _connections_by_owner(doc: DexpiDocument) -> dict[str | None, list[DexpiConnection]]:
    """Group the connectivity graph by the item each edge is written inside."""
    return ConnectionIndex.from_document(doc).grouped_by_owner()


def _role(item: DexpiItem, echo: bool) -> str:
    """The ``<Components property>`` ``item`` is composed under.

    A document read from DEXPI 2.0 knows its own answer and it is echoed. Anything else -- a
    converted Proteus document, or one built in Python -- carries a Proteus element tag on
    ``composition_role``, which is not a property name, so it is derived from what the item is.
    """
    if echo and item.composition_role:
        return item.composition_role
    if item.kind is ItemKind.INSTRUMENTATION and class_table.is_a(item.class_name, "ActuatingSystem"):
        return "ActuatingSystems"

    role = _ROLE_BY_KIND.get(item.kind, "TaggedPlantItems")
    # Only the plant model itself has tagged plant items; anything nested is an item of its owner.
    return "Items" if role == "TaggedPlantItems" and item.parent_id is not None else role


def _append_children(
    parent: ET.Element,
    doc: DexpiDocument,
    ids: list[str],
    echo: bool,
    owned: dict[str | None, list[DexpiConnection]],
) -> None:
    """Write ``ids`` as ``<Components>`` blocks, one per run of items sharing a property.

    Runs rather than buckets: grouping by property would reorder the document, and the reader mints
    IDs for anonymous objects in document order, so a re-ordered write would produce a document
    that reads back with different IDs.
    """
    runs: list[tuple[str, list[DexpiItem]]] = []
    for item_id in ids:
        item = doc.items.get(item_id)
        if item is None:
            continue
        role = _role(item, echo)
        if runs and runs[-1][0] == role:
            runs[-1][1].append(item)
        else:
            runs.append((role, [item]))

    for role, items in runs:
        components = xml_utils.sub_element(parent, "Components", {"property": role})
        for item in items:
            components.append(_object_element(doc, item, echo, owned))


# -- objects ----------------------------------------------------------------------------------------


def _type_name(item: DexpiItem) -> str:
    """The qualified ``type`` for ``item``: ``Plant/ProcessEquipment.Tank``.

    The verbatim type the source wrote wins. Otherwise it is the fragment of the class table's
    model-scoped URI, which is that same qualified name; an unknown class -- a vendor extension --
    falls back to its bare name, which is at least round-trippable.
    """
    declared = item.metadata.get("dexpi_type")
    if declared:
        return declared

    uri = class_table.class_uri(item.class_name)
    if uri and "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return item.class_name


def _object_element(
    doc: DexpiDocument,
    item: DexpiItem,
    echo: bool,
    owned: dict[str | None, list[DexpiConnection]],
) -> ET.Element:
    """One ``<Object>``, with everything composed into it."""
    edges = list(owned.get(item.id, ()))
    is_signal = class_table.is_a(item.class_name, "SignalConveyingFunction")

    attributes: dict[str, object] = {}
    if not item.metadata.get("synthetic_id"):
        attributes["id"] = item.id
    attributes["type"] = _type_name(item)
    attributes["name"] = item.metadata.get("object_name")

    element = xml_utils.element("Object", attributes)

    for attribute in item.attributes:
        _append_data(element, attribute)

    for association in item.associations:
        # A signal line's own Source/Target references are regenerated from its edge below, so
        # echoing them here as well would give the next reader two associations for each.
        if is_signal and association.type in _SIGNAL_ENDPOINT_PROPERTIES:
            continue
        xml_utils.sub_element(
            element,
            "References",
            {"property": association.type, "objects": " ".join(f"#{t}" for t in association.target_ids)},
        )

    if is_signal:
        for edge in edges:
            _reference(element, "Source", edge.from_item)
            _reference(element, "Target", edge.to_item)
        edges = []

    _append_nodes(element, item, echo)
    _append_children(element, doc, item.child_ids, echo, owned)
    _append_connections(element, doc, edges, echo)

    return element


def _append_nodes(element: ET.Element, item: DexpiItem, echo: bool) -> None:
    """``<Components property="Nodes">``, anchors excluded -- DEXPI 2.0 has no such thing."""
    nodes = [node for node in sorted(item.nodes, key=lambda candidate: candidate.ordinal) if not node.is_anchor]
    if not nodes:
        return

    components = xml_utils.sub_element(element, "Components", {"property": "Nodes"})
    for node in nodes:
        components.append(_node_element(node, echo))


def _node_element(node: DexpiNode, echo: bool) -> ET.Element:
    """One ``PipingNode``.

    Echoed from the source when the document came from DEXPI 2.0, because a node object may carry
    attributes the model reduces to a tag and a diameter. The id is stated even where the source
    omitted it, so the next reader agrees with this one about what the node is called.
    """
    if echo and node.raw is not None and node.raw.tag == "Object":
        element = xml_utils.copy_of(node.raw)
        if element.get("id") is None:
            element.set("id", node.id)
        return element

    element = xml_utils.element("Object", {"id": node.id, "type": _PIPING_NODE})
    if node.tag:
        _literal_data(element, attribute_lookup.property_name(attribute_lookup.SUB_TAG_NAME), node.tag)
    if node.nominal_diameter is not None:
        # Back out as the DN designator it was read as: the model holds metres, DN is the metric
        # designator read as millimetres, so 0.08 goes out as DN 80.
        _literal_data(
            element,
            attribute_lookup.property_name(attribute_lookup.NOMINAL_DIAMETER_NUMERICAL_VALUE_REPRESENTATION),
            xml_utils.format_float(node.nominal_diameter * 1000.0),
        )
        _literal_data(
            element,
            attribute_lookup.property_name(attribute_lookup.NOMINAL_DIAMETER_TYPE_REPRESENTATION),
            "DN",
        )
    return element


# -- connections ------------------------------------------------------------------------------------


def _append_connections(
    parent: ET.Element,
    doc: DexpiDocument,
    connections,
    echo: bool,
) -> None:
    """``<Components property="Connections">`` holding one ``Pipe`` object per edge."""
    edges = list(connections)
    if not edges:
        return

    components = xml_utils.sub_element(parent, "Components", {"property": "Connections"})
    for connection in edges:
        components.append(_connection_element(connection, echo))


def _connection_element(connection: DexpiConnection, echo: bool) -> ET.Element:
    """One edge, as a ``Plant/Piping.Pipe`` object addressing its ends by ID.

    The endpoints are always regenerated from the model, so an edit to the graph lands; everything
    else the source pipe carried -- its own nominal diameter, piping class, insulation -- is echoed
    beside them.
    """
    source = connection.raw if (echo and connection.raw is not None and connection.raw.tag == "Object") else None

    attributes: dict[str, object] = {}
    if source is not None and source.get("id"):
        attributes["id"] = source.get("id")
    attributes["type"] = (source.get("type") if source is not None else None) or _PIPE

    element = xml_utils.element("Object", attributes)
    if source is not None:
        for child in source:
            if child.tag == "References" and child.get("property") in _ENDPOINT_PROPERTIES:
                continue
            element.append(xml_utils.copy_of(child))

    _reference(element, "SourceItem", connection.from_item)
    _reference(element, "SourceNode", connection.from_node)
    _reference(element, "TargetItem", connection.to_item)
    _reference(element, "TargetNode", connection.to_node)
    return element


def _reference(parent: ET.Element, property_name: str, target_id: str | None) -> None:
    if target_id is None:
        return
    xml_utils.sub_element(parent, "References", {"property": property_name, "objects": f"#{target_id}"})


# -- attribute data -----------------------------------------------------------------------------------


def _property_name(name: str) -> str:
    """``TagNameAssignmentClass`` -> ``TagName``.

    Only the RDL assignment-class suffix comes off. A DEXPI 2.0 document's property names are
    already bare and are left exactly as they are, which is what keeps a re-write lossless.
    """
    return attribute_lookup.property_name(name) if name.endswith("AssignmentClass") else name


def _literal_data(parent: ET.Element, property_name: str, value: str | None, tag: str = "String") -> ET.Element:
    data = xml_utils.sub_element(parent, "Data", {"property": property_name})
    xml_utils.text_element(data, tag, value)
    return data


def _append_data(parent: ET.Element, attribute: DexpiAttribute) -> None:
    """One ``<Data property>`` holding the value in whichever of the five shapes fits it.

    An attribute with a unit becomes a ``PhysicalQuantity`` aggregate and one with a language a
    ``SingleLanguageString``, which is exactly how the reader took them apart; the reader flattens a
    ``MultiLanguageString`` into one attribute per language, and those go back out as one
    ``SingleLanguageString`` each, which says the same thing.
    """
    data = xml_utils.sub_element(parent, "Data", {"property": _property_name(attribute.name)})

    if attribute.format == "undefined":
        xml_utils.sub_element(data, "Undefined")
        return

    if attribute.units is not None or attribute.units_uri is not None:
        aggregate = xml_utils.sub_element(data, "AggregatedDataValue", {"type": _PHYSICAL_QUANTITY})
        _literal_data(aggregate, "Value", attribute.value, "Double")
        unit = xml_utils.sub_element(aggregate, "Data", {"property": "Unit"})
        xml_utils.sub_element(unit, "DataReference", {"data": attribute.units_uri or attribute.units})
        return

    if attribute.language is not None:
        aggregate = xml_utils.sub_element(data, "AggregatedDataValue", {"type": _SINGLE_LANGUAGE_STRING})
        _literal_data(aggregate, "Value", attribute.value)
        _literal_data(aggregate, "Language", attribute.language)
        return

    if attribute.value_uri is not None and attribute.format in (None, "anyURI"):
        xml_utils.sub_element(data, "DataReference", {"data": attribute.value_uri})
        return

    # An empty literal rather than <Undefined/>: an emitter that wrote <String/> is saying the
    # property has a string value it does not know, which is a different statement from having no
    # value at all, and only the literal form reads back as the same attribute.
    xml_utils.text_element(data, _LITERAL_TAGS.get(attribute.format or "string", "String"), attribute.value)
