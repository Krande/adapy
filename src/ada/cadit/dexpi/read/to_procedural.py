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
   A **branch point** -- an in-line fitting that more than one segment ends at, typically a
   ``PipeTee`` -- is additionally materialised as a small equipment with a port per connection node,
   because the segments meeting there have to terminate on something (see below).
2. **Layout.** The resolved envelopes go to :func:`~ada.topo_model.layout.plan_layout`, which
   generates the decks and places each item on one.
3. **Systems.** One :class:`~ada.topology.entities.TopoSystem` per DEXPI ``PipingNetworkSegment``,
   named ``<line number>/<segment number>`` -- *except* at a 3+-way junction (a tee, a wye), where
   :func:`_fold_branch_groups` merges the segments meeting there into a single **branched** system
   instead (``ada.topology.routing.route_system`` detects two or more ``System.segments`` sharing a
   junction equipment and routes every leg; see :doc:`/documents/routing_through_objects`, Stage 2).
   A DEXPI segment is by definition a two-ended run, so segment-per-system is both faithful to the
   source and routable for the common (degree-2-or-fewer) case; the parent ``PipingNetworkSystem``
   survives as the run's ``MEDIUM`` and as provenance in its ``METADATA``. Segment-per-system is
   *not* by itself enough where the segments meet at a shared fitting: the three runs at a tee each
   name the tee as an end, which is neither a nozzle nor an equipment, so all three used to be
   dropped as unconnectable. Materialising the branch point as equipment (step 1) is what gives each
   of them a real port to terminate on -- a 2-way junction (a run split at an in-line component, not
   a real branch) still becomes two separate two-ended systems, which is Stage 1 (waypoints)
   territory, not this.
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

import dataclasses
import pathlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from ada.api.systems.branch_meta import (
    BRANCH_JUNCTION_ID,
    BRANCH_KEY,
    BRANCH_LEG_METADATA,
    BRANCH_LEGS,
)
from ada.core.text_utils import slugify
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
from ..equipment_list import (
    ResolvedEquipment,
    branch_points,
    connection_flow,
    instrument_items,
    nozzle_specs_for,
    operated_component,
    resolve_equipment,
    signal_lines,
    signal_terminal,
)
from ..model import DexpiDocument, DexpiItem, ItemKind
from ..nozzle_placers import NozzleSpec, nozzle_from_node, port_names
from .conventions import (
    DIRECTION_IN,
    DIRECTION_OUT,
    INLINE_BBOX,
    INLINE_IFC,
    INSTRUMENT_BBOX,
    INSTRUMENT_IFC,
    INSTRUMENT_IFC_DEFAULT,
    JUNCTION_IFC,
    SIGNAL_PORT_KEY,
    SITE_ELEVATION,
    SITE_ELEVATION_FRACTION,
    SITE_INLET_FACE,
    SITE_OUTLET_FACE,
    PortDirectionToken,
)
from .naming import unique_name

__all__ = [
    "DexpiImportReport",
    "ImportIssue",
    "InlineComponents",
    "default_layout_rules",
    "dexpi_import_report",
    "ResolvedDexpi",
    "dexpi_to_procedural_doc",
    "dexpi_to_resolved",
    "resolved_to_procedural_doc",
]

InlineComponents = Literal["metadata", "equipment"]

