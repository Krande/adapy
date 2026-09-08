"""The neutral in-memory DEXPI model.

DEXPI ships in two incompatible serializations -- Proteus XML (1.3/1.4) and DEXPI XML (2.0.0) --
that carry the same plant semantics. Both readers produce the structures below, and both writers
consume them, so everything between the readers and the writers is written once.

This is a near-complete parse, not a lossy summary. Constructs adapy has no concept of still land
here, and every item keeps the ``ET.Element`` it came from in :attr:`DexpiItem.raw`, so the writer
can echo them back verbatim rather than silently dropping a third of the source document.

:class:`DexpiDocument` indexes items **flat**, by ID, with the tree recoverable from ``parent_id``
and ``child_ids``. DEXPI connectivity is by ID across nesting levels -- a connection names a nozzle
three levels inside an equipment -- so a flat index is what every consumer of this model wants.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum

from . import attributes as attribute_lookup
from . import class_table

__all__ = [
    "DexpiAssociation",
    "DexpiAttribute",
    "DexpiConnection",
    "DexpiDocument",
    "DexpiFlavour",
    "DexpiHeader",
    "DexpiItem",
    "DexpiNode",
    "DexpiPlacement",
    "ItemKind",
    "classify",
]


class DexpiFlavour(str, Enum):
    """Which serialization a document is in."""

    PROTEUS = "proteus"
    DEXPI20 = "dexpi20"


class ItemKind(str, Enum):
    """What an item *is*, coarse enough for the importer to branch on.

    Assigned by :func:`classify` from the DEXPI supertype graph rather than from a list of class
    names, so a class the spec added yesterday still lands in the right bucket.
    """

    MODEL = "model"
    EQUIPMENT = "equipment"
    CHAMBER = "chamber"
    NOZZLE = "nozzle"
    PIPING_SYSTEM = "piping_system"
    PIPING_SEGMENT = "piping_segment"
    PIPING_COMPONENT = "piping_component"
    OFF_PAGE_CONNECTOR = "off_page_connector"
    INSTRUMENTATION = "instrumentation"
    PLANT_STRUCTURE = "plant_structure"
    PRESENTATION = "presentation"
    OTHER = "other"


def classify(class_name: str) -> ItemKind:
    """Map a DEXPI class name to its :class:`ItemKind`.

    Order matters: the specific tests come before the general ones, because a ``Nozzle`` is also a
    ``PipingNodeOwner`` and a ``FlowInPipeOffPageConnector`` is also a ``PipingNetworkSegmentItem``.
    An unknown class -- a vendor extension, say -- falls through to ``OTHER``.
    """
    is_a = class_table.is_a
    name = class_table.resolve(class_name)

    if is_a(name, "PipeOffPageConnector") or is_a(name, "SignalOffPageConnector"):
        return ItemKind.OFF_PAGE_CONNECTOR
    if is_a(name, "Nozzle"):
        return ItemKind.NOZZLE
    if is_a(name, "Chamber"):
        return ItemKind.CHAMBER
    if is_a(name, "PipingNetworkSystem"):
        return ItemKind.PIPING_SYSTEM
    if is_a(name, "PipingNetworkSegment"):
        return ItemKind.PIPING_SEGMENT
    if is_a(name, "PipingComponent"):
        return ItemKind.PIPING_COMPONENT
    if is_a(name, "ProcessEquipment"):
        return ItemKind.EQUIPMENT
    if is_a(name, "PlantModel") or is_a(name, "EngineeringModel"):
        return ItemKind.MODEL

    entry = class_table.get(name)
    package = entry["package"] if entry else ""
    if package == "Plant/Instrumentation":
        return ItemKind.INSTRUMENTATION
    if package == "Plant/PlantStructure":
        return ItemKind.PLANT_STRUCTURE
    if package.endswith("Diagram"):
        return ItemKind.PRESENTATION
    return ItemKind.OTHER


@dataclass
class DexpiAttribute:
    """One attribute value: a Proteus ``<GenericAttribute>`` or a DEXPI 2.0 ``<Data property>``.

    ``name`` is the attribute's own name (``TagNameAssignmentClass`` or ``TagName``); ``uri`` is
    the RDL reference for it when the emitter wrote one. ``value_uri`` carries the RDL reference of
    an *enumerated* value, which Proteus writes alongside the human-readable ``value``.
    """

    name: str
    value: str | None = None
    uri: str | None = None
    format: str | None = None
    units: str | None = None
    units_uri: str | None = None
    value_uri: str | None = None
    language: str | None = None
    set_name: str | None = None


@dataclass
class DexpiNode:
    """A connection point on an item -- a Proteus ``<Node>`` or a DEXPI 2.0 ``PipingNode``.

    ``ordinal`` is the 1-based position of the node within its owner. Proteus connections address
    nodes *by that ordinal*, so it is the reader's job to keep it exact; it is also the only node
    identity that survives a flavour conversion, which is what ``canonical.graph_signature`` leans
    on.

    ``is_anchor`` marks the symbol's own anchor point rather than a real process connection. It
    still occupies an ordinal and must never be renumbered away -- it is only excluded when
    generating adapy ports.
    """

    id: str
    ordinal: int
    owner_id: str | None = None
    node_type: str | None = None
    is_anchor: bool = False
    flow: str | None = None
    position: tuple[float, float, float] | None = None
    direction: tuple[float, float, float] | None = None
    tag: str | None = None
    nominal_diameter: float | None = None
    raw: ET.Element | None = field(default=None, repr=False)


@dataclass
class DexpiPlacement:
    """Where an item sits in the *schematic* frame, in the document's own units.

    This is drawing geometry, never plant geometry: DEXPI 2D coordinates are millimetres on a
    sheet. adapy keeps it for round-tripping and uses it at most as a tie-break hint.
    """

    location: tuple[float, float, float] | None = None
    axis: tuple[float, float, float] | None = None
    reference: tuple[float, float, float] | None = None
    extent_min: tuple[float, float, float] | None = None
    extent_max: tuple[float, float, float] | None = None
    polyline: list[tuple[float, float, float]] = field(default_factory=list)


@dataclass
class DexpiAssociation:
    """A non-composition link between two items (Proteus ``<Association>``, DEXPI 2.0
    ``<References>``): the referencing item, the role it plays, and what it points at."""

    type: str
    owner_id: str
    target_ids: list[str] = field(default_factory=list)
    raw: ET.Element | None = field(default=None, repr=False)


@dataclass
class DexpiItem:
    """Any addressable object in the document, from the plant model down to a single label."""

    id: str
    class_name: str
    kind: ItemKind = ItemKind.OTHER
    class_uri: str | None = None
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)
    composition_role: str | None = None
    nodes: list[DexpiNode] = field(default_factory=list)
    attributes: list[DexpiAttribute] = field(default_factory=list)
    associations: list[DexpiAssociation] = field(default_factory=list)
    placement: DexpiPlacement | None = None
    metadata: dict = field(default_factory=dict)
    raw: ET.Element | None = field(default=None, repr=False)

    @property
    def tag(self) -> str | None:
        """The item's tag name, or its sub-tag for a nozzle or chamber."""
        return attribute_lookup.tag_of(self)

    @property
    def process_nodes(self) -> list[DexpiNode]:
        """Nodes that are real process connections -- everything but the symbol anchor."""
        return [node for node in self.nodes if not node.is_anchor]

    def node_by_id(self, node_id: str) -> DexpiNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def node_by_ordinal(self, ordinal: int) -> DexpiNode | None:
        for node in self.nodes:
            if node.ordinal == ordinal:
                return node
        return None

    def __repr__(self) -> str:
        return f"DexpiItem({self.id!r}, {self.class_name!r}, kind={self.kind.value}, tag={self.tag!r})"


