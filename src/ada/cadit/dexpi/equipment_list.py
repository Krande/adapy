"""The equipment definition list: user overrides on top of the class defaults.

The class defaults get a DEXPI import off the ground; real work needs the real vessel. This module
is where an engineer's own sizes and nozzle positions come in, keyed by **tag** (that vessel) or by
**DEXPI class** (every vessel of that kind), in either of two on-disk forms:

* **JSON** -- ``{slug or tag: EquipmentTypeDoc}``, the catalog document shape exactly as
  ``ada.comms.rest.catalog`` validates it.
* **XLSX** -- an ``EquipmentTypes`` sheet and a sibling ``Nozzles`` sheet joined on ``SLUG``, via
  the same :class:`~ada.serialize.xlsx.WorkbookSerializer` the procedural workbook uses. Two sheets
  rather than one JSON port column, because a single cell holding a JSON array is unusable for
  hand-editing the fifteen nozzles of a separator.

Both collapse to a plain ``{slug: doc}`` dict, and **a dict's** ``.get`` **is already a valid**
``equipment_resolver``: ``ProceduralBuilder`` and ``compile_procedural_doc`` take a duck-typed
``Callable[[str], dict | None]``. There is no new plumbing here, and none is needed --
:func:`dexpi_equipment_resolver` is one line over :func:`merge_definitions`.

Precedence is **per-tag override -> per-class override -> class default**, applied field by field.
An override that names only a ``bbox`` keeps the generated nozzles, and they are generated against
the *overridden* envelope, not the default one. An override that names ``ports`` replaces the
generated list outright, which is the point of writing one.
"""

from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass
from typing import Annotated, Any, Callable, ClassVar, Literal

from pydantic import BaseModel, Field

from ada.core.catalog_docs import validate_equipment_doc
from ada.core.text_utils import slugify
from ada.serialize.xlsx import WorkbookSerializer

from . import attributes, class_table
from .equipment_defaults import build_default_doc
from .model import DexpiDocument, DexpiItem, ItemKind
from .nozzle_placers import NozzleSpec, nozzle_from_item, nozzle_from_node, port_names
from .read.connectivity import ConnectionIndex
from .read.naming import unique_name

__all__ = [
    "EquipmentTypeRow",
    "NozzleRow",
    "ResolvedEquipment",
    "branch_points",
    "connection_flow",
    "instrument_items",
    "operated_component",
    "signal_lines",
    "signal_terminal",
    "definition_slug",
    "dexpi_equipment_resolver",
    "equipment_items",
    "load_equipment_definitions",
    "merge_definitions",
    "nozzle_specs_for",
    "resolve_equipment",
    "write_equipment_definitions",
]

#: Document keys carried by their own workbook column, sheet column -> document key. Anything else
#: a JSON definition puts on a document survives a JSON round-trip (``extra="allow"``) but not an
#: xlsx one -- the sheet has no column for it. Said out loud rather than patched over with a
#: catch-all JSON cell, which would reintroduce exactly the unreadable blob the two-sheet layout
#: exists to avoid.
_DOC_EXTRAS = {
    "DEXPI_CLASS": "dexpi_class",
    "DEXPI_ID": "dexpi_id",
    "TAG": "tag",
    "NOZZLE_LAYOUT": "nozzle_layout",
}


class EquipmentTypeRow(BaseModel):
    """One row of the ``EquipmentTypes`` sheet: an equipment definition without its nozzles."""

    SHEET_NAME: ClassVar[str] = "EquipmentTypes"
    ORIENTATION: ClassVar[str] = "HORIZONTAL"
    TAB_COLOR: ClassVar[str] = "4472C4"

    SLUG: Annotated[
        str, Field(description="Definition key: the equipment tag, or the DEXPI class for a class-wide override")
    ]
    LX: Annotated[float, Field(description="Envelope length along local X (m)")] = 1.0
    LY: Annotated[float, Field(description="Envelope length along local Y (m)")] = 1.0
    LZ: Annotated[float, Field(description="Envelope height along local Z (m)")] = 1.0
    MASS: Annotated[float, Field(description="Dry mass (kg)")] = 1000.0
    COG_X: Annotated[float | None, Field(description="Centre of gravity, local X (m); blank = bbox centroid")] = None
    COG_Y: Annotated[float | None, Field(description="Centre of gravity, local Y (m); blank = bbox centroid")] = None
    COG_Z: Annotated[float | None, Field(description="Centre of gravity, local Z (m); blank = bbox centroid")] = None
    IFC_ELEMENT_CLASS: Annotated[str, Field(description="IFC4 element class to export as")] = "IfcBuildingElementProxy"
    CAD_Z_UP: Annotated[bool, Field(description="Linked CAD asset is authored Z-up (adapy convention)")] = True
    NOZZLE_LAYOUT: Annotated[
        str | None, Field(description="Nozzle-layout strategy: vessel, pump, exchanger or generic")
    ] = None
    DEXPI_CLASS: Annotated[str | None, Field(description="DEXPI class this definition came from")] = None
    DEXPI_ID: Annotated[str | None, Field(description="ID of the source DEXPI item")] = None
    TAG: Annotated[str | None, Field(description="Equipment tag as the P&ID spells it")] = None