# Envelopes, IFC classes, the synthetic signal-port key and the site-terminal placement are
# conventions, held in :mod:`.conventions` alongside every other one the import runs on.


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

    ``kind="model"`` is the whole-document case rather than one lost item: a P&ID that yields no
    3D model at all, because nothing in it resolved to equipment to lay out. It carries no count of
    its own, so :meth:`DexpiImportReport.summary` states it instead of the per-item tallies, which
    are all zero in that situation and read as a clean import if left to speak alone.
    """

    kind: Literal["system", "equipment", "model"]
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
        counts = (
            f"{systems_dropped} of {systems_total} system(s) and "
            f"{equipment_dropped} of {equipment_total} equipment did not reach the 3D model"
        )

        # A document-level failure has no per-item counts behind it -- every tally above is 0 of 0,
        # which reads as a clean import. Say what actually happened instead.
        document = "; ".join(issue.reason for issue in self.of_kind("model"))
        if not document:
            return counts
        if systems_dropped or equipment_dropped:
            return f"{document}; {counts}"
        return document

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


def dexpi_import_report(model) -> str:
    """The read report of a :class:`~ada.SystemModel`, as a console table.

    Takes the model rather than a built assembly: what the *read* could not carry and what the
    *build* could not carry are different failures, and the second lives on the assembly the build
    produced (``assembly.metadata["build"]``). Anything without a report says so rather than raising.
    """
    report = getattr(model, "report", None)
    if report is None:
        return "No import report on this model."
    return report.format()


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
    direction: PortDirectionToken | None = None
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
        self.signal_ports: dict[str, list[tuple[str, str]]] = {}
        self._signal_used: dict[str, int] = {}
        for entry in resolved:
            name = names[entry.slug]
            self.owners[entry.item.id] = name
            for nozzle_id, port_name in entry.ports.items():
                self.ports[nozzle_id] = (name, port_name)

    def add_owner(self, item_id: str, name: str) -> None:
        self.owners[item_id] = name

    def add_port(self, key: str, equipment: str, port: str) -> None:
        self.ports[key] = (equipment, port)

    def add_signal_port(self, item_id: str, equipment: str, port: str) -> None:
        """Offer one of ``item_id``'s ports for a signal line to terminate on.

        A pool rather than a single port because an instrument is routinely an end of more than one
        line -- a controller reads a transmitter and drives a valve -- and ``System.connect`` refuses
        a port that is already wired, so sharing one would silently drop every line after the first.
        """
        self.signal_ports.setdefault(item_id, []).append((equipment, port))

    def take_signal_port(self, item_id: str) -> tuple[str, str] | None:
        """The next unused signal port on ``item_id``, or None when the pool is empty."""
        pool = self.signal_ports.get(item_id) or []
        used = self._signal_used.get(item_id, 0)
        if used >= len(pool):
            return None
        self._signal_used[item_id] = used + 1
        return pool[used]


# --------------------------------------------------------------------------- #
# The importer
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class ResolvedDexpi:
    """A P&ID read but not placed -- the output of :func:`dexpi_to_resolved`.

    Everything here is settled by the source: which equipment exists, how big each one is, where its
    ports sit on its own box, and what is connected to what. **Nothing here has a plant coordinate**,
    because a P&ID states none. Placing it is :func:`resolved_to_procedural_doc`, which needs deck
    bounds that only a build can supply.
    """

    #: Unplaced footprints, one per equipment the layout will have to find room for.
    items: list[LayoutItem]
    #: One entry per routable segment and signal line: the system entity and its two endpoints.
    segments: list["_SegmentSpec"]
    #: ``{slug: equipment document}`` -- the compiler's ``equipment_resolver``.
    catalog: dict
    #: ``{equipment name: metadata}``, stamped onto the placed rows by the build.
    provenance: dict
    #: Read-stage gaps only. Whether a vessel *fits* depends on deck bounds, so it can only ever be
    #: a build gap and is reported there.
    report: DexpiImportReport
    #: The source file's basename, not its full path -- this reaches the browser (see
    #: ``resolved_to_procedural_doc``), and a viewer should not be naming the machine it runs on.
    source: str | None = None
    flavour: str = ""
    warnings: list[str] = dataclasses.field(default_factory=list)


def dexpi_to_resolved(
    doc: DexpiDocument,
    *,
    definitions: Any = None,
    inline_components: InlineComponents = "metadata",
) -> ResolvedDexpi:
    """Resolve ``doc`` into a :class:`ResolvedDexpi` -- read, but not placed.

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
    taken = set(names.values())

    # Before the segments, because every one of them that ends at a branch point needs the
    # junction's ports already in the index to resolve that end.
    junctions = branch_points(doc)
    items.extend(_junction_equipment(doc, junctions, catalog, index, provenance, taken))

    segments = [_segment_spec(doc, item, index, report, junctions) for item in _routable_segments(doc)]
    segments = [spec for spec in segments if spec is not None]
    # A 3+-way junction's segments become one branched System (see _fold_branch_groups); a 2-way
    # one is a pass-through, not a branch, and is left as two separate two-ended systems.
    segments = _fold_branch_groups(doc, segments, junctions)

    if inline_components == "equipment":
        items.extend(_inline_equipment(doc, segments, catalog, index, provenance, taken))

    # After the in-line components, so an actuator can be grouped with the valve it operates by the
    # name that valve was actually placed under.
    items.extend(_instrument_equipment(doc, catalog, index, provenance, taken))
    segments.extend(_signal_specs(doc, index, report))

    # What the read produced, which is what its own summary line counts against. The build keeps a
    # separate tally of what reached 3D; the two are different numbers answering different questions.
    report.stats = {"equipment": len(items), "systems": len(segments)}

    return ResolvedDexpi(
        items=items,
        segments=segments,
        catalog=catalog,
        provenance=provenance,
        report=report,
        source=pathlib.Path(doc.source).name if doc.source else None,
        flavour=doc.flavour.value,
        warnings=list(doc.warnings),
    )


