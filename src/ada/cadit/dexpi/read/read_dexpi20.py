"""Read a DEXPI XML document (DEXPI 2.0.0) into the neutral model.

DEXPI 2.0.0 kept the plant semantics of 1.3/1.4 and threw the serialization away. Where Proteus
writes one element per plant concept, DEXPI XML writes a single generic ``<Object type>`` and hangs
three kinds of child off it, and the whole vocabulary is the eleven element names below --
``DEXPI_XML_Schema.xsd`` in the specification repo defines nothing else::

    <Object id="BallValve1" type="Plant/Piping.BallValve">
      <Components property="Nodes">      <!-- composition: the object owns what is inside -->
        <Object id="PipingNode7" type="Plant/Piping.PipingNode"/>
      </Components>
      <Data property="TagName">          <!-- attribute: one typed literal, or an aggregate -->
        <String>HV-4711</String>
      </Data>
      <References property="SourceItem" objects="#Nozzle6"/>   <!-- a link, by ID -->
    </Object>

Two consequences shape everything here.

**Connectivity is by ID.** A ``Plant/Piping.Pipe`` names ``SourceItem``/``SourceNode``/
``TargetItem``/``TargetNode`` outright, so the 0-based positional trap that dominates
:mod:`~ada.cadit.dexpi.read.read_proteus` simply does not exist: strip the leading ``#`` and the
reference is a node ID. The :class:`~ada.cadit.dexpi.model.DexpiConnection` objects that come out
are nonetheless indistinguishable from the ones the Proteus reader produces, which is the whole
point of having one model behind two readers.

**Most objects are anonymous.** ``id`` is optional in the schema, and in the specification's own
reference P&ID 1314 of 1612 objects have none. Anything unreferenced -- the diagram primitives,
above all -- simply goes without, so this reader mints a deterministic ID for those it keeps.

``<Import prefix="Plant" source="https://data.dexpi.org/models/2.0.0/Plant.xml"/>`` is recorded on
the header and **never fetched**. The prefixes are static names resolved against the vendored class
table; a reader that reached for the network to parse a file would be wrong even if the certificate
on that host were valid, which it is not.
"""

from __future__ import annotations

import pathlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
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
    ItemKind,
    classify,
)

__all__ = ["read_dexpi20"]

# ``<Data property="X"><String>...</String></Data>`` -> the Proteus ``Format`` that says the same
# thing, so an attribute means one thing regardless of which flavour it was read from.
_LITERAL_FORMATS = {
    "String": "string",
    "Double": "double",
    "Integer": "integer",
    "Boolean": "boolean",
    "DateTime": "dateTime",
}

# Aggregated data types this reader understands the shape of. Anything else is flattened.
_PHYSICAL_QUANTITY = "PhysicalQuantity"
_SINGLE_LANGUAGE_STRING = "SingleLanguageString"
_MULTI_LANGUAGE_STRING = "MultiLanguageString"

# Where the document header hides in DEXPI 2.0: as ordinary <Data> on the top-level object, rather
# than in a dedicated element the way Proteus writes <PlantInformation>.
_HEADER_PROPERTIES = {
    "OriginatingSystemName": "originating_system",
    "OriginatingSystemVendorName": "originating_system_vendor",
    "OriginatingSystemVersion": "originating_system_version",
}

# https://data.dexpi.org/models/2.0.0/Plant.xml -> "2.0.0". The imports are the only place a DEXPI
# 2.0 file states which version of the specification it was written against.
_MODEL_VERSION = re.compile(r"/models/(\d+(?:\.\d+)*)/")


