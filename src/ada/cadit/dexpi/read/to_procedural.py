"""Turn a parsed DEXPI P&ID into a procedural document the 3D engine can build.

This is the join between the two halves of the branch. On one side a
:class:`~ada.cadit.dexpi.model.DexpiDocument`: what the plant *is* -- which vessels, which nozzles,
which line runs between which two of them -- and nothing whatsoever about where any of it sits. On
the other side :mod:`ada.topo_model`, which needs cells, placed equipment with real 3D ports, and
two-ended systems, and then routes and models the lot. Four things happen here:

1. **Equipment.** Every item that ``is_a(cls, "ProcessEquipment")`` resolves through the definition
   list to a catalog document -- an envelope, a mass, an IFC class and a port per nozzle, placed on
   the box by the class's nozzle strategy. A ``Chamber`` is folded into its owner (a separator's
   boot is part of the separator as far as routing cares) rather than becoming an asset of its own.
2. **Layout.** The resolved envelopes go to :func:`~ada.topo_model.layout.plan_layout`, which
   generates the decks and places each item on one.
3. **Systems.** One :class:`~ada.topology.entities.TopoSystem` per DEXPI ``PipingNetworkSegment``,
   named ``<line number>/<segment number>``. This is the load-bearing structural decision:
   :func:`ada.topology.routing.route_system` routes exactly ``ports[0] -> ports[-1]`` and raises on
   fewer than two, with no branch or tee support anywhere. A DEXPI segment is by definition a
   two-ended run, so segment-per-system is both faithful to the source and routable; the parent
   ``PipingNetworkSystem`` survives as the run's ``MEDIUM`` and as provenance in its ``METADATA``.
4. **Nothing is lost quietly.** Every segment that could not be turned into a system, and every
   equipment the layout could not place, is collected into a :class:`DexpiImportReport`. The
   compiler drops an unwireable system with a ``logger.warning`` and skips an unroutable run the
   same way; a user must never be handed a model that looks finished with half the pipes missing.

**Two honesty notes, because they are easy to mistake for engineering.**

*The layout has no process sense.* It is shelf packing on physical size alone (see
:mod:`ada.topo_model.layout`): a pump can land at the far end of a deck from the vessel it feeds.
Equipment connected by a segment is packed as one group, which is a cheap first heuristic and not
a plot plan. The result is a plausible, routable arrangement to start from, not a layout.

*DEXPI schematic coordinates are never used as plant coordinates.* A P&ID's ``Position`` is
millimetres on a drawing sheet, with no relation to where anything stands. It is carried through to
metadata and used at most as a tie-break hint. Every coordinate in the generated document comes
from :func:`~ada.topo_model.layout.plan_layout`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from ada.comms.rest.catalog import slugify
from ada.topo_model.layout import (
    LayoutItem,
    LayoutRules,
    apply_layout,
    plan_layout,
    validate_equipment_in_cells,
)
from ada.topology.entities import SystemConnection, TopoSpace, TopoSystem

from .. import attributes, class_table
from ..equipment_defaults import build_default_doc
from ..equipment_list import ResolvedEquipment, connection_flow, resolve_equipment
from ..model import DexpiDocument, DexpiItem, ItemKind
from ..nozzle_placers import nozzle_from_node, port_names

__all__ = [
    "DexpiImportReport",
    "ImportIssue",
    "InlineComponents",
    "default_layout_rules",
    "dexpi_import_report",
    "dexpi_to_procedural_doc",
]

InlineComponents = Literal["metadata", "equipment"]

#: Envelope and IFC class for an in-line component materialised as its own equipment under
#: ``inline_components="equipment"``. Small and square on purpose: a valve body is a detail, and
#: giving it a considered size would be inventing data the P&ID does not hold.
_INLINE_BBOX = [0.4, 0.4, 0.4]
_INLINE_IFC = "IfcValve"

#: Height of a site terminal above the floor of the deck it is placed on, and the fraction of the
#: deck height to fall back to on a deck shallower than that.
_SITE_ELEVATION = 1.5
_SITE_ELEVATION_FRACTION = 0.4


def default_layout_rules() -> LayoutRules:
    """The layout rules a DEXPI import uses when the caller names none.

    ``deck_height`` is **explicit** and that is the whole point of this function: the pitch is
    uniform across the plan, so leaving it to "the tallest item plus headroom" lets a single 15 m
    ``ProcessColumn`` make every deck 16 m tall. A fixed 6 m storey with the column reported as
    poking through it is the more useful wrong answer.
    """
    return LayoutRules(max_length=24.0, max_width=12.0, deck_height=6.0)


# --------------------------------------------------------------------------- #
# The import report
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ImportIssue:
    """One thing the import could not carry through to the 3D model.

    ``stage`` says where it was lost -- ``connectivity`` (the P&ID endpoint does not name something
    adapy can connect to), ``layout`` (no cell would hold it), ``wiring`` (the compiler refused the
    connection) or ``routing`` (no path was found) -- because the fix differs completely between
    them.
    """

    kind: Literal["system", "equipment"]
    name: str
    stage: str
    reason: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "stage": self.stage, "reason": self.reason}


@dataclass
class DexpiImportReport:
    """What a DEXPI import dropped, and how much of it there was.

    The procedural compiler is deliberately forgiving: an unwireable system is skipped with a
    ``logger.warning`` (``ada.topo_model.compile._wire_systems``) and an unroutable run is skipped
    inside the engine (``run_design(skip_failed=True)``). Both are the right behaviour for one bad
    spec in a large document and the wrong behaviour for a user who asked for their P&ID -- so
    every one of them is collected here, attached to ``assembly.metadata["dexpi"]["report"]``, and
    summarised in a single warning line.
    """

    issues: list[ImportIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        """True when every segment routed and every equipment landed in a cell."""
        return not self.issues

    def add(self, kind: str, name: str, stage: str, reason: str) -> None:
        self.issues.append(ImportIssue(kind=kind, name=name, stage=stage, reason=reason))  # type: ignore[arg-type]

    def of_kind(self, kind: str) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.kind == kind]

    def summary(self) -> str:
        """One line naming the counts -- what the importer logs and what an exception says.

        ``stats`` counts what *did* reach the 3D model, not what was attempted, so the total an
        issue count is reported against is ``stats + dropped`` -- otherwise a document where every
        system failed reads as "N of (a smaller number)", which parses as nonsense rather than as
        a complete failure.
        """
        systems_dropped = len(self.of_kind("system"))
        equipment_dropped = len(self.of_kind("equipment"))
        systems_total = self.stats.get("systems", 0) + systems_dropped
        equipment_total = self.stats.get("equipment", 0) + equipment_dropped
        return (
            f"{systems_dropped} of {systems_total} system(s) and "
            f"{equipment_dropped} of {equipment_total} equipment did not reach the 3D model"
        )

    def as_dict(self) -> dict:
        return {"issues": [issue.as_dict() for issue in self.issues], "stats": dict(self.stats)}

    @classmethod
    def from_dict(cls, payload: dict | None) -> DexpiImportReport:
        payload = payload or {}
        return cls(
            issues=[
                ImportIssue(
                    kind=entry.get("kind", "system"),  # type: ignore[arg-type]
                    name=entry.get("name", ""),
                    stage=entry.get("stage", ""),
                    reason=entry.get("reason", ""),
                )
                for entry in payload.get("issues") or []
            ],
            stats=dict(payload.get("stats") or {}),
        )

    def format(self) -> str:
        """Render the report as an aligned console table, mirroring
        :func:`ada.api.systems.validation.format_port_report`."""
        if not self.issues:
            return f"DEXPI import complete: {self.summary()}."
        headers = ("Kind", "Name", "Stage", "Reason")
        rows = [(i.kind, i.name, i.stage, i.reason) for i in self.issues]
        widths = [max(len(headers[c]), *(len(r[c]) for r in rows)) for c in range(len(headers))]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        lines = [self.summary() + ":", "", fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
        lines.extend(fmt.format(*r) for r in rows)
        return "\n".join(lines)


def dexpi_import_report(assembly) -> str:
    """The import report of an assembly built by :func:`ada.from_dexpi`, as a console table.

    Reads ``assembly.metadata["dexpi"]["report"]``; an assembly that did not come from a DEXPI
    import reports nothing rather than raising.
    """
    payload = (assembly.metadata.get("dexpi") or {}).get("report")
    if payload is None:
        return "No DEXPI import report on this assembly."
    return DexpiImportReport.from_dict(payload).format()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@dataclass
class _Endpoint:
    """One end of a DEXPI segment, on its way to becoming a
    :class:`~ada.topology.entities.SystemConnection`."""

    item_id: str
    node_id: str | None
    role: Literal["from", "to"]
    equipment: str | None = None
    port: str | None = None
    site: str | None = None
    direction: str | None = None
    position: tuple[float, float, float] | None = None
    direction_vector: tuple[float, float, float] | None = None
    problem: str | None = None

    def to_connection(self) -> SystemConnection:
        if self.site is not None:
            return SystemConnection(
                SITE=self.site,
                POSITION=[float(v) for v in (self.position or (0.0, 0.0, 0.0))],
                DIRECTION=self.direction,  # type: ignore[arg-type]
                DIRECTION_VECTOR=[float(v) for v in (self.direction_vector or (0.0, 0.0, 1.0))],
            )
        return SystemConnection(EQUIPMENT=self.equipment, PORT=self.port)


class _Index:
    """Lookups from DEXPI identity to adapy identity, built once per import.

    ``ports`` is keyed by whatever a connection may name as its end: a ``Nozzle`` item's ID, or --
    for an item that carries its connection nodes directly, with no ``Nozzle`` children -- a node
    ID. ``owners`` maps an equipment or chamber ID to the placed equipment's name, so an endpoint
    that names the vessel rather than its nozzle still finds its host.
    """

    def __init__(self, resolved: Iterable[ResolvedEquipment], names: dict[str, str]) -> None:
        self.ports: dict[str, tuple[str, str]] = {}
        self.owners: dict[str, str] = {}
        for entry in resolved:
            name = names[entry.slug]
            self.owners[entry.item.id] = name
            for nozzle_id, port_name in entry.ports.items():
                self.ports[nozzle_id] = (name, port_name)

    def add_owner(self, item_id: str, name: str) -> None:
        self.owners[item_id] = name

    def add_port(self, key: str, equipment: str, port: str) -> None:
        self.ports[key] = (equipment, port)


# --------------------------------------------------------------------------- #
# The importer
# --------------------------------------------------------------------------- #
def dexpi_to_procedural_doc(
    doc: DexpiDocument,
    *,
    definitions: Any = None,
    layout: LayoutRules | dict | None = None,
    base_doc: dict | None = None,
    inline_components: InlineComponents = "metadata",
) -> tuple[dict, dict]:
    """Convert ``doc`` into ``(procedural document, equipment catalog)``.

    The document is the compiler's commit format (``spaces``/``equipments``/``systems``, see
    :class:`ada.comms.rest.procedural.ProceduralDoc`) and the catalog is ``{slug: equipment
    document}``; a dict's ``.get`` is already a valid ``equipment_resolver``, so the pair feeds
    :func:`ada.topo_model.compile.build_procedural_assembly` directly.

    ``definitions`` is the equipment definition list (a path to JSON/XLSX, a loaded dict, or None
    for the shipped class defaults). ``layout`` is a :class:`~ada.topo_model.layout.LayoutRules`
    or a dict of its fields; :func:`default_layout_rules` answers when it is None. ``base_doc``
    is an existing procedural document to merge into: its equipment placements win over the
    generated ones, which is what makes re-importing an edited P&ID non-destructive.

    ``inline_components`` decides what becomes of the valves, strainers and orifices inside a
    segment. ``"metadata"`` (the default) records them on the run's ``METADATA`` and models no
    geometry for them. ``"equipment"`` additionally materialises each as a small ``IfcValve``
    equipment that the layout has to place -- note that this places the component *somewhere on a
    deck*, not at its position along the run, because the run is routed by A* and has no notion of
    where its fittings sit.

    Everything the conversion could not carry lands in the report at ``document["dexpi"]["report"]``
    and is never silently dropped.
    """
    if inline_components not in ("metadata", "equipment"):
        raise ValueError(f"inline_components must be 'metadata' or 'equipment', got {inline_components!r}")

    rules = _layout_rules(layout)
    report = DexpiImportReport()

    resolved = resolve_equipment(doc, definitions)
    names = _equipment_names(resolved)
    catalog = {entry.slug: entry.doc for entry in resolved}
    index = _Index(resolved, names)
    for entry in resolved:
        for chamber in _chambers(doc, entry.item):
            index.add_owner(chamber.id, names[entry.slug])

    items = [_layout_item(entry, names[entry.slug]) for entry in resolved]
    provenance = {names[entry.slug]: _equipment_metadata(entry) for entry in resolved}

    segments = [_segment_spec(doc, item, index, report) for item in _routable_segments(doc)]
    segments = [spec for spec in segments if spec is not None]

    if inline_components == "equipment":
        items.extend(_inline_equipment(doc, segments, catalog, index, provenance, names))

    _group_by_connectivity(items, segments)
    plan = plan_layout(items, rules)
    for name in plan.unplaced:
        report.add("equipment", name, "layout", "no cell in the plan is large enough for its footprint")

    out = apply_layout(dict(base_doc or {}), plan)
    _restore_base_placements(out, base_doc)
    _stamp_equipment_metadata(out, provenance)

    spaces = [TopoSpace(**_no_none(row)) for row in out.get("spaces") or []]
    placements = {row.get("NAME"): row for row in out.get("equipments") or [] if isinstance(row, dict)}
    _place_site_terminals(segments, spaces, placements)
    for spec in segments:
        spec.entity.CONNECTIONS = [end.to_connection() for end in spec.ends]

    out["systems"] = _merge_systems(out.get("systems") or [], [spec.entity for spec in segments])
    out.setdefault("openings", [])
    out.setdefault("blueprint", {})
    out.setdefault("design_rules", "standard")

    for message in validate_equipment_in_cells(out):
        name = message.split(":", 1)[0]
        if name not in plan.unplaced:
            report.add("equipment", name, "layout", message.split(":", 1)[-1].strip())

    report.stats = {"equipment": len(out.get("equipments") or []), "systems": len(out["systems"])}
    out["dexpi"] = {
        "source": doc.source,
        "flavour": doc.flavour.value,
        "reader_warnings": list(doc.warnings),
        "report": report.as_dict(),
    }
    return out, catalog


# --------------------------------------------------------------------------- #
# Equipment
# --------------------------------------------------------------------------- #
def _equipment_names(resolved: list[ResolvedEquipment]) -> dict[str, str]:
    """Catalog slug -> the name the equipment is placed and wired under.

    The tag as the P&ID spells it, because that is what a reader of the model expects to see and
    what ``SystemConnection.EQUIPMENT`` has to match. Two items sharing a tag is a real (if sloppy)
    thing in P&ID files and the equipment map is keyed by name, so the second is suffixed rather
    than allowed to shadow the first.
    """
    out: dict[str, str] = {}
    used: set[str] = set()
    for entry in resolved:
        base = (entry.item.tag or "").strip() or entry.slug
        name = base
        suffix = 1
        while name in used:
            suffix += 1
            name = f"{base}-{suffix}"
        used.add(name)
        out[entry.slug] = name
    return out


def _layout_item(entry: ResolvedEquipment, name: str) -> LayoutItem:
    bbox = entry.doc.get("bbox") or {}
    cog = entry.doc.get("cog")
    return LayoutItem(
        name=name,
        type_slug=entry.slug,
        lx=float(bbox.get("lx", 1.0)),
        ly=float(bbox.get("ly", 1.0)),
        lz=float(bbox.get("lz", 1.0)),
        mass=float(entry.doc.get("mass") or 0.0),
        cog=tuple(float(v) for v in cog) if cog else None,
    )


def _equipment_metadata(entry: ResolvedEquipment) -> dict:
    """DEXPI provenance for one placed equipment, for ``TopoEquipment.METADATA``.

    The schematic placement rides along under ``schematic`` in the source document's own units --
    drawing millimetres, kept for a round-trip and for a future face-selection heuristic, never
    read as a plant coordinate.
    """
    placement = entry.item.placement
    metadata: dict = {
        "dexpi_id": entry.item.id,
        "dexpi_class": class_table.resolve(entry.item.class_name),
        "tag": entry.item.tag,
    }
    if placement is not None and placement.location is not None:
        metadata["schematic"] = {"location": [float(v) for v in placement.location]}
    return {"dexpi": metadata}


def _chambers(doc: DexpiDocument, item: DexpiItem) -> list[DexpiItem]:
    """``item``'s chambers, depth-first. A chamber is a sub-volume of its owner (a separator boot,
    a jacket), so its nozzles are ports on the owner's box and a connection naming it resolves to
    the owner."""
    out: list[DexpiItem] = []
    for child in doc.children(item.id):
        if class_table.is_a(child.class_name, "Chamber"):
            out.append(child)
            out.extend(_chambers(doc, child))
    return out


def _stamp_equipment_metadata(out: dict, provenance: dict[str, dict]) -> None:
    for row in out.get("equipments") or []:
        if isinstance(row, dict) and row.get("NAME") in provenance:
            metadata = dict(row.get("METADATA") or {})
            metadata.update(provenance[row["NAME"]])
            row["METADATA"] = metadata


def _restore_base_placements(out: dict, base_doc: dict | None) -> None:
    """Put back every equipment row ``base_doc`` already carried.

    ``apply_layout`` merges by name with the generated row winning, which is right for a fresh
    import and wrong for a re-import: an equipment a user has already positioned must keep the
    position they gave it. The generated row still sized the deck it sits on.
    """
    if not base_doc:
        return
    kept = {row.get("NAME"): row for row in base_doc.get("equipments") or [] if isinstance(row, dict)}
    if not kept:
        return
    out["equipments"] = [
        dict(kept[row["NAME"]]) if isinstance(row, dict) and row.get("NAME") in kept else row
        for row in out.get("equipments") or []
    ]


def _inline_equipment(
    doc: DexpiDocument,
    segments: list[_SegmentSpec],
    catalog: dict,
    index: _Index,
    provenance: dict[str, dict],
    names: dict[str, str],
) -> list[LayoutItem]:
    """Materialise each segment's in-line components as their own small equipment.

    Off by default, because a real P&ID carries dozens of valves per unit and they would otherwise
    flood the equipment listing and the mass rollup. When it is on, each component gets its own
    catalog entry with ports generated from its actual connection nodes -- so it is a real placed
    object with real nozzles, just not one the run passes through.
    """
    taken = set(names.values())
    flow = connection_flow(doc)
    out: list[LayoutItem] = []
    for spec in segments:
        for component in spec.components:
            base = (component.tag or slugify(component.class_name) or component.id).strip()
            name = base
            suffix = 1
            while name in taken:
                suffix += 1
                name = f"{base}-{suffix}"
            taken.add(name)

            specs = [
                nozzle_from_node(node, owner_class=component.class_name, flow=flow.get(node.id))
                for node in component.process_nodes
            ]
            document = build_default_doc(
                component.class_name,
                specs,
                bbox=_INLINE_BBOX,
                strategy="generic",
                ifc_element_class=_INLINE_IFC,
                tag=component.tag,
                dexpi_id=component.id,
            )
            slug = slugify(f"{name}-{component.id}")
            catalog[slug] = document
            index.add_owner(component.id, name)
            for nozzle_id, port_name in port_names(specs).items():
                index.add_port(nozzle_id, name, port_name)
            provenance[name] = {
                "dexpi": {
                    "dexpi_id": component.id,
                    "dexpi_class": class_table.resolve(component.class_name),
                    "tag": component.tag,
                    "inline_component_of": spec.entity.NAME,
                }
            }
            out.append(
                LayoutItem(
                    name=name,
                    type_slug=slug,
                    lx=_INLINE_BBOX[0],
                    ly=_INLINE_BBOX[1],
                    lz=_INLINE_BBOX[2],
                    mass=float(document.get("mass") or 0.0),
                    group=spec.entity.NAME,
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Systems
# --------------------------------------------------------------------------- #
@dataclass
class _SegmentSpec:
    """A DEXPI segment turned into a system entity, with the bits the later passes still need."""

    item: DexpiItem
    entity: TopoSystem
    ends: list[_Endpoint]
    components: list[DexpiItem]


def _routable_segments(doc: DexpiDocument) -> list[DexpiItem]:
    """The items that become systems, in ID order: DEXPI ``PipingNetworkSegment``\\ s.

    Signal and actuating lines (``SignalConveyingFunction`` and friends) run between instrument
    items, which adapy models as metadata rather than as equipment with ports, so they are not
    routed; they survive on the source document for the writer. The system *type* mapping below
    still covers them, because a segment's type is decided by the network it belongs to.
    """
    return sorted(doc.by_kind(ItemKind.PIPING_SEGMENT), key=lambda item: item.id)


def _system_type(doc: DexpiDocument, segment: DexpiItem) -> str:
    """The adapy service type of ``segment``, from the network that owns it.

    ``PipingNetworkSystem`` is piping; an ``ActuatingElectricalSystem`` is electrical; an
    instrumentation signal line is a cable tray. There is **no ducting concept in a DEXPI P&ID**,
    so ``"duct"`` is never inferred here -- a definition list that wants one says so by naming the
    type, and that is documented rather than guessed.
    """
    for owner in [segment, *doc.ancestors(segment.id)]:
        name = class_table.resolve(owner.class_name)
        if class_table.is_a(name, "ActuatingElectricalSystem"):
            return "electrical"
        if class_table.is_a(name, "SignalConveyingFunction") or class_table.is_a(
            name, "ProcessInstrumentationFunction"
        ):
            return "cable"
        if class_table.is_a(name, "PipingNetworkSystem"):
            return "piping"
    return "piping"


def _system_name(doc: DexpiDocument, segment: DexpiItem) -> str:
    """``<line number>/<segment number>``, falling back through the tags to the IDs.

    The line number lives on the parent ``PipingNetworkSystem`` and the segment number on the
    segment, which is exactly the identity a process engineer uses for a run.
    """
    parent = next((item for item in doc.ancestors(segment.id) if item.kind is ItemKind.PIPING_SYSTEM), None)
    line = None
    if parent is not None:
        line = attributes.value_of(parent, attributes.LINE_NUMBER) or parent.tag or parent.id
    number = attributes.value_of(segment, attributes.SEGMENT_NUMBER) or segment.tag
    if line and number:
        return f"{line}/{number}"
    if line:
        return f"{line}/{segment.id}"
    return number or segment.id


def _segment_spec(
    doc: DexpiDocument, segment: DexpiItem, index: _Index, report: DexpiImportReport
) -> _SegmentSpec | None:
    """One segment as a system entity, or None with a reported reason.

    A segment's ends are the connection endpoints that point *outside* it -- everything naming one
    of its own in-line components is interior to the run. Anything other than exactly two of them
    is reported rather than guessed at: a one-ended segment has nowhere to route to, and a
    three-ended one is a branch, which :func:`~ada.topology.routing.route_system` has no concept of.
    """
    name = _system_name(doc, segment)
    # An off-page connector is composed INTO the segment in a real Proteus file, and it is still an
    # end of the run rather than something the run passes through -- so it is deliberately not
    # counted as interior. Everything else inside the segment is.
    inner = {segment.id} | {
        item_id
        for item_id in _descendants(doc, segment.id)
        if doc.items[item_id].kind is not ItemKind.OFF_PAGE_CONNECTOR
    }
    components = [item for item in doc.children(segment.id) if item.kind is ItemKind.PIPING_COMPONENT]

    ends: list[_Endpoint] = []
    for connection in doc.connections:
        if connection.owner_id != segment.id:
            continue
        for item_id, node_id, role in (
            (connection.from_item, connection.from_node, "from"),
            (connection.to_item, connection.to_node, "to"),
        ):
            if item_id is None or item_id in inner:
                continue
            ends.append(_Endpoint(item_id=item_id, node_id=node_id, role=role))  # type: ignore[arg-type]

    if len(ends) != 2:
        report.add(
            "system",
            name,
            "connectivity",
            f"{len(ends)} endpoint(s) outside the segment; a routed run needs exactly two",
        )
        return None

    for end in ends:
        _resolve_endpoint(doc, end, index)
        if end.problem is not None:
            report.add("system", name, "connectivity", f"{end.role} end: {end.problem}")
            return None

    parent = next((item for item in doc.ancestors(segment.id) if item.kind is ItemKind.PIPING_SYSTEM), None)
    entity = TopoSystem(
        NAME=name,
        TYPE=_system_type(doc, segment),  # type: ignore[arg-type]
        MEDIUM=attributes.medium_of(segment) or (attributes.medium_of(parent) if parent is not None else None),
        # Filled in once the layout is known: a site terminal has no position until there is a deck
        # to put it on the edge of.
        CONNECTIONS=[],
        METADATA={
            "dexpi": {
                "dexpi_id": segment.id,
                "dexpi_class": class_table.resolve(segment.class_name),
                "network_id": parent.id if parent is not None else None,
                "network_class": class_table.resolve(parent.class_name) if parent is not None else None,
                "line_number": (attributes.value_of(parent, attributes.LINE_NUMBER) if parent is not None else None),
                "segment_number": attributes.value_of(segment, attributes.SEGMENT_NUMBER),
                "piping_class": attributes.spec_of(segment)
                or (attributes.spec_of(parent) if parent is not None else None),
                "components": [_component_metadata(item) for item in components],
            }
        },
    )
    return _SegmentSpec(item=segment, entity=entity, ends=ends, components=components)


def _component_metadata(item: DexpiItem) -> dict:
    """An in-line component as metadata on the run: what it is, what it is called, how big.

    The routed geometry is a single swept solid with no fittings in it, so this is the only record
    that the run passes through a DN 100 ball valve -- kept whether or not the component was also
    materialised as its own equipment.
    """
    return {
        "dexpi_id": item.id,
        "dexpi_class": class_table.resolve(item.class_name),
        "tag": item.tag,
        "nominal_diameter": attributes.nominal_diameter_of(item),
        "piping_class": attributes.spec_of(item),
    }


def _descendants(doc: DexpiDocument, item_id: str) -> set[str]:
    out: set[str] = set()
    stack = list(doc.items[item_id].child_ids) if item_id in doc.items else []
    while stack:
        current = stack.pop()
        if current in out:
            continue
        out.add(current)
        if current in doc.items:
            stack.extend(doc.items[current].child_ids)
    return out


def _resolve_endpoint(doc: DexpiDocument, end: _Endpoint, index: _Index) -> None:
    """Fill in ``end`` from the DEXPI item it names, or set ``problem``.

    A nozzle (or a bare connection node on an item that has no ``Nozzle`` children) becomes an
    equipment port. An off-page connector becomes a site terminal -- an exact semantic match for
    adapy's model-boundary terminal -- and the **concrete class carries the direction**:
    ``FlowInPipeOffPageConnector`` is a ``PipingSourceItem`` and ``FlowOutPipeOffPageConnector`` a
    ``PipingTargetItem``, so no ``FlowIn``/``FlowOut`` lookup is needed. Where the emitter wrote
    only the abstract ``PipeOffPageConnector``, the end's own role decides: a segment's source end
    is an input, its target end an output. ``connect_site`` rejects ``INOUT``, so an answer of
    "unknown" is not one of the options.
    """
    for key in (end.item_id, end.node_id):
        if key is not None and key in index.ports:
            end.equipment, end.port = index.ports[key]
            return

    item = doc.items.get(end.item_id)
    if item is None:
        end.problem = f"{end.item_id!r} is not an item in the document"
        return

    class_name = class_table.resolve(item.class_name)
    if item.kind is ItemKind.OFF_PAGE_CONNECTOR:
        end.site = slugify(item.tag or item.id) or item.id
        end.direction = _off_page_direction(class_name, end.role)
        return

    if end.item_id in index.owners:
        end.problem = (
            f"{class_name} {end.item_id!r} names equipment {index.owners[end.item_id]!r} but not one of its ports"
        )
        return

    end.problem = f"{class_name} {end.item_id!r} is not a nozzle, an equipment or an off-page connector"


def _off_page_direction(class_name: str, role: str) -> str:
    if class_table.is_a(class_name, "PipingSourceItem") or class_table.is_a(
        class_name, "SignalConveyingFunctionSource"
    ):
        return "IN"
    if class_table.is_a(class_name, "PipingTargetItem") or class_table.is_a(
        class_name, "SignalConveyingFunctionTarget"
    ):
        return "OUT"
    return "IN" if role == "from" else "OUT"


def _place_site_terminals(segments: list[_SegmentSpec], spaces: list[TopoSpace], placements: dict) -> None:
    """Give every site terminal a world position on the boundary of a deck.

    A P&ID's off-page connector has no coordinate at all, so one is generated: inputs enter through
    the ``-X`` face of the deck their equipment stands on and outputs leave through the ``+X`` face,
    spread evenly along that face so two terminals never land on the same point. The direction
    vector points **into** the model, which is the direction the run leaves the boundary along --
    the routing engine follows it for one grid pitch before it starts pathfinding.
    """
    if not spaces:
        return
    by_name = {space.NAME: space for space in spaces}
    groups: dict[tuple[str, str], list[_Endpoint]] = {}
    for spec in segments:
        host = next(
            (by_name[name] for name in (_space_of(placements, end.equipment) for end in spec.ends) if name in by_name),
            spaces[0],
        )
        for end in spec.ends:
            if end.site is not None:
                groups.setdefault((host.NAME, "-X" if end.direction == "IN" else "+X"), []).append(end)

    for (space_name, face), members in sorted(groups.items()):
        space = by_name[space_name]
        elevation = min(_SITE_ELEVATION, float(space.DZ) * _SITE_ELEVATION_FRACTION)
        for i, end in enumerate(members):
            x = float(space.X) if face == "-X" else float(space.X) + float(space.DX)
            y = float(space.Y) + float(space.DY) * (i + 1) / (len(members) + 1)
            end.position = (round(x, 6), round(y, 6), round(float(space.Z) + elevation, 6))
            end.direction_vector = (1.0, 0.0, 0.0) if face == "-X" else (-1.0, 0.0, 0.0)


def _space_of(placements: dict, equipment_name: str | None) -> str | None:
    row = placements.get(equipment_name) if equipment_name else None
    return row.get("SPACE_NAME") if isinstance(row, dict) else None


def _merge_systems(existing: list, generated: list[TopoSystem]) -> list[dict]:
    """Union the generated systems into ``existing`` on ``NAME``, generated winning in place."""
    merged = [dict(row) if isinstance(row, dict) else row for row in existing]
    at = {row.get("NAME"): i for i, row in enumerate(merged) if isinstance(row, dict) and row.get("NAME")}
    for entity in generated:
        row = entity.model_dump(mode="json", exclude_none=True)
        if row["NAME"] in at:
            merged[at[row["NAME"]]] = row
        else:
            at[row["NAME"]] = len(merged)
            merged.append(row)
    return merged


# --------------------------------------------------------------------------- #
# Layout plumbing
# --------------------------------------------------------------------------- #
def _layout_rules(layout: LayoutRules | dict | None) -> LayoutRules:
    if layout is None:
        return default_layout_rules()
    if isinstance(layout, LayoutRules):
        return layout
    if isinstance(layout, dict):
        rules = default_layout_rules()
        for key, value in layout.items():
            if not hasattr(rules, key):
                raise ValueError(f"unknown layout rule {key!r}; expected one of {sorted(vars(rules))}")
            setattr(rules, key, value)
        return rules
    raise TypeError(f"layout must be a LayoutRules, a dict or None, got {type(layout).__name__}")


def _group_by_connectivity(items: list[LayoutItem], segments: list[_SegmentSpec]) -> None:
    """Pack equipment joined by a run together, by giving each connected component of the process
    graph a shared :attr:`~ada.topo_model.layout.LayoutItem.group`.

    The cheapest possible answer to "shelf packing has no process sense": it does not know that a
    pump belongs beside its vessel, but it can at least keep the things a line touches on the same
    stretch of deck. The group key is the alphabetically first member, so it is deterministic and
    independent of the order the segments were read in.
    """
    parent: dict[str, str] = {item.name: item.name for item in items}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for spec in segments:
        connected = [end.equipment for end in spec.ends if end.equipment in parent]
        for other in connected[1:]:
            a, b = find(connected[0]), find(other)
            if a != b:
                parent[max(a, b)] = min(a, b)

    for item in items:
        if item.group is None:
            item.group = find(item.name)


def _no_none(row: Any) -> dict:
    return {k: v for k, v in row.items() if v is not None} if isinstance(row, dict) else row