def resolved_to_procedural_doc(
    resolved: "ResolvedDexpi",
    *,
    layout: LayoutRules | dict | None = None,
    base_doc: dict | None = None,
) -> tuple[dict, dict]:
    """Place ``resolved`` under ``layout`` and return ``(procedural document, equipment catalog)``.

    The build half. Everything here needs deck bounds to have an answer at all -- generating the
    decks, packing the equipment onto them, seating a site terminal on a deck edge -- which is
    exactly why none of it belongs to reading a P&ID.

    ``resolved`` is **not** modified: the layout stamps a group onto every item and site-terminal
    placement writes positions onto a segment's endpoints, so a second build with different bounds
    would otherwise inherit the first one's placements. Both are copied per call.
    """
    rules = _layout_rules(layout)
    report = DexpiImportReport(issues=list(resolved.report.issues))

    items = [dataclasses.replace(item) for item in resolved.items]
    segments = [_copy_segment(spec) for spec in resolved.segments]

    _group_by_connectivity(items, segments)
    plan = plan_layout(items, rules)
    for name in plan.unplaced:
        report.add("equipment", name, "layout", "no cell in the plan is large enough for its footprint")

    out = apply_layout(dict(base_doc or {}), plan)
    _restore_base_placements(out, base_doc)
    _stamp_equipment_metadata(out, resolved.provenance)

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
        "source": resolved.source,
        "flavour": resolved.flavour,
        "reader_warnings": list(resolved.warnings),
        "report": report.as_dict(),
    }
    return out, resolved.catalog


def _copy_segment(spec: "_SegmentSpec") -> "_SegmentSpec":
    """A segment safe to place: fresh entity and fresh endpoints, same source item.

    Only the two mutated things are copied. ``item`` is a :class:`DexpiItem` holding the source
    ``ET.Element``; deep-copying it per build would be both expensive and pointless, since nothing
    downstream of here writes to it.
    """
    return _SegmentSpec(
        item=spec.item,
        entity=spec.entity.model_copy(deep=True),
        ends=[dataclasses.replace(end) for end in spec.ends],
        components=spec.components,
    )