def read_dexpi20(source: str | pathlib.Path | ET.Element | ET.ElementTree) -> DexpiDocument:
    """Parse a DEXPI 2.0 ``<Model>`` into a :class:`DexpiDocument`.

    ``source`` is a path, an already-parsed element, or a tree. Structural problems are collected
    on ``doc.warnings`` and logged; only a document that is not DEXPI XML at all raises.
    """
    root, origin = _root_of(source)
    if _local_name(root.tag) != "Model":
        raise ValueError(f"not a DEXPI 2.0 document: root element is <{_local_name(root.tag)}>, expected <Model>")

    doc = DexpiDocument(flavour=DexpiFlavour.DEXPI20, source=origin, raw_root=root)
    doc.header = _read_header(root)
    state = _State(doc=doc)

    for child in root:
        # The imports are already on the header, and the schema-definition elements (<Package>,
        # <ConcreteClass>, ...) belong to a model that describes classes rather than a plant.
        if child.tag == "Import":
            continue
        if child.tag != "Object":
            doc.extras.append(child)
        elif _is_envelope(child.get("type") or ""):
            _read_envelope(child, None, state)
        else:
            _read_object(child, None, None, state)

    _resolve_composition_references(state)
    return doc


# -- reader state ----------------------------------------------------------------------------------


@dataclass
class _State:
    """What the recursive walk carries besides the document.

    ``minted`` backs the synthetic IDs, and ``composition_references`` defers every
    ``<ObjectReference>`` to the end of the walk, because one may name an object that has not been
    read yet.
    """

    doc: DexpiDocument
    minted: dict[str, int] = field(default_factory=dict)
    composition_references: list[tuple[str, str, str]] = field(default_factory=list)

    def mint(self, class_name: str) -> str:
        """A deterministic ID for an object that carries none: ``BallValve#auto3``.

        Numbered per class in document order, so re-reading the same file yields the same IDs and
        two documents that differ only in which objects were named do not collide.
        """
        count = self.minted.get(class_name, 0) + 1
        self.minted[class_name] = count
        return f"{class_name}#auto{count}"

    def warn(self, message: str) -> None:
        self.doc.warnings.append(message)
        logger.warning(message)


# -- sources ---------------------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    """DEXPI XML declares no namespaces, but a re-serializer may have added one anyway."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _root_of(source: str | pathlib.Path | ET.Element | ET.ElementTree) -> tuple[ET.Element, str | None]:
    if isinstance(source, ET.ElementTree):
        return source.getroot(), None
    if isinstance(source, ET.Element):
        return source, None

    path = pathlib.Path(source)
    if not path.exists():
        raise FileNotFoundError(path)
    return ET.parse(path).getroot(), str(path)


def _read_header(root: ET.Element) -> DexpiHeader:
    """Document provenance, gathered from the two places DEXPI 2.0 keeps it.

    ``<Model name uri>`` names the document; the ``<Import>`` elements name the models it was
    written against, which is the only statement of schema version a DEXPI 2.0 file makes. The
    originating system sits on the top-level object as ordinary attribute data and is read there --
    and also left on that item, so a writer echoing the item back does not have to special-case it.
    """
    header = DexpiHeader(project=root.get("name"), model_uri=root.get("uri"), raw=root)

    for element in root.findall("Import"):
        prefix = element.get("prefix")
        location = element.get("source") or ""
        if prefix:
            header.imports[prefix] = location
        version = _MODEL_VERSION.search(location)
        if version is not None and header.schema_version is None:
            header.schema_version = version.group(1)

    for element in root.findall("Object"):
        _read_header_data(element, header)

    return header


def _read_header_data(element: ET.Element, header: DexpiHeader) -> None:
    """Pull the header properties off the model envelope, however deeply it nests.

    ``header.raw`` is the ``<Model>`` element itself, so anything on the envelope this does not
    recognise is still reachable verbatim for a writer to echo back.
    """
    if not _is_envelope(element.get("type") or ""):
        return

    for data in element.findall("Data"):
        field_name = _HEADER_PROPERTIES.get(data.get("property") or "")
        if field_name is not None and getattr(header, field_name) is None:
            setattr(header, field_name, _literal_text(data))

    for components in element.findall("Components"):
        for child in components.findall("Object"):
            _read_header_data(child, header)


def _literal_text(data: ET.Element) -> str | None:
    for child in data:
        if child.tag in _LITERAL_FORMATS:
            text = (child.text or "").strip()
            return text or None
    return None


# -- objects ---------------------------------------------------------------------------------------