@dataclass
class DexpiConnection:
    """One edge of the connectivity graph.

    The node references are always **resolved node IDs**. Proteus writes them as 1-based ordinals
    and emitters get that wrong in several distinct ways; normalizing at read time means no
    consumer ever has to know.
    """

    from_item: str
    from_node: str | None = None
    to_item: str | None = None
    to_node: str | None = None
    owner_id: str | None = None
    raw: ET.Element | None = field(default=None, repr=False)


@dataclass
class DexpiHeader:
    """Document-level provenance. The volatile parts of it are excluded from canonical
    comparisons -- re-exporting a file legitimately changes the timestamp."""

    originating_system: str | None = None
    originating_system_vendor: str | None = None
    originating_system_version: str | None = None
    export_date: str | None = None
    export_time: str | None = None
    schema_version: str | None = None
    units: str | None = None
    project: str | None = None
    # DEXPI 2.0 only: the model's own identity, and the ``<Import prefix source>`` declarations that
    # name the models its ``type`` references resolve against. The sources are recorded and never
    # fetched -- the prefixes are static names, answered by the vendored class table.
    model_uri: str | None = None
    imports: dict[str, str] = field(default_factory=dict)
    raw: ET.Element | None = field(default=None, repr=False)


@dataclass
class DexpiDocument:
    """A parsed DEXPI document, flavour-independent."""

    flavour: DexpiFlavour = DexpiFlavour.PROTEUS
    header: DexpiHeader = field(default_factory=DexpiHeader)
    items: dict[str, DexpiItem] = field(default_factory=dict)
    root_ids: list[str] = field(default_factory=list)
    connections: list[DexpiConnection] = field(default_factory=list)
    extras: list[ET.Element] = field(default_factory=list, repr=False)
    warnings: list[str] = field(default_factory=list)
    source: str | None = None
    raw_root: ET.Element | None = field(default=None, repr=False)

    def add(self, item: DexpiItem, parent_id: str | None = None) -> DexpiItem:
        """Register ``item``, wiring it to ``parent_id`` or recording it as a root."""
        self.items[item.id] = item
        item.parent_id = parent_id
        if parent_id is None:
            if item.id not in self.root_ids:
                self.root_ids.append(item.id)
        else:
            parent = self.items.get(parent_id)
            if parent is not None and item.id not in parent.child_ids:
                parent.child_ids.append(item.id)
        return item

    def children(self, item_id: str) -> list[DexpiItem]:
        parent = self.items.get(item_id)
        if parent is None:
            return []
        return [self.items[cid] for cid in parent.child_ids if cid in self.items]

    def ancestors(self, item_id: str) -> list[DexpiItem]:
        """Parents of ``item_id``, nearest first. Stops on a cycle rather than spinning."""
        out: list[DexpiItem] = []
        seen = {item_id}
        current = self.items.get(item_id)
        while current is not None and current.parent_id is not None:
            if current.parent_id in seen:
                break
            seen.add(current.parent_id)
            current = self.items.get(current.parent_id)
            if current is None:
                break
            out.append(current)
        return out

    def by_kind(self, kind: ItemKind) -> list[DexpiItem]:
        return [item for item in self.items.values() if item.kind is kind]

    def by_class(self, class_name: str) -> list[DexpiItem]:
        """Every item whose class is, or inherits from, ``class_name``."""
        return [item for item in self.items.values() if class_table.is_a(item.class_name, class_name)]

    def find_node(self, node_id: str) -> tuple[DexpiItem, DexpiNode] | None:
        for item in self.items.values():
            node = item.node_by_id(node_id)
            if node is not None:
                return item, node
        return None

    def __repr__(self) -> str:
        return (
            f"DexpiDocument(flavour={self.flavour.value}, items={len(self.items)}, "
            f"connections={len(self.connections)}, source={self.source!r})"
        )