def dexpi_to_procedural_doc(
    doc: DexpiDocument,
    *,
    definitions: Any = None,
    layout: LayoutRules | dict | None = None,
    base_doc: dict | None = None,
    inline_components: InlineComponents = "metadata",
) -> tuple[dict, dict]:
    """Read ``doc`` and place it in one call -- :func:`dexpi_to_resolved` then
    :func:`resolved_to_procedural_doc`.

    Kept because the pair is what most callers want and because it is the shape
    ``ada.dexpi_to_procedural`` exposes. Take the two halves separately to resolve a P&ID once and
    place it several ways.
    """
    resolved = dexpi_to_resolved(doc, definitions=definitions, inline_components=inline_components)
    return resolved_to_procedural_doc(resolved, layout=layout, base_doc=base_doc)


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
        out[entry.slug] = unique_name((entry.item.tag or "").strip() or entry.slug, used)
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


def _junction_equipment(
    doc: DexpiDocument,
    junctions: dict[str, list[str]],
    catalog: dict,
    index: _Index,
    provenance: dict[str, dict],
    taken: set[str],
) -> list[LayoutItem]:
    """Materialise each branch point as a small equipment with a port per connection node.

    Unconditional, unlike :func:`_inline_equipment`: a junction is not a detail a caller can choose
    to skip, because without it every run that meets there is dropped. The ports come from the
    fitting's own connection nodes, so a tee gets exactly its three, and each segment resolves its
    end to the specific node it named rather than to the fitting as a whole.

    No ``group`` is set, so :func:`_group_by_connectivity` folds the junction in with the equipment
    its runs reach -- which is the one placement heuristic that matters here, since a tee stranded
    on a far deck makes every run through it a long one.
    """
    flow = connection_flow(doc)
    out: list[LayoutItem] = []
    for item_id, segment_ids in junctions.items():
        item = doc.items[item_id]
        # One pool across resolved equipment, branch points and materialised in-line components:
        # they all land in the same equipment map, keyed by name.
        name = unique_name((item.tag or item.id).strip(), taken, fallback="equipment")
        specs = nozzle_specs_for(doc, item, flow)
        document = build_default_doc(
            item.class_name,
            specs,
            bbox=INLINE_BBOX,
            strategy="generic",
            ifc_element_class=JUNCTION_IFC,
            tag=item.tag,
            dexpi_id=item.id,
        )
        slug = slugify(f"{name}-{item.id}")
        catalog[slug] = document
        index.add_owner(item.id, name)
        for nozzle_id, port_name in port_names(specs).items():
            index.add_port(nozzle_id, name, port_name)
        provenance[name] = {
            "dexpi": {
                "dexpi_id": item.id,
                "dexpi_class": class_table.resolve(item.class_name),
                "tag": item.tag,
                "branch_point_of": [_system_name(doc, doc.items[sid]) for sid in segment_ids],
            }
        }
        out.append(
            LayoutItem(
                name=name,
                type_slug=slug,
                lx=INLINE_BBOX[0],
                ly=INLINE_BBOX[1],
                lz=INLINE_BBOX[2],
                mass=float(document.get("mass") or 0.0),
            )
        )
    return out


def _instrument_ifc(class_name: str) -> str:
    """The IFC class for an instrument, resolved up the DEXPI supertype DAG."""
    name = class_table.resolve(class_name)
    if name in INSTRUMENT_IFC:
        return INSTRUMENT_IFC[name]
    for dexpi_class, ifc_class in INSTRUMENT_IFC.items():
        if class_table.is_a(name, dexpi_class):
            return ifc_class
    return INSTRUMENT_IFC_DEFAULT