def _is_envelope(type_name: str) -> bool:
    """``Core/EngineeringModel`` and ``Plant/PlantModel`` are the document, not items in it.

    Proteus says the same thing with the ``<PlantModel>`` root element and its ``<PlantInformation>``
    header, neither of which becomes an item either. Keeping the DEXPI 2.0 envelope as two extra
    items would put ``EngineeringModel[]/PlantModel[]/`` in front of every path in
    :func:`~ada.cadit.dexpi.canonical.graph_signature` and break the convergence contract on the
    first equipment. So the envelope is walked through: what it composes becomes the document roots,
    and the ``property`` it was composed under -- ``TaggedPlantItems``, ``PipingNetworkSystems``,
    ``ActuatingSystems`` -- lands on ``composition_role`` where a consumer can still read it.
    """
    return classify(type_name) is ItemKind.MODEL


def _is_node(type_name: str) -> bool:
    """``Plant/Piping.PipingNode`` is a connection point, not a plant item.

    It is written as an ``<Object>`` like everything else, but it becomes a
    :class:`~ada.cadit.dexpi.model.DexpiNode` on its owner -- exactly where the Proteus reader puts
    a ``<Node>`` -- so that both flavours address connectivity the same way.
    """
    return class_table.is_a(type_name, "PipingNode")


def _is_piping_connection(type_name: str) -> bool:
    """``Pipe`` and ``DirectPipingConnection`` are edges, not items.

    They carry nothing but their four endpoint references and correspond one-for-one to a Proteus
    ``<Connection>``, which is not an item either. The ``PipingNetworkSegment`` that owns them
    repeats its own end points as references; those stay associations, because emitting them as
    connections too would double every edge in the graph.
    """
    return class_table.is_a(type_name, "PipingConnection")


def _read_object(
    element: ET.Element,
    parent_id: str | None,
    role: str | None,
    state: _State,
) -> DexpiItem | None:
    """Build the item for ``<Object>`` and, recursively, for the objects composed into it.

    ``composition_role`` is the ``property`` of the ``<Components>`` the object sits in --
    ``TaggedPlantItems``, ``Segments``, ``Nozzles``, ``Chambers`` -- which is what tells a nested
    ``Chamber`` apart from a plant asset of its own. It is the DEXPI 2.0 counterpart of the Proteus
    element tag the other reader records there.
    """
    doc = state.doc
    type_name = element.get("type") or ""
    if not type_name:
        state.warn("an <Object> carries no type and cannot be classified; skipping it")
        doc.extras.append(element)
        return None

    class_name = class_table.resolve(type_name)
    item_id = element.get("id") or element.get("name") or state.mint(class_name)

    if item_id in doc.items:
        state.warn(f"duplicate object id {item_id!r}; keeping the first {doc.items[item_id].class_name}")
        return doc.items[item_id]

    item = DexpiItem(
        id=item_id,
        class_name=class_name,
        kind=classify(type_name),
        class_uri=class_table.class_uri(class_name),
        composition_role=role,
        nodes=_read_nodes(element, item_id, state),
        attributes=_read_attributes(element, item_id, state),
        associations=_read_associations(element, item_id),
        metadata=_read_metadata(element),
        raw=element,
    )
    doc.add(item, parent_id)

    _read_components(element, item_id, item_id, state)

    # A signal line is an item that is also an edge -- the Proteus <InformationFlow> holding a
    # <Connection> -- so it contributes both, with itself as the connection's owner.
    if class_table.is_a(class_name, "SignalConveyingFunction"):
        _read_connection(element, item_id, ("Source", None, "Target", None), state)

    return item


def _read_envelope(element: ET.Element, parent_id: str | None, state: _State) -> None:
    """Walk through a model envelope without making an item of it. See :func:`_is_envelope`."""
    _read_components(element, class_table.resolve(element.get("type") or ""), parent_id, state)