class NozzleRow(BaseModel):
    """One row of the ``Nozzles`` sheet: one port of the ``EquipmentTypes`` row with the same
    ``SLUG``. Positions and directions are equipment-local, in the frame the envelope defines --
    origin at the footprint centre of the base."""

    SHEET_NAME: ClassVar[str] = "Nozzles"
    ORIENTATION: ClassVar[str] = "HORIZONTAL"
    TAB_COLOR: ClassVar[str] = "70AD47"

    SLUG: Annotated[str, Field(description="Equipment definition this nozzle belongs to")]
    NAME: Annotated[
        str, Field(description="Port name; unique within the equipment, and what a system connection names")
    ]
    X: Annotated[float, Field(description="Local position X (m)")] = 0.0
    Y: Annotated[float, Field(description="Local position Y (m)")] = 0.0
    Z: Annotated[float, Field(description="Local position Z (m)")] = 0.0
    DX: Annotated[float, Field(description="Outward direction X")] = 0.0
    DY: Annotated[float, Field(description="Outward direction Y")] = 0.0
    DZ: Annotated[float, Field(description="Outward direction Z")] = 1.0
    DIRECTION: Annotated[Literal["IN", "OUT", "INOUT"], Field(description="Flow direction")] = "INOUT"
    CATEGORY: Annotated[
        Literal["process", "electrical", "signal"],
        Field(description="Service carried. A mismatch here silently drops the whole system at compile time"),
    ] = "process"
    TAG: Annotated[str | None, Field(description="Nozzle tag as the P&ID spells it")] = None
    NOMINAL_DIAMETER: Annotated[float | None, Field(description="Nominal diameter in METRES (DN 100 -> 0.1)")] = None
    SPEC: Annotated[str | None, Field(description="Piping class / specification code")] = None


def definition_slug(item: DexpiItem) -> str:
    """The catalog slug for a DEXPI item: its tag, slugified.

    This is what lands in ``TopoEquipment.DESCRIPTION`` and what the equipment resolver is asked
    for, so it has to be derivable from the item alone. An untagged item -- rare, but real files
    have them -- falls back to its class and ID, which is at least stable across re-imports.
    """
    return slugify(item.tag or "") or slugify(f"{class_table.resolve(item.class_name)}-{item.id}")


#: Proteus element tag that declares a plant asset regardless of what its ``ComponentClass`` says.
#: See :func:`is_equipment` for why the tag has to be consulted at all.
_PROTEUS_EQUIPMENT_TAG = "Equipment"