def _instrument_equipment(
    doc: DexpiDocument,
    catalog: dict,
    index: _Index,
    provenance: dict[str, dict],
    taken: set[str],
) -> list[LayoutItem]:
    """Materialise each connected instrument as a small equipment with a signal port.

    Instrumentation used to stop at the document: a controller, a transmitter and the actuator on a
    control valve were carried as metadata and echoed back out by the writer, and none of them
    existed in 3D. That makes the model quietly untruthful in a specific way -- the P&ID says this
    controller drives that valve, and the 3D model contains no controller, no actuator and nothing
    joining them.

    The ports are ``signal``, not ``process``, and the distinction is load-bearing rather than
    cosmetic: ``System.connect`` refuses a category mismatch, so a signal run wired to a process
    port is dropped by the compiler with a warning. An instrument that declares real connection
    nodes gets a port per node; the majority declare none at all -- DEXPI states instrument
    connectivity with associations rather than ``<ConnectionPoints>`` -- and get one synthetic
    signal port instead, keyed so :func:`_signal_specs` can find it again.

    An actuator is grouped with the valve it operates, so shelf packing keeps them on the same
    stretch of deck instead of putting a positioner on another level from its valve. That is the
    same cheap heuristic :func:`_group_by_connectivity` applies to process equipment, applied to the
    one relationship instrumentation states outright.
    """
    flow = connection_flow(doc)
    instruments = instrument_items(doc)
    # How many signal lines end on each instrument, so it can be given that many ports.
    terminals: dict[str, int] = {}
    for ends in signal_lines(doc).values():
        for end_id in ends:
            target = signal_terminal(doc, end_id)
            if target is not None:
                terminals[target.id] = terminals.get(target.id, 0) + 1

    out: list[LayoutItem] = []
    for item in instruments:
        operated = operated_component(doc, item)
        # An actuator must not be named after the valve it drives: with the valve materialised too
        # the model then carries PV-202 and PV-202-2 and neither name says which is which. DEXPI
        # numbers the actuating system itself (PV-202.01), so that wins; failing that the valve tag
        # is qualified rather than borrowed outright.
        base = (
            item.tag
            or attributes.value_of(item, attributes.ACTUATING_SYSTEM_NUMBER)
            or (f"{operated.tag}-ACT" if operated is not None and operated.tag else None)
            or item.id
        ).strip()
        name = unique_name(base, taken, fallback="equipment")

        # An instrument's own nodes are signal connections whatever the node type says: the
        # category_for default is "process", which is right for a nozzle and wrong for a
        # transmitter, and System.connect would refuse the run on the mismatch.
        specs = [
            dataclasses.replace(
                nozzle_from_node(node, owner_class=item.class_name, flow=flow.get(node.id)),
                category="signal",
            )
            for node in item.process_nodes
        ]
        # Most instruments declare no connection points at all -- DEXPI states their connectivity
        # with associations -- so ports are synthesised instead, one per line that ends here. One
        # port would not do: a controller that reads a transmitter and drives a valve is the end of
        # two lines, and System.connect refuses a port that is already wired.
        wanted_ports = max(1, terminals.get(item.id, 0))
        while len(specs) < wanted_ports:
            ordinal = len(specs) + 1
            specs.append(
                NozzleSpec(
                    id=f"{item.id}{SIGNAL_PORT_KEY}{ordinal}",
                    name=f"S{ordinal}",
                    category="signal",
                )
            )

        document = build_default_doc(
            item.class_name,
            specs,
            bbox=INSTRUMENT_BBOX,
            strategy="generic",
            ifc_element_class=_instrument_ifc(item.class_name),
            tag=item.tag,
            dexpi_id=item.id,
        )
        slug = slugify(f"{name}-{item.id}")
        catalog[slug] = document
        index.add_owner(item.id, name)
        for nozzle_id, port_name in port_names(specs).items():
            index.add_port(nozzle_id, name, port_name)
            index.add_signal_port(item.id, name, port_name)
        # A signal line names the *function* an instrument performs as often as the instrument
        # itself, so a descendant resolves to this object's port too -- otherwise the run terminates
        # on an ActuatingFunction that was never placed. A descendant that is a placed instrument in
        # its own right is left alone: a loop function owns its own sensing element, and mapping the
        # element onto its parent would put both ends of the line between them on one object.
        placed = {other.id for other in instruments}
        for descendant in _descendants(doc, item.id):
            if descendant not in placed:
                index.add_owner(descendant, name)

        provenance[name] = {
            "dexpi": {
                "dexpi_id": item.id,
                "dexpi_class": class_table.resolve(item.class_name),
                "tag": item.tag,
                "instrument": True,
                "operates": operated.tag or operated.id if operated is not None else None,
            }
        }
        out.append(
            LayoutItem(
                name=name,
                type_slug=slug,
                lx=INSTRUMENT_BBOX[0],
                ly=INSTRUMENT_BBOX[1],
                lz=INSTRUMENT_BBOX[2],
                mass=float(document.get("mass") or 0.0),
                group=(operated.tag or operated.id) if operated is not None else None,
            )
        )
    return out