def _read_components(element: ET.Element, owner_id: str, parent_id: str | None, state: _State) -> None:
    """Descend into an object's composition properties.

    ``owner_id`` names the object for warnings and object references; ``parent_id`` is what the
    children are parented to, and the two differ only for a model envelope, which has children but
    is not itself an item.

    Five kinds of child never become items: the envelope (walked through), piping nodes (already
    read onto the owner's ``nodes``), presentation objects (the diagram and the shape catalogue,
    kept whole in ``extras`` and echoed back verbatim on write), piping connections (edges, see
    :func:`_is_piping_connection`) and ``<ObjectReference>`` (deferred, because it may name an
    object that has not been read yet).
    """
    doc = state.doc

    for components in element.findall("Components"):
        role = components.get("property") or ""

        for child in components:
            if child.tag == "ObjectReference":
                target = _strip_reference(child.get("object"))
                if target is None:
                    state.warn(f"{owner_id}/{role}: an <ObjectReference> names no object")
                else:
                    state.composition_references.append((owner_id, role, target))
                continue

            if child.tag != "Object":
                doc.extras.append(child)
                continue

            type_name = child.get("type") or ""
            if _is_envelope(type_name):
                _read_envelope(child, parent_id, state)
                continue
            if _is_node(type_name):
                continue
            if classify(type_name) is ItemKind.PRESENTATION:
                doc.extras.append(child)
                continue
            if _is_piping_connection(type_name):
                # The owner is the item the edge is recorded against -- the segment -- which is
                # ``parent_id`` rather than ``owner_id``, the two differing only for an envelope.
                _read_connection(child, parent_id, ("SourceItem", "SourceNode", "TargetItem", "TargetNode"), state)
                continue

            _read_object(child, parent_id, role, state)


def _read_metadata(element: ET.Element) -> dict:
    """The object facts that belong to neither the model nor the attribute list.

    ``dexpi_type`` is the qualified type verbatim, which the writer needs and ``class_name`` has
    dropped the package from; ``synthetic_id`` marks the objects this reader had to name itself, so
    a later merge does not mistake a minted ID for one the source document chose.
    """
    metadata: dict = {"dexpi_type": element.get("type")}

    name = element.get("name")
    if name is not None:
        metadata["object_name"] = name
    if element.get("id") is None and name is None:
        metadata["synthetic_id"] = True

    return metadata


# -- nodes -------------------------------------------------------------------------------------------


def _read_nodes(element: ET.Element, owner_id: str, state: _State) -> list[DexpiNode]:
    """The owner's piping nodes, in document order, ordinal 1..N.

    There is no anchor: the symbol placement DEXPI 2.0 keeps in the diagram is what Proteus folds
    into the ``-DefaultNode`` that opens its ``<ConnectionPoints>``, and that is not a connection
    point. So every node here is a process node, and ``is_anchor`` is never set.
    """
    nodes: list[DexpiNode] = []

    for components in element.findall("Components"):
        for child in components.findall("Object"):
            if not _is_node(child.get("type") or ""):
                continue

            ordinal = len(nodes) + 1
            node_id = child.get("id") or f"{owner_id}#node{ordinal}"
            carrier = SimpleNamespace(attributes=_read_attributes(child, node_id, state))

            nodes.append(
                DexpiNode(
                    id=node_id,
                    ordinal=ordinal,
                    owner_id=owner_id,
                    tag=attribute_lookup.tag_of(carrier),
                    nominal_diameter=attribute_lookup.nominal_diameter_of(carrier),
                    raw=child,
                )
            )

    return nodes


# -- references and connections ------------------------------------------------------------------------


def _strip_reference(token: str | None) -> str | None:
    """``"#Nozzle6"`` -> ``"Nozzle6"``. A reference that is a qualified name is left alone."""
    if token is None:
        return None
    text = token.strip()
    return text[1:] if text.startswith("#") else text or None


def _read_associations(element: ET.Element, owner_id: str) -> list[DexpiAssociation]:
    """One :class:`DexpiAssociation` per ``<References>``, kept in document order.

    ``@objects`` is a space-separated list in the schema even though every emitter seen so far
    writes exactly one, so all of them are kept.
    """
    return [
        DexpiAssociation(
            type=reference.get("property") or "",
            owner_id=owner_id,
            target_ids=[
                target
                for target in (_strip_reference(token) for token in (reference.get("objects") or "").split())
                if target
            ],
            raw=reference,
        )
        for reference in element.findall("References")
    ]