def is_equipment(item: DexpiItem) -> bool:
    """Is ``item`` a plant asset an equipment definition is wanted for?

    The primary rule is ``is_a(cls, "ProcessEquipment")``, which is what the class hierarchy
    demands: there is no DEXPI class called ``Equipment``, and ``Chamber``/``Nozzle`` come straight
    off ``Core/ConceptualObject`` rather than off ``ProcessEquipment``.

    That rule alone is not enough for real files, though, and the official corpus is emphatic about
    it. The vendored class table is generated from the **DEXPI 2.0.0** specification, while a
    DEXPI 1.2 export names its equipment out of the emitter's own symbol library --
    ``ComponentClass="Pumps"``, ``"VerticalDrums"``, ``"Shell&TubeExchangers"``. None of those are
    DEXPI classes, so ``is_a`` answers False for every one and a P&ID full of equipment resolves to
    no equipment at all: 72 of the 220 official test cases, including ``C01 the complete DEXPI
    PnID``, produced an empty layout and then a hard error from the procedural builder.

    In those files the Proteus element tag *is* the statement of intent -- the emitter wrote
    ``<Equipment>`` -- so it is honoured when the class is not recognisable. ``Chamber`` and
    ``Nozzle`` are excluded explicitly rather than by omission, because Proteus spells a chamber
    ``<Equipment ComponentClass="Chamber">`` and it would otherwise be promoted to an asset of its
    own by the very tag that is supposed to identify its owner.

    An unknown class still resolves to a physical envelope:
    :func:`~ada.cadit.dexpi.equipment_defaults.resolve_defaults` reports ``source="fallback"`` and
    hands back the ``ProcessEquipment`` catch-all, which is the honest answer for a vendor class
    nothing in the table describes.
    """
    if class_table.is_a(item.class_name, "ProcessEquipment"):
        return True
    if item.composition_role != _PROTEUS_EQUIPMENT_TAG:
        return False
    return item.kind not in (ItemKind.CHAMBER, ItemKind.NOZZLE)


def equipment_items(doc: DexpiDocument) -> list[DexpiItem]:
    """The items an equipment definition is wanted for, in ID order.

    Membership is :func:`is_equipment`. Equipment nested inside other equipment is skipped -- it is
    folded into its owner, the same way a chamber is -- and the nesting test uses the same
    predicate, or an item under a legacy-classed owner would escape the fold.
    """
    out = [item for item in doc.items.values() if is_equipment(item)]
    owned = {item.id for item in out if any(is_equipment(a) for a in doc.ancestors(item.id))}
    return sorted((item for item in out if item.id not in owned), key=lambda item: item.id)


def branch_points(doc: DexpiDocument) -> dict[str, list[str]]:
    """Branch points: in-line fitting ID -> the IDs of the segments meeting at it, in ID order.

    A ``PipeTee`` (or any other passive fitting) that more than one ``PipingNetworkSegment`` names
    as a connection end is a junction, and it is a *different* problem from "no branch support". The
    3-way junction itself genuinely cannot be one routed system -- ``ada.topology.routing`` has no
    such concept, and one system per segment is the answer to that. But each individual run into or
    out of the tee is an ordinary two-ended run; it only failed to import because its end named a
    fitting, which is neither a nozzle nor an equipment. So the importer materialises the fitting as
    a small equipment with a port per connection node and all of them resolve.

    A fitting referenced by exactly one segment is **not** a junction: it is an ordinary in-line
    component the run passes through, and it stays interior to that run (carried as metadata, or as
    its own equipment under ``inline_components="equipment"``).

    Lives here, next to :func:`equipment_items`, because the rule has to be *identical* on both
    sides: the importer places these as equipment and the merge writer has to recognise the same
    ones on the way back out, or a round-trip rewrites a ``PipeTee`` as a new ``ProcessEquipment``.
    """
    index = ConnectionIndex.from_document(doc)
    owners: dict[str, set[str]] = {}
    for owner in doc.by_kind(ItemKind.PIPING_SEGMENT):
        for item_id, _node_id, _role in index.ends_of(owner.id):
            item = doc.items.get(item_id)
            if item is not None and item.kind is ItemKind.PIPING_COMPONENT:
                owners.setdefault(item.id, set()).add(owner.id)
    return {item_id: sorted(segments) for item_id, segments in sorted(owners.items()) if len(segments) > 1}


#: The two association types a signal-carrying item uses to name its ends. DEXPI states signal
#: connectivity this way -- 121 "has logical start" and 118 "has logical end" across the official
#: corpus -- rather than with the ``<Connection>`` elements piping uses. A ``SignalConveyingFunction``
#: is a *function*, not a pipe: what it joins is stated logically, and the drawing's signal line is
#: presentation.
SIGNAL_START = "has logical start"
SIGNAL_END = "has logical end"

#: Instrumentation classes that are a physical thing to place, as opposed to a function, a grouping
#: or a line. ``InstrumentationLoopFunction`` is deliberately absent: a loop is a collection of the
#: others, and materialising it would put a box in the model for something that is not an object.
_PLACEABLE_INSTRUMENTS = (
    "ProcessInstrumentationFunction",
    "ActuatingSystem",
    "ControlledActuator",
    "ProcessSignalGeneratingSystem",
    "ProcessSignalGeneratingFunction",
    # The actuating counterpart of ProcessSignalGeneratingFunction, and placeable for the same
    # reason: a loop function owns both the element that senses and the one that acts, the signal
    # line runs between them, and treating only the sensing half as a device leaves every
    # controller-to-valve line with both ends on one object.
    "ActuatingFunction",
    "Positioner",
)