def _inline_equipment(
    doc: DexpiDocument,
    segments: list[_SegmentSpec],
    catalog: dict,
    index: _Index,
    provenance: dict[str, dict],
    taken: set[str],
) -> list[LayoutItem]:
    """Materialise each segment's in-line components as their own small equipment.

    Off by default, because a real P&ID carries dozens of valves per unit and they would otherwise
        flood the equipment listing and the mass rollup. When it is on, each component gets its own
        catalog entry with ports generated from its actual connection nodes -- so it is a real placed
        object with real nozzles, just not one the run passes through.
    """
    flow = connection_flow(doc)
    out: list[LayoutItem] = []
    for spec in segments:
        for component in spec.components:
            name = unique_name(
                (component.tag or slugify(component.class_name) or component.id).strip(), taken, fallback="equipment"
            )

            specs = [
                nozzle_from_node(node, owner_class=component.class_name, flow=flow.get(node.id))
                for node in component.process_nodes
            ]
            document = build_default_doc(
                component.class_name,
                specs,
                bbox=INLINE_BBOX,
                strategy="generic",
                ifc_element_class=INLINE_IFC,
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
                    lx=INLINE_BBOX[0],
                    ly=INLINE_BBOX[1],
                    lz=INLINE_BBOX[2],
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
    doc: DexpiDocument,
    segment: DexpiItem,
    index: _Index,
    report: DexpiImportReport,
    junctions: dict[str, list[str]],
) -> _SegmentSpec | None:
    """One segment as a system entity, or None with a reported reason.

    A segment's ends are the connection endpoints that point *outside* it -- everything naming one
    of its own in-line components is interior to the run. Anything other than exactly two of them
    is reported rather than guessed at: a one-ended segment has nowhere to route to, and a
    three-ended one is a branch, which :func:`~ada.topology.routing.route_system` has no concept of.
    """
    name = _system_name(doc, segment)
    # Two kinds of child are ends of the run rather than something it passes through, and so are
    # deliberately not counted as interior. An off-page connector, which a real Proteus file
    # composes INTO the segment; and a branch point (see
    # :func:`~ada.cadit.dexpi.equipment_list.branch_points`), which is where this run stops and the
    # next one starts -- the segment that happens to own the tee in the file has
    # no more claim to route through it than the two that reference it from outside. Everything
    # else inside the segment is interior.
    inner = {segment.id} | {
        item_id
        for item_id in _descendants(doc, segment.id)
        if doc.items[item_id].kind is not ItemKind.OFF_PAGE_CONNECTOR and item_id not in junctions
    }
    components = [
        item for item in doc.children(segment.id) if item.kind is ItemKind.PIPING_COMPONENT and item.id not in junctions
    ]

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