def _reference(element: ET.Element, property_name: str, owner_id: str | None, state: _State) -> str | None:
    """The single object a ``<References property=...>`` names, or None if it names none."""
    targets: list[str] = []
    for reference in element.findall(f"References[@property='{property_name}']"):
        targets.extend(t for t in (_strip_reference(token) for token in (reference.get("objects") or "").split()) if t)

    if not targets:
        return None
    if len(targets) > 1:
        state.warn(
            f"{owner_id}/@{property_name} names {len(targets)} objects; a connection end has one, using the first"
        )
    return targets[0]


def _read_connection(
    element: ET.Element,
    owner_id: str | None,
    properties: tuple[str, str | None, str, str | None],
    state: _State,
) -> None:
    """Turn an object's endpoint references into one edge of the connectivity graph.

    ``properties`` names the four reference properties to read, because piping and signal lines
    spell them differently: a pipe connects ``SourceItem``/``SourceNode`` to ``TargetItem``/
    ``TargetNode``, while a signal line connects ``Source`` to ``Target`` with no node at either
    end. A dangling end is legal -- a segment that stops in mid-air -- and is left as None.
    """
    source_item, source_node, target_item, target_node = properties

    from_item = _reference(element, source_item, owner_id, state)
    to_item = _reference(element, target_item, owner_id, state)
    if from_item is None and to_item is None:
        return

    state.doc.connections.append(
        DexpiConnection(
            from_item=from_item,
            from_node=_reference(element, source_node, owner_id, state) if source_node else None,
            to_item=to_item,
            to_node=_reference(element, target_node, owner_id, state) if target_node else None,
            owner_id=owner_id,
            raw=element,
        )
    )


def _resolve_composition_references(state: _State) -> None:
    """Wire up the ``<ObjectReference>``s collected during the walk.

    An object reference inside a ``<Components>`` *is* a composition link -- it says "this property
    of mine is that object over there", written that way because the object is serialized
    elsewhere -- so the target becomes a child. Composition is exclusive, though, so a target that
    already has a parent is recorded as an association and flagged rather than re-parented.
    """
    doc = state.doc

    for owner_id, role, target_id in state.composition_references:
        owner = doc.items.get(owner_id)
        target = doc.items.get(target_id)

        if owner is None:
            state.warn(
                f"{owner_id}/{role}: object reference {target_id!r} is composed into something that is not an item"
            )
            continue
        if target is None:
            state.warn(f"{owner_id}/{role}: object reference {target_id!r} names an object this document does not hold")
            continue

        if target.parent_id is not None:
            state.warn(
                f"{owner_id}/{role}: object reference {target_id!r} is already composed into "
                f"{target.parent_id!r}; recording it as an association instead"
            )
            owner.associations.append(DexpiAssociation(type=role, owner_id=owner_id, target_ids=[target_id]))
            continue

        if target_id in doc.root_ids:
            doc.root_ids.remove(target_id)
        target.parent_id = owner_id
        target.composition_role = role
        if target_id not in owner.child_ids:
            owner.child_ids.append(target_id)


# -- attributes ----------------------------------------------------------------------------------------


def _read_attributes(element: ET.Element, owner_id: str, state: _State) -> list[DexpiAttribute]:
    """Every ``<Data>`` directly on ``element``, flattened to the neutral attribute list.

    DEXPI 2.0 has no attribute *sets*, so ``set_name`` stays None -- a Proteus document may hold the
    DEXPI set and a vendor's own side by side, and this format simply cannot say that.
    """
    out: list[DexpiAttribute] = []
    for data in element.findall("Data"):
        name = data.get("property") or ""
        for value in data:
            out.extend(_read_value(name, value, owner_id, state))
    return out