#: Classes that carry a signal rather than terminate one -- these become runs, not objects.
_SIGNAL_CARRIERS = (
    "SignalConveyingFunction",
    "SignalLineFunction",
    "MeasuringLineFunction",
)


def _association_target(item: DexpiItem, association_type: str) -> str | None:
    """The first item ``item`` points at with ``association_type``, or None."""
    for association in item.associations or []:
        if association.type == association_type and association.target_ids:
            return association.target_ids[0]
    return None


def signal_lines(doc: DexpiDocument) -> dict[str, tuple[str, str]]:
    """Signal-carrying item ID -> ``(start item ID, end item ID)``, for the ones that state both.

    A ``SignalConveyingFunction`` (or a ``SignalLineFunction``/``MeasuringLineFunction``) is the
    instrumentation counterpart of a ``PipingNetworkSegment``, and it is the reason a P&ID's
    instrumentation is connectivity rather than decoration: it is what says *this controller drives
    that valve*. Its ends are :data:`SIGNAL_START`/:data:`SIGNAL_END` associations, not
    ``<Connection>`` elements -- piping and signal state their topology in two different ways, and
    reading only the piping one is why signal lines never reached the 3D model at all.

    A carrier that names fewer than two ends is omitted rather than guessed at, exactly as a
    one-ended piping segment is.
    """
    out: dict[str, tuple[str, str]] = {}
    for item in doc.items.values():
        name = class_table.resolve(item.class_name)
        if not any(class_table.is_a(name, carrier) for carrier in _SIGNAL_CARRIERS):
            continue
        start = _association_target(item, SIGNAL_START)
        end = _association_target(item, SIGNAL_END)
        if start and end and start in doc.items and end in doc.items:
            out[item.id] = (start, end)
    return dict(sorted(out.items()))


def _is_placeable_instrument(item: DexpiItem) -> bool:
    name = class_table.resolve(item.class_name)
    return any(class_table.is_a(name, cls) for cls in _PLACEABLE_INSTRUMENTS)


def signal_terminal(doc: DexpiDocument, item_id: str) -> DexpiItem | None:
    """The physical object a signal line's end refers to, or None if the document has no such item.

    A signal end frequently names a *function* rather than a thing -- an ``ActuatingFunction`` or a
    ``ProcessSignalGeneratingFunction`` nested inside the instrument that performs it. The object to
    terminate a run on is then the enclosing instrument, so this walks up to the nearest placeable
    ancestor, exactly as a ``Chamber`` folds into the equipment that owns it. Across the official
    corpus that is 35 of 236 ends; another 186 already name a placeable instrument outright.

    An end that names something outside instrumentation altogether -- a ``BallValve`` a positioner
    sits on, a ``Nozzle`` a transmitter senses at -- is returned as itself. It is a real object the
    rest of the importer already knows how to place, and the signal run should land on it rather
    than on an instrument invented to stand in for it.
    """
    item = doc.items.get(item_id)
    if item is None:
        return None
    if item.kind is not ItemKind.INSTRUMENTATION:
        return item
    for candidate in [item, *doc.ancestors(item_id)]:
        if _is_placeable_instrument(candidate):
            return candidate
    return item


