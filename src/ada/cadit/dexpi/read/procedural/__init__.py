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
from typing import Any, Literal

from ada.topo_model.layout import (
    LayoutItem,
    LayoutRules,
    apply_layout,
    plan_layout,
    validate_equipment_in_cells,
)
from ada.topology.entities import TopoSpace

from ...equipment_list import branch_points, resolve_equipment
from ...model import DexpiDocument
from ..connectivity import ConnectionIndex
from .layout import (
    _group_by_connectivity,
    _layout_rules,
    _no_none,
    _place_site_terminals,
    _restore_base_placements,
    _stamp_equipment_metadata,
    default_layout_rules,
)
from .merge import _fold_branch_groups, _merge_systems
from .reports import DexpiImportReport, ImportIssue, dexpi_import_report
from .resolve import (
    _Index,
    _routable_segments,
    _segment_spec,
    _SegmentSpec,
    _signal_specs,
)
from .synthesize import (
    _chambers,
    _equipment_metadata,
    _equipment_names,
    _inline_equipment,
    _instrument_equipment,
    _junction_equipment,
    _layout_item,
)

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
    # One pass over the connectivity graph for the whole import: segment endpoints and the flow
    # fallback for every synthesised port both come off it.
    connections = ConnectionIndex.from_document(doc)
    for entry in resolved:
        for chamber in _chambers(doc, entry.item):
            index.add_owner(chamber.id, names[entry.slug])

    items = [_layout_item(entry, names[entry.slug]) for entry in resolved]
    provenance = {names[entry.slug]: _equipment_metadata(entry) for entry in resolved}
    taken = set(names.values())

    # Before the segments, because every one of them that ends at a branch point needs the
    # junction's ports already in the index to resolve that end.
    junctions = branch_points(doc)
    items.extend(_junction_equipment(doc, junctions, catalog, index, provenance, taken, connections))

    segments = [_segment_spec(doc, item, index, report, junctions, connections) for item in _routable_segments(doc)]
    segments = [spec for spec in segments if spec is not None]
    # A 3+-way junction's segments become one branched System (see _fold_branch_groups); a 2-way
    # one is a pass-through, not a branch, and is left as two separate two-ended systems.
    segments = _fold_branch_groups(doc, segments, junctions)

    if inline_components == "equipment":
        items.extend(_inline_equipment(doc, segments, catalog, index, provenance, taken, connections))

    # After the in-line components, so an actuator can be grouped with the valve it operates by the
    # name that valve was actually placed under.
    items.extend(_instrument_equipment(doc, catalog, index, provenance, taken, connections))
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