def _fold_branch_groups(
    doc: DexpiDocument, segments: list["_SegmentSpec"], junctions: dict[str, list[str]]
) -> list["_SegmentSpec"]:
    """Merge the segments meeting at a 3+-way junction into one branched :class:`_SegmentSpec`.

    Each of those segments already resolved its junction-facing end to its OWN dedicated port on
    the junction equipment (:func:`_junction_equipment` gives a tee exactly three), so nothing about
    connectivity changes here -- this only changes how many :class:`~ada.topology.entities.TopoSystem`
    rows that connectivity becomes. The merged system's ``ends`` is every leg's two endpoints
    concatenated leg by leg, so the ``CONNECTIONS`` list :func:`resolved_to_procedural_doc` later
    builds from ``ends`` is N pairs in that same order; ``METADATA["branch"]["legs"]`` names each
    leg (the ORIGINAL per-segment name, e.g. ``"L100/1"``) in that order too, which is what
    :func:`~ada.topo_model.compile._wire_systems` reads to rebuild the tree and what
    :mod:`ada.cadit.dexpi.write.from_ada` reads to split a branched System back into the segments
    the source had.

    A degree-2 junction (in ``junctions`` but with only two owning segments) is a pass-through, not
    a branch -- left as two separate two-ended systems, unchanged. A leg that failed
    :func:`_segment_spec` already reported its own reason and is missing from ``segments``; the
    whole junction is then left unfolded rather than merging a partial, disconnected set. Likewise
    when one of a junction's segments directly joins it to ANOTHER 3+-way junction (no equipment
    between two tees) and that other junction folded first: taking the shared segment into this
    merge too would duplicate its two endpoints across both branched systems, so this junction is
    left unfolded rather than double-booking it -- a real but rare shape (adjacent branch points
    with no run between them) that stays Stage-2-unsupported and two-ended-per-segment, same as
    before this function existed.
    """
    by_id = {spec.item.id: spec for spec in segments}
    folded: list[_SegmentSpec] = []
    consumed: set[str] = set()
    for junction_id, segment_ids in junctions.items():
        if len(segment_ids) < 3 or any(sid in consumed for sid in segment_ids):
            continue
        legs = [by_id[sid] for sid in segment_ids if sid in by_id]
        if len(legs) != len(segment_ids):
            continue
        consumed.update(segment_ids)
        junction_item = doc.items[junction_id]
        first = legs[0].entity
        merged_ends: list[_Endpoint] = []
        leg_names: list[str] = []
        leg_metadata: list[dict] = []
        components: list[DexpiItem] = []
        for leg in legs:
            merged_ends.extend(leg.ends)
            leg_names.append(leg.entity.NAME)
            leg_metadata.append({"name": leg.entity.NAME, "dexpi": leg.entity.METADATA.get("dexpi", {})})
            components.extend(leg.components)
        entity = TopoSystem(
            NAME=f"branch-{(junction_item.tag or junction_item.id).strip()}",
            TYPE=first.TYPE,
            MEDIUM=first.MEDIUM,
            CONNECTIONS=[],
            METADATA={
                BRANCH_KEY: {
                    BRANCH_JUNCTION_ID: junction_id,
                    BRANCH_LEGS: leg_names,
                    BRANCH_LEG_METADATA: leg_metadata,
                },
            },
        )
        folded.append(_SegmentSpec(item=junction_item, entity=entity, ends=merged_ends, components=components))
    folded.extend(spec for spec in segments if spec.item.id not in consumed)
    return folded


def _signal_endpoint(doc: DexpiDocument, index: _Index, end_id: str, role: str) -> _Endpoint:
    """One end of a signal line, resolved to a placed object's port.

    ``signal_terminal`` decides *which* object the end means -- the instrument rather than the
    function it performs, the valve rather than the positioner's own reference to it. This then
    finds that object's port: its synthetic signal port if it has one, otherwise any port the index
    already holds for it, so a line that terminates on a nozzle or a materialised valve still lands
    somewhere real.
    """
    end = _Endpoint(item_id=end_id, node_id=None, role=role)  # type: ignore[arg-type]
    target = signal_terminal(doc, end_id)
    if target is None:
        end.problem = f"{end_id!r} is not an item in the document"
        return end

    port = index.take_signal_port(target.id) or index.ports.get(target.id)
    if port is None:
        end.problem = (
            f"{class_table.resolve(target.class_name)} {target.id!r} is not placed in the model "
            "with a free signal port, so a signal run has nothing to terminate on"
        )
        return end

    end.equipment, end.port = port
    return end