def instrument_items(doc: DexpiDocument) -> list[DexpiItem]:
    """The instrumentation items to materialise as placed objects, in ID order.

    An instrument earns a body in the model when it is a placeable *thing* -- a controller, a
    transmitter, an actuator -- and either a signal line terminates on it or it is bound to the
    piping component it operates. Functions, loops and the signal lines themselves are excluded: a
    ``SignalConveyingFunction`` becomes a run, an ``InstrumentationLoopFunction`` is a grouping, and
    giving either a box would put geometry in the model for something with no physical extent.

    Nesting deliberately does **not** fold an instrument into its owner, which is the opposite of
    the rule a ``Chamber`` follows and worth spelling out. DEXPI nests the sensing element inside
    the loop function it belongs to -- ``PI 4712.01`` in the official ``C01`` file contains both its
    ``ProcessSignalGeneratingFunction`` and the ``SignalConveyingFunction`` joining that element to
    the indicator. Those are two real devices, a transmitter down at the process and an indicator in
    the control room, and the signal line between them is the whole point. Folding the child into
    the parent collapses both ends of that line onto one object and the run is then rejected for
    having no two distinct ends to route between.

    So membership is exactly "something the signal graph refers to, or something bound to a
    component it operates". A parent that is not itself an end of a line is not pulled in merely for
    owning one that is.
    """
    wanted: dict[str, DexpiItem] = {}
    for ends in signal_lines(doc).values():
        for end_id in ends:
            target = signal_terminal(doc, end_id)
            if target is not None and target.kind is ItemKind.INSTRUMENTATION and _is_placeable_instrument(target):
                wanted[target.id] = target

    for item in doc.items.values():
        if item.kind is ItemKind.INSTRUMENTATION and _is_placeable_instrument(item):
            if _association_target(item, "is fulfilled by") is not None:
                wanted[item.id] = item

    return sorted(wanted.values(), key=lambda i: i.id)


def operated_component(doc: DexpiDocument, item: DexpiItem) -> DexpiItem | None:
    """The piping component ``item`` actuates, via its ``is fulfilled by`` association.

    An ``ActuatingSystem`` on a P&ID is not free-standing: it sits on the valve it drives, and the
    association is the only statement of which one. Used to place the actuator with its valve rather
    than wherever shelf packing happens to drop it.
    """
    target_id = _association_target(item, "is fulfilled by")
    target = doc.items.get(target_id) if target_id else None
    if target is not None and target.kind is ItemKind.PIPING_COMPONENT:
        return target
    return None


def connection_flow(doc: DexpiDocument) -> dict[str, str]:
    """Nozzle/node ID -> ``"in"`` or ``"out"``, derived from the connectivity graph.

    Proteus records a node's flow direction on the node itself (``@FlowIn``/``@FlowOut``); **DEXPI
    2.0 records none at all** -- direction is implied by which end of a ``Pipe`` the node sits on.
    So the graph is asked instead: an item at the SOURCE end of a connection has fluid leaving it
    (an outlet), one at the TARGET end has fluid arriving (an inlet). Used only as the fallback for
    a node that does not declare its own flow, so a Proteus document is unaffected and a 2.0 one
    stops producing nothing but ``INOUT`` ports. The one-pass answer of
    :attr:`~ada.cadit.dexpi.read.connectivity.ConnectionIndex.flows`, for callers that hold only
    the document.
    """
    return ConnectionIndex.from_document(doc).flows


def nozzle_specs_for(doc: DexpiDocument, item: DexpiItem, flow: dict[str, str] | None = None) -> list[NozzleSpec]:
    """Every connection of ``item``, ready to place.

    Nozzles on the item's chambers are included: a separator's boot is part of the separator as far
    as 3D and routing are concerned, so its nozzles belong on the same box. Nested *equipment* is
    not descended into -- that gets its own definition. An item that carries its connection nodes
    directly, with no ``Nozzle`` children at all, contributes those nodes instead, so a piping-only
    file still yields ports.

    ``flow`` is the :func:`connection_flow` fallback for nozzles whose nodes do not declare a
    direction of their own.
    """
    specs: list[NozzleSpec] = []
    flow = flow or {}
    spec_code = attributes.spec_of(item)
    for owner in _owned(doc, item):
        children = [child for child in doc.children(owner.id) if class_table.is_a(child.class_name, "Nozzle")]
        for child in sorted(children, key=lambda child: child.id):
            specs.append(
                nozzle_from_item(
                    child,
                    spec=attributes.spec_of(child) or spec_code,
                    flow=_flow_of(flow, child.id, *(node.id for node in child.process_nodes)),
                )
            )
        if not children:
            for node in owner.process_nodes:
                specs.append(
                    nozzle_from_node(
                        node,
                        owner_class=owner.class_name,
                        spec=spec_code,
                        flow=_flow_of(flow, node.id),
                    )
                )
    return specs