def _read_value(name: str, element: ET.Element, owner_id: str, state: _State) -> list[DexpiAttribute]:
    """One ``<Data>`` value. A list, because a multi-language string is several attributes."""
    if element.tag in _LITERAL_FORMATS:
        text = (element.text or "").strip()
        return [DexpiAttribute(name=name, value=text or None, format=_LITERAL_FORMATS[element.tag])]

    if element.tag == "Undefined":
        # An explicitly empty value: the emitter has the property and knows it has no value.
        return [DexpiAttribute(name=name, value=None, format="undefined")]

    if element.tag == "DataReference":
        reference = element.get("data") or ""
        return [DexpiAttribute(name=name, value=_reference_literal(reference), format="anyURI", value_uri=reference)]

    if element.tag == "AggregatedDataValue":
        return _read_aggregate(name, element, owner_id, state)

    state.warn(f"{owner_id}/{name}: <{element.tag}> is not a DEXPI 2.0 data value; ignoring it")
    return []


def _reference_literal(reference: str) -> str | None:
    """The human-readable half of a reference.

    ``Plant/Enumerations.FailActionClassification.FailClose`` -> ``FailClose``, ``#Node7`` ->
    ``Node7``. The full reference is kept alongside it on the attribute's ``value_uri``, which is
    where Proteus puts the RDL URI of an enumerated value.
    """
    token = _strip_reference(reference)
    if not token:
        return None
    return token.rsplit(".", 1)[-1]


def _read_aggregate(name: str, element: ET.Element, owner_id: str, state: _State) -> list[DexpiAttribute]:
    """An ``<AggregatedDataValue>``: a structured value rather than a literal.

    Three shapes carry meaning adapy uses and get folded into a single attribute each -- a physical
    quantity into value plus unit, and the two language strings into one attribute per language,
    which is exactly how Proteus writes them. Anything else (the diagram's points, colours and
    strokes) is flattened to dotted attribute names, so nothing is lost even where nothing is
    understood.
    """
    aggregate = class_table.resolve(element.get("type") or "")

    if aggregate == _PHYSICAL_QUANTITY:
        return [_physical_quantity(name, element)]

    if aggregate == _SINGLE_LANGUAGE_STRING:
        return [
            DexpiAttribute(
                name=name,
                value=_property_text(element, "Value"),
                format="string",
                language=_property_text(element, "Language"),
            )
        ]

    if aggregate == _MULTI_LANGUAGE_STRING:
        out: list[DexpiAttribute] = []
        for data in element.findall("Data[@property='SingleLanguageStrings']"):
            for value in data:
                out.extend(_read_value(name, value, owner_id, state))
        return out

    out = []
    for data in element.findall("Data"):
        nested = data.get("property") or ""
        for value in data:
            out.extend(_read_value(f"{name}.{nested}" if nested else name, value, owner_id, state))
    return out


def _physical_quantity(name: str, element: ET.Element) -> DexpiAttribute:
    """``<AggregatedDataValue type="Core/PhysicalQuantities.PhysicalQuantity">`` -> value + unit.

    The unit is a reference into a ``Core/PhysicalQuantities`` enumeration
    (``...LengthUnit.Millimetre``); the member name goes on ``units`` and the whole reference on
    ``units_uri``, both of which :func:`ada.cadit.dexpi.units.normalize_unit` accepts. No conversion
    happens here -- adapy converts where it models the quantity and echoes the original elsewhere.
    """
    unit = _reference_of(element, "Unit")
    return DexpiAttribute(
        name=name,
        value=_property_text(element, "Value"),
        format="double",
        units=_reference_literal(unit) if unit else None,
        units_uri=unit or None,
    )


def _property_text(element: ET.Element, property_name: str) -> str | None:
    for data in element.findall(f"Data[@property='{property_name}']"):
        text = _literal_text(data)
        if text is not None:
            return text
    return None


def _reference_of(element: ET.Element, property_name: str) -> str | None:
    for data in element.findall(f"Data[@property='{property_name}']"):
        for child in data.findall("DataReference"):
            return child.get("data")
    return None