def _signal_specs(
    doc: DexpiDocument,
    index: _Index,
    report: DexpiImportReport,
) -> list[_SegmentSpec]:
    """Every signal line that names both ends, as a routable two-ended system.

    The instrumentation counterpart of :func:`_segment_spec`, and deliberately a separate function
    because the two flavours of connectivity are stated in different ways. A piping segment owns
    ``<Connection>`` elements naming node indices; a ``SignalConveyingFunction`` owns nothing and
    states its ends as ``has logical start``/``has logical end`` associations, because it is a
    *function* rather than a pipe -- what it joins is a logical fact and the line drawn on the sheet
    is presentation. Reading only the piping form is why none of this reached 3D before.

    An end that resolves to nothing placed is reported rather than guessed at, the same contract a
    piping run gets.
    """
    out: list[_SegmentSpec] = []
    for item_id, (start_id, end_id) in signal_lines(doc).items():
        item = doc.items[item_id]
        name = _system_name(doc, item)

        ends = [
            _signal_endpoint(doc, index, start_id, "from"),
            _signal_endpoint(doc, index, end_id, "to"),
        ]
        problem = next((end for end in ends if end.problem is not None), None)
        if problem is not None:
            report.add("system", name, "connectivity", f"{problem.role} end: {problem.problem}")
            continue
        if ends[0].equipment == ends[1].equipment:
            report.add(
                "system",
                name,
                "connectivity",
                f"both ends resolve to {ends[0].equipment!r}; a routed run needs two distinct objects",
            )
            continue

        parent = next((a for a in doc.ancestors(item.id) if a.kind is ItemKind.INSTRUMENTATION), None)
        entity = TopoSystem(
            NAME=name,
            TYPE=_system_type(doc, item),  # type: ignore[arg-type]
            MEDIUM=None,
            CONNECTIONS=[],
            METADATA={
                "dexpi": {
                    "dexpi_id": item.id,
                    "dexpi_class": class_table.resolve(item.class_name),
                    "signal_line": True,
                    "logical_start": start_id,
                    "logical_end": end_id,
                    "loop_id": parent.id if parent is not None else None,
                    "loop_tag": parent.tag if parent is not None else None,
                }
            },
        )
        out.append(_SegmentSpec(item=item, entity=entity, ends=ends, components=[]))
    return out


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


def _off_page_direction(class_name: str, role: str) -> PortDirectionToken:
    if class_table.is_a(class_name, "PipingSourceItem") or class_table.is_a(
        class_name, "SignalConveyingFunctionSource"
    ):
        return DIRECTION_IN
    if class_table.is_a(class_name, "PipingTargetItem") or class_table.is_a(
        class_name, "SignalConveyingFunctionTarget"
    ):
        return DIRECTION_OUT
    return DIRECTION_IN if role == "from" else DIRECTION_OUT


def _place_site_terminals(segments: list[_SegmentSpec], spaces: list[TopoSpace], placements: dict) -> None:
    """Give every site terminal a world position on the boundary of a deck.

    A P&ID's off-page connector has no coordinate at all, so one is generated: inputs enter through
    :data:`~.conventions.SITE_INLET_FACE` of the deck their equipment stands on and outputs leave
    through :data:`~.conventions.SITE_OUTLET_FACE`, spread evenly along that face so two terminals
    never land on the same point. The direction
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
                face = SITE_INLET_FACE if end.direction == DIRECTION_IN else SITE_OUTLET_FACE
                groups.setdefault((host.NAME, face), []).append(end)

    for (space_name, face), members in sorted(groups.items()):
        space = by_name[space_name]
        elevation = min(SITE_ELEVATION, float(space.DZ) * SITE_ELEVATION_FRACTION)
        for i, end in enumerate(members):
            x = float(space.X) if face == SITE_INLET_FACE else float(space.X) + float(space.DX)
            y = float(space.Y) + float(space.DY) * (i + 1) / (len(members) + 1)
            end.position = (round(x, 6), round(y, 6), round(float(space.Z) + elevation, 6))
            end.direction_vector = (1.0, 0.0, 0.0) if face == SITE_INLET_FACE else (-1.0, 0.0, 0.0)


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