def _flow_of(flow: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        if key in flow:
            return flow[key]
    return None


def _owned(doc: DexpiDocument, item: DexpiItem) -> list[DexpiItem]:
    """``item`` plus its chambers, depth-first and ID-ordered. Chambers may nest."""
    out = [item]
    for child in sorted(doc.children(item.id), key=lambda child: child.id):
        if class_table.is_a(child.class_name, "Chamber"):
            out.extend(_owned(doc, child))
    return out


@dataclass(frozen=True)
class ResolvedEquipment:
    """One DEXPI item and everything the importer needs to place and wire it.

    ``slug`` is the catalog key (and so the value of ``TopoEquipment.DESCRIPTION``); ``doc`` is the
    validated catalog document; ``ports`` maps each nozzle's DEXPI ID to the port name the document
    actually carries. That last map is the reason this exists rather than a bare ``{slug: doc}``:
    a connection in the P&ID names a *nozzle*, a run in adapy names a *port*, and only the placer
    knows which name a nozzle ended up with once duplicate tags were deduplicated.
    """

    item: DexpiItem
    slug: str
    doc: dict
    ports: dict[str, str]


def resolve_equipment(dexpi_doc: DexpiDocument, overrides: Any = None) -> list[ResolvedEquipment]:
    """Resolve every equipment in ``dexpi_doc``, in the order :func:`equipment_items` gives them.

    ``overrides`` is a definition list, in any of four forms:

    * **None** -- the shipped class defaults answer for everything.
    * **a path** to a ``.json`` or ``.xlsx``/``.xlsm`` definition list.
    * **a dict** ``{tag or class: document}``, already loaded.
    * **a callable** ``(item) -> document | None`` -- the escape hatch, for a definition that has to
      be *computed* rather than tabulated: looked up in a vendor database, derived from an attribute
      the table has no column for, or generated. It is asked once per equipment item and returns
      that item's document, or None to fall through to the shipped class default. Its return value
      is validated exactly like a table entry, so a computed definition cannot fail later inside the
      compiler where the cause would be much harder to see.

    For the table forms, keys are matched against the item's tag first and its DEXPI class second,
    so a per-tag entry wins over a per-class one, and both win over the class default. A callable
    has no such precedence to resolve: it already sees the item and can decide for itself.

    Every document is validated on the way out, including the port-name uniqueness the catalog
    enforces, so nothing that leaves here can fail later inside the compiler.
    """
    table, resolver = _as_definitions(overrides)
    flow = connection_flow(dexpi_doc)
    out: list[ResolvedEquipment] = []
    used: set[str] = set()

    for item in equipment_items(dexpi_doc):
        class_name = class_table.resolve(item.class_name)
        tag = item.tag
        # Two items sharing a tag is a real (if sloppy) thing in P&ID files, and the catalog is
        # keyed by slug, so the second must not overwrite the first.
        slug = unique_name(definition_slug(item), used, fallback="equipment")

        if resolver is not None:
            per_tag = _as_definition_document(resolver(item), item)
            per_class = {}
        else:
            per_class = _lookup(table, class_name, item.class_name)
            per_tag = _lookup(table, tag, slug)

        specs = nozzle_specs_for(dexpi_doc, item, flow)
        # Geometry first: ports are generated against the FINAL envelope, so an override that
        # resizes the box without listing ports still gets nozzles that sit on it.
        doc = build_default_doc(
            class_name,
            specs,
            bbox=_first(per_tag, per_class, "bbox"),
            strategy=_first(per_tag, per_class, "nozzle_layout"),
            tag=tag,
            dexpi_id=item.id,
        )
        ports = port_names(specs)
        for override in (per_class, per_tag):
            doc.update(copy.deepcopy(override))
            # An override that supplies its own ports replaces the generated list outright, so the
            # generated nozzle map no longer describes the document; fall back to matching a
            # nozzle to the port carrying its tag, and report nothing where even that fails.
            if override.get("ports") is not None:
                ports = _ports_by_tag(specs, doc.get("ports") or [])
        out.append(ResolvedEquipment(item=item, slug=slug, doc=validate_equipment_doc(doc), ports=ports))
    return out


def _ports_by_tag(specs: list[NozzleSpec], ports: list[dict]) -> dict[str, str]:
    """Nozzle ID -> port name, matched on tag then on name, for a hand-written port list.

    A definition list that spells its own ports out is authoritative, and its author is free to
    name them anything; this is the best honest guess at which nozzle each one answers for. A
    nozzle with no match is simply absent, which surfaces as a reported endpoint rather than a
    connection to a port that does not exist.
    """
    by_tag = {str(port.get("tag")): port["name"] for port in ports if port.get("tag")}
    by_name = {port["name"]: port["name"] for port in ports}
    out: dict[str, str] = {}
    for spec in specs:
        match = by_tag.get(str(spec.tag)) if spec.tag else None
        match = match if match is not None else by_name.get(spec.name)
        if match is not None:
            out[spec.id] = match
    return out


def merge_definitions(dexpi_doc: DexpiDocument, overrides: Any = None) -> dict[str, dict]:
    """Every equipment in ``dexpi_doc`` as a catalog, ``{slug: document}``.

    The catalog shape :class:`~ada.topo_model.builder.ProceduralBuilder` resolves against; see
    :func:`resolve_equipment` for the same answer with the source item and the nozzle-to-port map
    still attached.
    """
    return {resolved.slug: resolved.doc for resolved in resolve_equipment(dexpi_doc, overrides)}


def dexpi_equipment_resolver(dexpi_doc: DexpiDocument, overrides: Any = None) -> Callable[[str], dict | None]:
    """An ``equipment_resolver`` for ``dexpi_doc``: slug in, catalog document or None out.

    ``ProceduralBuilder.equipment_resolver`` and ``compile_procedural_doc(equipment_resolver=)`` are
    duck-typed ``Callable[[str], dict | None]``, and a dict's ``.get`` already satisfies that -- the
    postgres catalog is only one producer of such a callable, not the interface.
    """
    return merge_definitions(dexpi_doc, overrides).get


# ---------------------------------------------------------------------------
# Loading and writing
# ---------------------------------------------------------------------------
def load_equipment_definitions(path: str | pathlib.Path) -> dict[str, dict]:
    """Read a definition list from ``path``. ``.json`` and ``.xlsx``/``.xlsm`` are understood.

    Returns ``{slug or tag: document}`` with every document validated -- a typo in a hand-edited
    workbook is a loud error here rather than a silently wrong nozzle three steps downstream.
    """
    file = pathlib.Path(path)
    suffix = file.suffix.lower()
    if suffix == ".json":
        return _read_json(file)
    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx(file)
    raise ValueError(f"{file.name}: an equipment definition list must be .json or .xlsx, not {suffix!r}")


def write_equipment_definitions(definitions: dict[str, dict], path: str | pathlib.Path) -> None:
    """Write ``definitions`` to ``path`` as JSON or as the two-sheet workbook.

    The workbook is the editable form: dump the resolved defaults for a P&ID, hand it to the
    process engineer, read back what they corrected.
    """
    file = pathlib.Path(path)
    suffix = file.suffix.lower()
    if suffix == ".json":
        payload = {slug: validate_equipment_doc(doc) for slug, doc in sorted(definitions.items())}
        file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    if suffix in (".xlsx", ".xlsm"):
        _write_xlsx(definitions, file)
        return
    raise ValueError(f"{file.name}: an equipment definition list must be .json or .xlsx, not {suffix!r}")


def _read_json(file: pathlib.Path) -> dict[str, dict]:
    with file.open(encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        raise ValueError(
            f"{file.name}: expected an object of {{slug: equipment document}}, got a {type(payload).__name__}"
        )
    return {str(slug): validate_equipment_doc(doc) for slug, doc in payload.items()}


def _read_xlsx(file: pathlib.Path) -> dict[str, dict]:
    by_type = _serializer().read(str(file))
    ports: dict[str, list[dict]] = {}
    for row in by_type.get(NozzleRow, []):
        ports.setdefault(row.SLUG, []).append(
            {
                "name": row.NAME,
                "position": [row.X, row.Y, row.Z],
                "direction_vector": [row.DX, row.DY, row.DZ],
                "direction": row.DIRECTION,
                "category": row.CATEGORY,
                "tag": row.TAG,
                "nominal_diameter": row.NOMINAL_DIAMETER,
                "spec": row.SPEC,
            }
        )

    out: dict[str, dict] = {}
    for row in by_type.get(EquipmentTypeRow, []):
        cog = [row.COG_X, row.COG_Y, row.COG_Z]
        doc: dict = {
            "bbox": {"lx": row.LX, "ly": row.LY, "lz": row.LZ},
            "mass": row.MASS,
            "cog": [float(v) for v in cog] if all(v is not None for v in cog) else None,
            "ifc_element_class": row.IFC_ELEMENT_CLASS,
            "cad_z_up": row.CAD_Z_UP,
            "ports": ports.get(row.SLUG, []),
        }
        for column, key in _DOC_EXTRAS.items():
            value = getattr(row, column)
            if value is not None:
                doc[key] = value
        out[row.SLUG] = validate_equipment_doc(doc)
    return out


def _write_xlsx(definitions: dict[str, dict], file: pathlib.Path) -> None:
    rows: list[Any] = []
    nozzles: list[Any] = []
    for slug, raw in sorted(definitions.items()):
        doc = validate_equipment_doc(raw)
        bbox = doc.get("bbox") or {}
        cog = doc.get("cog") or [None, None, None]
        rows.append(
            EquipmentTypeRow(
                SLUG=slug,
                LX=bbox.get("lx", 1.0),
                LY=bbox.get("ly", 1.0),
                LZ=bbox.get("lz", 1.0),
                MASS=doc.get("mass", 1000.0),
                COG_X=cog[0],
                COG_Y=cog[1],
                COG_Z=cog[2],
                IFC_ELEMENT_CLASS=doc.get("ifc_element_class", "IfcBuildingElementProxy"),
                CAD_Z_UP=bool(doc.get("cad_z_up", True)),
                **{column: doc.get(key) for column, key in _DOC_EXTRAS.items()},
            )
        )
        for port in doc.get("ports") or []:
            position = port.get("position") or [0.0, 0.0, 0.0]
            direction_vector = port.get("direction_vector") or [0.0, 0.0, 1.0]
            nozzles.append(
                NozzleRow(
                    SLUG=slug,
                    NAME=port["name"],
                    X=position[0],
                    Y=position[1],
                    Z=position[2],
                    DX=direction_vector[0],
                    DY=direction_vector[1],
                    DZ=direction_vector[2],
                    DIRECTION=port.get("direction", "INOUT"),
                    CATEGORY=port.get("category", "process"),
                    TAG=port.get("tag"),
                    NOMINAL_DIAMETER=port.get("nominal_diameter"),
                    SPEC=port.get("spec"),
                )
            )
    _serializer().write([*rows, *nozzles], str(file))


def _serializer() -> WorkbookSerializer:
    serializer = WorkbookSerializer()
    serializer.register(EquipmentTypeRow)
    serializer.register(NozzleRow)
    return serializer


# ---------------------------------------------------------------------------
# Override lookup
# ---------------------------------------------------------------------------
def _as_definitions(overrides: Any) -> tuple[dict[str, dict], Callable[[DexpiItem], Any] | None]:
    """Normalise ``overrides`` to ``(table, resolver)`` -- exactly one of which is meaningful.

    A table is looked up by key; a resolver is asked per item. They are returned as a pair rather
    than collapsed into one callable so the table path keeps its tag-then-class precedence, which a
    resolver neither needs nor could express.
    """
    if overrides is None:
        return {}, None
    if isinstance(overrides, (str, pathlib.Path)):
        return load_equipment_definitions(overrides), None
    if isinstance(overrides, dict):
        return {str(key): dict(value) for key, value in overrides.items()}, None
    if callable(overrides):
        return {}, overrides
    raise TypeError(f"overrides must be a path, a dict, a callable or None, got {type(overrides).__name__}")


def _as_definition_document(value: Any, item: DexpiItem) -> dict:
    """One definition returned by a resolver, as a plain dict.

    Accepts a pydantic document (anything with ``model_dump``), a plain mapping, or None. Anything
    else is a loud error naming the item, because the alternative is a resolver quietly contributing
    nothing to a model that then comes out the wrong size.
    """
    if value is None:
        return {}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(
        f"the definitions callable returned {type(value).__name__} for {item.tag or item.id!r}; "
        "it must return an equipment document (a dict or a pydantic model) or None"
    )


def _lookup(table: dict[str, dict], *keys: str | None) -> dict:
    """The first entry matching any of ``keys``, verbatim or slugified. Empty dict if none match."""
    for key in keys:
        if not key:
            continue
        for candidate in (key, slugify(key)):
            if candidate and candidate in table:
                return table[candidate]
    return {}


def _first(per_tag: dict, per_class: dict, key: str) -> Any:
    """The value of ``key`` under the definition-list precedence: tag, then class, then nothing."""
    for override in (per_tag, per_class):
        if override.get(key) is not None:
            return override[key]
    return None
