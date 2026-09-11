"""Write a live ``ada.Assembly`` back out as DEXPI: a merge, not a regeneration.

Two very different things happen here, matching :meth:`ada.Assembly.to_dexpi`'s two modes.

**The merge** (:func:`merge_from_assembly`, the default) starts from the source
:class:`~ada.cadit.dexpi.model.DexpiDocument` the assembly was read from
(``assembly.dexpi_store``) and edits a deep copy of it in place: equipment and systems that still
exist in the live assembly are re-synced from the live objects, so a port added or removed in
Python lands in the output; everything the source document carried that adapy does not model at
all -- instrumentation, the shape catalogue, presentation, ``DexpiCustomAttributes``, and every
attribute this module does not touch -- is untouched and reaches the writer through the ordinary
``.raw``/``extras`` echo the two writers (:mod:`.write_proteus`, :mod:`.write_dexpi20`) already
implement. This module never constructs XML itself; it edits the neutral model and hands the result
to those writers, exactly as a hand-edited :class:`DexpiDocument` would be.

**The from-scratch build** (:func:`build_from_scratch`, ``to_dexpi(from_scratch=True)``) ignores
the source document entirely and writes a new one from the live equipment/ports/systems alone.
Lossy by construction and never claimed otherwise: there is no chamber, no instrumentation, no
piping class, no RDL URI, and no schematic layout to write, because none of it survives on the
live adapy objects. It exists for a plain ``ada.topo_model`` assembly that was never read from
DEXPI at all.

**What "adapy owns" is narrower than the neutral model.** The importer
(:mod:`ada.cadit.dexpi.read.to_procedural`) turns an equipment's nozzles into ports and a segment
into a two-ended system -- or, at a 3+-way junction, several segments into one *branched* system
(``System.segments``, see :func:`_branch_legs`) -- and nothing else round-trips through a live
object today. So the merge only re-syncs: an equipment's *name* (matched against, not edited) and
its *port list* (added/removed ports become nozzle items), and a system's *two connection
endpoints* per leg. A renamed system, an edited tag, a moved nozzle -- none of those have a live
adapy field to read the edit back from, and this module does not invent one; see the docstring of
:func:`_sync_equipment_ports` for the one exception (reconnecting a system to a different port,
which *is* readable, and *is* synced).

**Identity.** Neither ``ada.Equipment`` nor ``ada.api.systems.System`` carries a DEXPI id today (see
the gap report) -- the only handle back to the source item is the *name* the importer assigned, and
that name is derived deterministically from the source document alone (the equipment's tag, or a
line/segment number pair), never from anything the caller passed in. So matching a live object back
to its source item means recomputing that same derivation over the (still-original) source document
-- :func:`_source_identity` and :func:`_source_segment_by_name` mirror
``to_procedural._equipment_names``/``._junction_equipment``/``._system_name`` for exactly that
reason, and the two sides must be kept in sync if that naming rule ever changes.

**Reconciliation.** An equipment or a system present in the source but absent from the live assembly
is dropped -- item and every connection that named it -- and logged. One present in the live
assembly with no matching source item is minted a fresh id (``_IdMinter``) and written as a new
item; adapy holds no DEXPI class for equipment it did not read from DEXPI, so a new equipment is
written as the abstract ``ProcessEquipment`` and a new system as a bare ``PipingNetworkSegment``
with no owning ``PipingNetworkSystem`` -- both are honest placeholders, not inferred data.
``ComponentClassURI`` is never synthesised for a new item, matching the from-scratch writers.
"""

from __future__ import annotations

import copy
import itertools
import pathlib
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.api.systems.ports import PortDirection
from ada.config import logger
from ada.core.text_utils import slugify

from .. import attributes as attribute_lookup
from ..equipment_list import (
    branch_points,
    connection_flow,
    definition_slug,
    equipment_items,
    instrument_items,
    nozzle_specs_for,
    operated_component,
)
from ..model import (
    DexpiAttribute,
    DexpiConnection,
    DexpiDocument,
    DexpiFlavour,
    DexpiHeader,
    DexpiItem,
    DexpiNode,
    ItemKind,
)
from ..nozzle_placers import port_names
from . import xml_utils
from .write_dexpi20 import write_dexpi20
from .write_proteus import write_proteus

if TYPE_CHECKING:
    from ada.api.spatial.equipment import Equipment
    from ada.api.systems.base import System
    from ada.api.systems.ports import Port
    from ada.api.systems.segments import SystemSegment

__all__ = ["build_from_scratch", "merge_into_document", "write_model_from_scratch", "write_model_merged"]

# The attribute set every synthesised ``GenericAttributes`` group goes into -- matches the
# from-scratch writers' own ``DEXPI_ATTRIBUTE_SET``.
_ATTRIBUTE_SET = "DexpiAttributes"

# The class a new, adapy-only item is given when there is no source class to echo. Both are real,
# abstract DEXPI classes -- honest placeholders, not a guess at the concrete one.
_NEW_EQUIPMENT_CLASS = "ProcessEquipment"
_NEW_SEGMENT_CLASS = "PipingNetworkSegment"

_WRITERS = {DexpiFlavour.PROTEUS: write_proteus, DexpiFlavour.DEXPI20: write_dexpi20}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def write_model_merged(model, destination: str | pathlib.Path, flavour: str = "proteus") -> pathlib.Path:
    """Merge ``model`` into its source DEXPI document and write ``flavour`` to ``destination``.

    Takes a :class:`~ada.api.systems.model.SystemModel` rather than an assembly, because that is the
    layer DEXPI actually describes: the export needs the equipment/port/system graph and DEXPI has
    no way to express a coordinate, so a built assembly has nothing to add.
    """
    doc = merge_into_document(model.source_document, model.equipment, model.systems)
    return xml_utils.write_element(_render(doc, flavour), destination)


def write_model_from_scratch(model, destination: str | pathlib.Path, flavour: str = "proteus") -> pathlib.Path:
    """Write ``model`` as a brand-new DEXPI document -- see :func:`build_from_scratch`."""
    doc = build_from_scratch(model, flavour=flavour)
    return xml_utils.write_element(_render(doc, flavour), destination)


def _render(doc: DexpiDocument, flavour: str | DexpiFlavour | None) -> ET.Element:
    resolved = DexpiFlavour(flavour) if flavour is not None else doc.flavour
    return _WRITERS[resolved](doc)


# ---------------------------------------------------------------------------
# The merge
# ---------------------------------------------------------------------------
def merge_into_document(source: DexpiDocument | None, equipment, systems) -> DexpiDocument:
    """A deep copy of ``source``, edited to match the live ``equipment`` and ``systems``.

    Raises ``ValueError`` when there is no source document -- there is nothing to merge into; the
    caller wants ``to_dexpi(from_scratch=True)`` instead.
    """
    if source is None:
        raise ValueError(
            "to_dexpi(from_scratch=False) needs a source DEXPI document on this model "
            "(SystemModel.source_document), which only a reader such as ada.from_dexpi sets. Pass "
            "from_scratch=True to write a new, adapy-only document instead."
        )

    doc = copy.deepcopy(source)
    minter = _IdMinter(doc)

    live_equipment = list(equipment)
    source_equipment, source_junctions, source_instruments = _source_identity(doc)
    live_by_name = {eq.name: eq for eq in live_equipment}

    # Branch points first, because they must never fall through to the "new in the assembly" path
    # below: a materialised ``PipeTee`` is a mirror of a piping component the source document
    # already carries, not equipment adapy authored, and minting a ``ProcessEquipment`` for it
    # would rewrite the junction and every connection into it.
    port_index: dict[tuple[str, str], tuple[str, str]] = _junction_port_index(doc, source_junctions, live_by_name)

    for name, eq in live_by_name.items():
        # Junctions and instruments are both mirrors of items the source already carries, so neither
        # may fall through to the "new in the assembly" path below.
        if name in source_junctions or name in source_instruments:
            continue
        item = source_equipment.get(name)
        if item is None:
            item = _add_equipment_item(doc, eq, minter)
            logger.info("dexpi merge: %r is new in the assembly; writing it as a new %s", name, _NEW_EQUIPMENT_CLASS)
        for port_name, endpoint in _sync_equipment_ports(doc, item, eq, minter).items():
            port_index[(name, port_name)] = endpoint

    for name, item in source_equipment.items():
        if name not in live_by_name:
            _drop_item_tree(doc, item.id, f"equipment {name!r} was removed from the assembly")

    site_index = _site_index(doc)
    source_segments = _source_segment_by_name(doc)
    live_systems: dict[str, System] = {system.name: system for system in systems}

    # A branched system's legs (see _branch_legs) are named after the ORIGINAL per-segment name
    # _fold_branch_groups stashed on import, so reconciling by leg name -- not by the live system's
    # own (new, combined) name -- is what lets each of the source's several PipingNetworkSegments
    # still find and edit its own item, instead of being read as removed just because the live model
    # merged them into one System.
    seen_names: set[str] = set()
    for system in live_systems.values():
        legs = _branch_legs(system)
        views = [(leg.name, (leg.from_port, leg.to_port)) for leg in legs] if legs else [(system.name, None)]
        for name, endpoints in views:
            seen_names.add(name)
            segment = source_segments.get(name)
            if segment is None:
                segment = _add_segment_item(doc, name, minter)
                logger.info(
                    "dexpi merge: system %r is new in the assembly; writing it as a new %s", name, _NEW_SEGMENT_CLASS
                )
            _sync_segment_connections(doc, segment, system, port_index, site_index, endpoints=endpoints)

    for name, segment in source_segments.items():
        if name not in seen_names:
            _drop_item_tree(doc, segment.id, f"system {name!r} was removed from the assembly")

    return doc


# -- equipment identity -------------------------------------------------------------------------------


def _source_identity(
    doc: DexpiDocument,
) -> tuple[dict[str, DexpiItem], dict[str, DexpiItem], dict[str, DexpiItem]]:
    """``({equipment name: item}, {branch-point name: item}, {instrument name: item})``, using the
    exact naming the importer assigns -- see the module docstring's "Identity" note.

    One function and **one name pool**, in the importer's own order, because that is what makes the
    two agree: ``to_procedural`` names the resolved equipment first (the item's tag, falling back to
    its deduplicated catalog slug), then the branch points (tag, falling back to the item id), then
    the instruments, each against the names already taken. Splitting the pool would let a tee tagged
    the same as a vessel come back under a different name here than the one the live
    ``ada.Equipment`` carries, and the merge would then mint a duplicate instead of finding it.

    Instruments are in this pool for exactly the reason branch points are, and they were added after
    the same failure: a materialised actuator is an ``ada.Equipment`` whose name
    ``equipment_items`` never yields, so without it the writer took the actuator for something adapy
    had authored and minted a fresh ``ProcessEquipment`` on an *unedited* round-trip.
    """
    used_slugs: set[str] = set()
    used_names: set[str] = set()
    equipment: dict[str, DexpiItem] = {}
    for item in equipment_items(doc):
        slug = _dedupe(definition_slug(item), used_slugs)
        base = (item.tag or "").strip() or slug
        equipment[_dedupe(base, used_names)] = item

    junctions: dict[str, DexpiItem] = {}
    for item_id in branch_points(doc):
        item = doc.items[item_id]
        junctions[_dedupe((item.tag or item.id).strip(), used_names)] = item

    instruments: dict[str, DexpiItem] = {}
    for item in instrument_items(doc):
        operated = operated_component(doc, item)
        base = (
            item.tag
            or attribute_lookup.value_of(item, attribute_lookup.ACTUATING_SYSTEM_NUMBER)
            or (f"{operated.tag}-ACT" if operated is not None and operated.tag else None)
            or item.id
        ).strip()
        instruments[_dedupe(base, used_names)] = item
    return equipment, junctions, instruments


def _junction_port_index(
    doc: DexpiDocument, junctions: dict[str, DexpiItem], live_by_name: dict[str, Equipment]
) -> dict[tuple[str, str], tuple[str, str]]:
    """``{(junction name, port name): (item id, node id)}`` for every branch point still in the
    assembly.

    Read straight off the source component's own connection nodes -- the same
    :func:`~ada.cadit.dexpi.equipment_list.nozzle_specs_for` the importer used, so the port names
    match the live equipment's. Deliberately *not* routed through
    :func:`_sync_equipment_ports`: that reconciles ``Nozzle`` children, and a piping component
    carries its nodes on itself, so syncing it would hang nozzles off a ``PipeTee``. The
    consequence, said plainly: a port added to or removed from a materialised junction in Python
    does not write back. Nothing in adapy edits one today, and inventing nozzles on a fitting is a
    worse answer than not writing the edit.

    A junction whose equipment is gone from the assembly is skipped rather than dropped from the
    document: the component belongs to a segment, and the segment-level reconciliation below is
    what decides whether that run survives.
    """
    flow = connection_flow(doc)
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for name, item in junctions.items():
        if name not in live_by_name:
            continue
        for node_id, port_name in port_names(nozzle_specs_for(doc, item, flow)).items():
            out[(name, port_name)] = (item.id, node_id)
    return out


def _dedupe(base: str, used: set[str]) -> str:
    name = base
    suffix = 1
    while name in used:
        suffix += 1
        name = f"{base}-{suffix}"
    used.add(name)
    return name


# -- equipment: ports -----------------------------------------------------------------------------------


def _sync_equipment_ports(
    doc: DexpiDocument, item: DexpiItem, eq: Equipment, minter: _IdMinter
) -> dict[str, tuple[str, str]]:
    """Reconcile ``item``'s nozzle children against ``eq.ports``, and return the resulting
    ``{port name: (item id, node id)}`` a system connection can address.

    A port present live but not in the baseline is new -- a nozzle item is minted for it
    (:func:`_add_nozzle`). A baseline port absent from the live list was removed -- its nozzle item
    (or bare node) is dropped, along with any connection that named it (:func:`_drop_port`).
    Everything else -- an existing port's own tag, diameter or direction -- is left exactly as the
    source wrote it: the one exception is a port whose *system* changed (a rewire), which is not
    visible here at all and is instead read from ``system.ports`` in :func:`_sync_segment_connections`.
    """
    flow = connection_flow(doc)
    baseline = port_names(nozzle_specs_for(doc, item, flow))  # spec id -> port name, before any edit
    baseline_names = set(baseline.values())
    live_names = {port.name for port in eq.ports}

    for spec_id, port_name in baseline.items():
        if port_name not in live_names:
            _drop_port(doc, spec_id, port_name, eq.name)

    for port in eq.ports:
        if port.name not in baseline_names:
            _add_nozzle(doc, item, port, minter)

    # Recomputed after the edits land, so a freshly minted nozzle is included and a dropped one is
    # not -- this is the authoritative id a connection to this port should now name.
    index: dict[str, tuple[str, str]] = {}
    for spec_id, port_name in port_names(nozzle_specs_for(doc, item, flow)).items():
        endpoint = _endpoint_for_spec(doc, spec_id)
        if endpoint is not None:
            index[port_name] = endpoint
    return index


def _endpoint_for_spec(doc: DexpiDocument, spec_id: str) -> tuple[str, str] | None:
    """The ``(item id, node id)`` a connection should name for the nozzle or bare node ``spec_id``
    identifies -- the id :func:`~ada.cadit.dexpi.nozzle_placers.port_names` uses as its key, which is
    a ``Nozzle`` item's own id for an item-based connection or a node id for a bare one."""
    item = doc.items.get(spec_id)
    if item is not None and item.kind is ItemKind.NOZZLE:
        node = next(iter(item.process_nodes), None)
        return (item.id, node.id) if node is not None else None
    found = doc.find_node(spec_id)
    if found is None:
        return None
    owner, node = found
    return owner.id, node.id


def _add_nozzle(doc: DexpiDocument, item: DexpiItem, port: Port, minter: _IdMinter) -> DexpiItem:
    """A new ``Nozzle`` child of ``item`` for a port the live equipment carries but the source did
    not. New rather than a bare node on ``item`` itself, matching how every nozzle in the fixtures
    this branch was built against is modelled."""
    nozzle_id = minter.mint(f"{item.id}-{slugify(port.name) or 'Nozzle'}")
    node = DexpiNode(
        id=f"{nozzle_id}-Node-1",
        ordinal=1,
        owner_id=nozzle_id,
        node_type="process",
        is_anchor=False,
        flow=_flow_token(port.direction),
        tag=port.tag or port.name,
        nominal_diameter=port.nominal_diameter,
    )
    nozzle = DexpiItem(
        id=nozzle_id,
        class_name="Nozzle",
        kind=ItemKind.NOZZLE,
        attributes=[_tag_attribute(port.tag or port.name)],
        nodes=[node],
    )
    doc.add(nozzle, parent_id=item.id)
    return nozzle


def _drop_port(doc: DexpiDocument, spec_id: str, port_name: str, equipment_name: str) -> None:
    reason = f"port {port_name!r} on {equipment_name!r} was removed from the assembly"
    target = doc.items.get(spec_id)
    if target is not None and target.kind is ItemKind.NOZZLE:
        _drop_item_tree(doc, target.id, reason)
        return
    found = doc.find_node(spec_id)
    if found is None:
        return
    owner, node = found
    owner.nodes = [n for n in owner.nodes if n.id != node.id]
    _drop_connections_referencing(doc, node_id=node.id, reason=reason)


def _tag_attribute(value: str | None) -> DexpiAttribute:
    return DexpiAttribute(name=attribute_lookup.SUB_TAG_NAME, value=value, format="string", set_name=_ATTRIBUTE_SET)


def _flow_token(direction: PortDirection | str | None) -> str | None:
    value = getattr(direction, "value", direction)
    if value == PortDirection.IN.value:
        return "in"
    if value == PortDirection.OUT.value:
        return "out"
    return None


# -- equipment: whole items -----------------------------------------------------------------------------


def _add_equipment_item(doc: DexpiDocument, eq: Equipment, minter: _IdMinter) -> DexpiItem:
    item = DexpiItem(
        id=minter.mint("Equipment"),
        class_name=_NEW_EQUIPMENT_CLASS,
        kind=ItemKind.EQUIPMENT,
        attributes=[_tag_attribute(eq.name)],
    )
    doc.add(item)
    return item


# -- systems ----------------------------------------------------------------------------------------


def _source_segment_by_name(doc: DexpiDocument) -> dict[str, DexpiItem]:
    """Segment name -> source item, mirroring ``to_procedural._system_name`` (``<line>/<segment>``,
    falling back through the tags to the ids) -- the other half of the identity note in the module
    docstring."""
    return {
        _segment_name(doc, segment): segment
        for segment in sorted(doc.by_kind(ItemKind.PIPING_SEGMENT), key=lambda item: item.id)
    }


def _segment_name(doc: DexpiDocument, segment: DexpiItem) -> str:
    parent = next((item for item in doc.ancestors(segment.id) if item.kind is ItemKind.PIPING_SYSTEM), None)
    line = None
    if parent is not None:
        line = attribute_lookup.value_of(parent, attribute_lookup.LINE_NUMBER) or parent.tag or parent.id
    number = attribute_lookup.value_of(segment, attribute_lookup.SEGMENT_NUMBER) or segment.tag
    if line and number:
        return f"{line}/{number}"
    if line:
        return f"{line}/{segment.id}"
    return number or segment.id


def _site_index(doc: DexpiDocument) -> dict[str, DexpiItem]:
    """Site-terminal name -> off-page-connector item, the same slug
    ``to_procedural._resolve_endpoint`` derives (``slugify(tag or id)``), which is what
    ``System.connect_site`` names the live ``Port`` with."""
    out: dict[str, DexpiItem] = {}
    for item in doc.by_kind(ItemKind.OFF_PAGE_CONNECTOR):
        out[slugify(item.tag or item.id) or item.id] = item
    return out


def _add_segment_item(doc: DexpiDocument, name: str, minter: _IdMinter) -> DexpiItem:
    """A new, ownerless ``PipingNetworkSegment`` for a system with no source segment. Its
    ``<line>/<segment>`` name has nowhere to go but the segment number -- there is no
    ``PipingNetworkSystem`` to hold a line number for a system this module minted itself."""
    attributes = []
    _, _, number = name.rpartition("/")
    if number and number != name:
        attributes.append(
            DexpiAttribute(name=attribute_lookup.SEGMENT_NUMBER, value=number, format="string", set_name=_ATTRIBUTE_SET)
        )
    item = DexpiItem(
        id=minter.mint("PipingNetworkSegment"),
        class_name=_NEW_SEGMENT_CLASS,
        kind=ItemKind.PIPING_SEGMENT,
        attributes=attributes,
    )
    doc.add(item)
    return item


def _system_endpoints(system: System) -> list[Port]:
    """The two ports a run's connection should be written between -- ``route_system``'s own
    ``ports[0] -> ports[-1]`` contract, so this agrees with what actually got routed.

    Not meaningful for a branched system (see :func:`_branch_legs`) -- its several legs are synced
    individually, each against its own two ports, and never through this."""
    if len(system.ports) >= 2:
        return [system.ports[0], system.ports[-1]]
    return list(system.ports)


def _branch_legs(system: System) -> list["SystemSegment"] | None:
    """``system.segments`` when ``system`` is a branch (two or more legs meeting at a shared
    junction equipment -- see ``ada.topology.routing``), or ``None`` for an ordinary two-port
    system. The writer does not need to re-validate the tree the way routing does: each leg already
    carries its own two fully-resolved ports, which is all reconciliation below reads."""
    return system.segments if len(system.segments) >= 2 else None


def _resolve_live_endpoint(
    port: Port, port_index: dict[tuple[str, str], tuple[str, str]], site_index: dict[str, DexpiItem]
) -> tuple[str, str] | None:
    if port.is_site:
        item = site_index.get(port.name)
        if item is None:
            return None
        node = next(iter(item.process_nodes), None)
        return (item.id, node.id) if node is not None else (item.id, None)
    if port.parent is None:
        return None
    return port_index.get((port.parent.name, port.name))


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


def _segment_boundary(doc: DexpiDocument, segment: DexpiItem) -> list[tuple[str, str | None]]:
    """The endpoints outside ``segment`` today, exactly the way
    ``to_procedural._segment_spec`` finds them: everything a connection owned by the segment names
    that is not one of the segment's own descendants -- with the same two exceptions, an off-page
    connector (composed into the segment but still an end of the run) and a branch point (where the
    run stops and the next one starts, even when this is the segment the file happens to nest the
    tee under). A segment routed through an in-line component has *more* connections than this --
    one per hop -- and none of them should be mistaken for a change just because ``system.ports``
    only ever names the two outermost ones.

    Both exceptions have to track ``to_procedural`` exactly. If this said a tee was interior while
    the importer said it was an end, every run at a junction would compare unequal to its own
    unchanged boundary and be rewritten on a no-op round-trip.
    """
    junctions = branch_points(doc)
    inner = {segment.id} | {
        item_id
        for item_id in _descendants(doc, segment.id)
        if doc.items[item_id].kind is not ItemKind.OFF_PAGE_CONNECTOR and item_id not in junctions
    }
    ends: list[tuple[str, str | None]] = []
    for connection in doc.connections:
        if connection.owner_id != segment.id:
            continue
        for item_id, node_id in (
            (connection.from_item, connection.from_node),
            (connection.to_item, connection.to_node),
        ):
            if item_id is not None and item_id not in inner:
                ends.append((item_id, node_id))
    return ends


def _sorted_pairs(pairs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
    """``pairs``, sorted -- node id normalised to ``""`` for the comparison so a missing one never
    raises comparing ``None`` against a string."""
    return sorted((item_id, node_id or "") for item_id, node_id in pairs)


def _sync_segment_connections(
    doc: DexpiDocument,
    segment: DexpiItem,
    system: System,
    port_index: dict[tuple[str, str], tuple[str, str]],
    site_index: dict[str, DexpiItem],
    endpoints: tuple[Port, Port] | None = None,
) -> None:
    """Regenerate ``segment``'s connection from ``system.ports`` -- the one place a *rewire* (the
    same two ports, or a different pair) actually lands. Compared against the segment's *external*
    boundary (:func:`_segment_boundary`), not its raw connection list, so a run that passes through
    an in-line component keeps every one of those hops -- and its ``.raw`` -- untouched as long as
    its two outer ends have not moved. Replacing them with the single direct connection
    ``system.ports`` can express is only correct once they actually have.

    ``endpoints``, when given, overrides ``_system_endpoints(system)`` -- this is how a branched
    system's individual legs (see :func:`_branch_legs`) each sync against their OWN source segment
    instead of the meaningless ``system.ports[0]``/``[-1]`` a multi-leg system's flat port list would
    give ``_system_endpoints``.
    """
    resolved = [
        _resolve_live_endpoint(port, port_index, site_index) for port in (endpoints or _system_endpoints(system))
    ]
    boundary = _segment_boundary(doc, segment)

    if len(resolved) == 2 and all(end is not None for end in resolved):
        if _sorted_pairs(boundary) == _sorted_pairs(resolved):
            return
        doc.connections = [c for c in doc.connections if c.owner_id != segment.id]
        (from_item, from_node), (to_item, to_node) = resolved
        doc.connections.append(
            DexpiConnection(
                from_item=from_item, from_node=from_node, to_item=to_item, to_node=to_node, owner_id=segment.id
            )
        )
        return

    if any(c.owner_id == segment.id for c in doc.connections):
        doc.connections = [c for c in doc.connections if c.owner_id != segment.id]
        logger.warning(
            "dexpi merge: system %r has fewer than two resolvable endpoints; dropping its connection",
            system.name,
        )


# -- dropping -----------------------------------------------------------------------------------------


def _drop_item_tree(doc: DexpiDocument, item_id: str, reason: str) -> None:
    """Remove ``item_id`` and every descendant, and every connection naming any of them, logging
    once per item so a dropped edit is never silent."""
    item = doc.items.get(item_id)
    if item is None:
        return
    for child_id in list(item.child_ids):
        _drop_item_tree(doc, child_id, reason)

    if item.parent_id is not None:
        parent = doc.items.get(item.parent_id)
        if parent is not None and item_id in parent.child_ids:
            parent.child_ids.remove(item_id)
        # ``_append_unmodelled`` (write_proteus.py) echoes a raw child that is no longer in
        # doc.items back in verbatim -- that guard exists for content the reader never made an
        # item of, and a just-deleted item looks exactly like that unless its raw element is also
        # detached from the parent's raw tree here.
        if parent is not None and parent.raw is not None and item.raw is not None:
            try:
                parent.raw.remove(item.raw)
            except ValueError:
                pass
    elif item_id in doc.root_ids:
        doc.root_ids.remove(item_id)

    del doc.items[item_id]
    _drop_connections_referencing(doc, item_id=item_id, reason=reason)
    for node in item.nodes:
        _drop_connections_referencing(doc, node_id=node.id, reason=reason)
    # A PipingNetworkSegment owns its own connections (``connection.owner_id``) without naming
    # itself at either end of them -- those would otherwise survive, orphaned, referencing items
    # this same drop just removed.
    if any(c.owner_id == item_id for c in doc.connections):
        logger.warning("dexpi merge: dropping connection(s) owned by %r: %s", item_id, reason)
        doc.connections = [c for c in doc.connections if c.owner_id != item_id]
    logger.warning("dexpi merge: dropping %s %r (tag=%r): %s", item.class_name, item_id, item.tag, reason)


def _drop_connections_referencing(
    doc: DexpiDocument, *, item_id: str | None = None, node_id: str | None = None, reason: str
) -> None:
    keep = []
    for connection in doc.connections:
        hit = (item_id is not None and item_id in (connection.from_item, connection.to_item)) or (
            node_id is not None and node_id in (connection.from_node, connection.to_node)
        )
        if hit:
            logger.warning(
                "dexpi merge: dropping connection %r -> %r: %s", connection.from_item, connection.to_item, reason
            )
            continue
        keep.append(connection)
    doc.connections = keep


class _IdMinter:
    """Fresh, collision-free ids for items the merge adds. The ``adapy`` provenance is unmistakable
    in the id itself, and re-running the merge after the same edit mints the next number rather than
    colliding with what an earlier write already produced."""

    def __init__(self, doc: DexpiDocument):
        self._doc = doc
        self._counters: dict[str, itertools.count] = {}

    def mint(self, prefix: str) -> str:
        counter = self._counters.setdefault(prefix, itertools.count(1))
        while True:
            candidate = f"{prefix}-adapy{next(counter)}"
            if candidate not in self._doc.items:
                return candidate


# ---------------------------------------------------------------------------
# From scratch
# ---------------------------------------------------------------------------
def build_from_scratch(model, flavour: str = "proteus") -> DexpiDocument:
    """A new :class:`DexpiDocument` from ``model`` alone -- no source document, no echo.

    **Lossy by construction.** Only what a live ``ada.Equipment``/``ada.api.systems.System`` carries
    survives: a name, a port list (position/direction/tag/diameter, no nozzle-level DEXPI class), a
    two-ended connection. There is no chamber, no instrumentation, no piping class or spec, no RDL
    URI and no schematic drawing to write, because none of it exists on the live objects -- a new
    equipment is written as the abstract ``ProcessEquipment`` and a new system as a bare
    ``PipingNetworkSegment`` with no owning ``PipingNetworkSystem``. Use this for a model with no
    DEXPI provenance at all (one built by hand); a model that *was* read from DEXPI should go
    through :func:`merge_into_document` instead, which keeps everything this cannot.
    """
    flavour_enum = DexpiFlavour(flavour)
    doc = DexpiDocument(flavour=flavour_enum, header=DexpiHeader(project=model.name))
    minter = _IdMinter(doc)

    equipment = sorted(model.equipment, key=lambda eq: eq.name)
    port_index: dict[tuple[str, str], tuple[str, str]] = {}
    for eq in equipment:
        item_id = minter.mint("Equipment")
        item = DexpiItem(
            id=item_id, class_name=_NEW_EQUIPMENT_CLASS, kind=ItemKind.EQUIPMENT, attributes=[_tag_attribute(eq.name)]
        )
        doc.add(item)
        for ordinal, port in enumerate(eq.ports, start=1):
            node = DexpiNode(
                id=f"{item_id}-Node-{ordinal}",
                ordinal=ordinal,
                owner_id=item_id,
                node_type="process",
                is_anchor=False,
                flow=_flow_token(port.direction),
                tag=port.tag or port.name,
                nominal_diameter=port.nominal_diameter,
            )
            item.nodes.append(node)
            port_index[(eq.name, port.name)] = (item_id, node.id)

    site_index: dict[str, DexpiItem] = {}
    for system in sorted(model.systems, key=lambda s: s.name):
        # A branch (see _branch_legs) has no single PipingNetworkSystem to fold into and no source
        # tee item to synthesise -- lossy exactly the way a new equipment is, this writes one bare
        # PipingNetworkSegment per leg rather than one two-ended segment that would silently connect
        # two of its leaves straight through the junction and drop the rest.
        legs = _branch_legs(system)
        leg_ports = [(leg.from_port, leg.to_port) for leg in legs] if legs else [tuple(_system_endpoints(system))]

        for ports in leg_ports:
            segment_id = minter.mint("PipingNetworkSegment")
            segment = DexpiItem(id=segment_id, class_name=_NEW_SEGMENT_CLASS, kind=ItemKind.PIPING_SEGMENT)
            doc.add(segment)

            endpoints = [_from_scratch_endpoint(doc, port, port_index, site_index, minter) for port in ports]

            if len(endpoints) == 2 and all(end is not None for end in endpoints):
                (from_item, from_node), (to_item, to_node) = endpoints
                doc.connections.append(
                    DexpiConnection(
                        from_item=from_item, from_node=from_node, to_item=to_item, to_node=to_node, owner_id=segment_id
                    )
                )
            else:
                logger.warning(
                    "dexpi from_scratch: system %r does not have two resolvable endpoints; writing it with no "
                    "connection",
                    system.name,
                )

    return doc


def _from_scratch_endpoint(
    doc: DexpiDocument,
    port: Port,
    port_index: dict[tuple[str, str], tuple[str, str]],
    site_index: dict[str, DexpiItem],
    minter: _IdMinter,
) -> tuple[str, str] | None:
    """The endpoint for one system port: an equipment node from ``port_index`` (built while the
    equipment loop above wrote every port), or a freshly minted off-page connector for a site
    terminal -- one per distinct site name, reused if two systems share it."""
    if not port.is_site:
        if port.parent is None:
            return None
        return port_index.get((port.parent.name, port.name))

    item = site_index.get(port.name)
    if item is None:
        item = DexpiItem(
            id=minter.mint("PipeOffPageConnector"),
            class_name=(
                "FlowInPipeOffPageConnector" if port.direction == PortDirection.IN else "FlowOutPipeOffPageConnector"
            ),
            kind=ItemKind.OFF_PAGE_CONNECTOR,
            attributes=[_tag_attribute(port.name)],
        )
        doc.add(item)
        site_index[port.name] = item
    node = DexpiNode(
        id=f"{item.id}-Node-{len(item.nodes) + 1}",
        ordinal=len(item.nodes) + 1,
        owner_id=item.id,
        node_type="process",
        is_anchor=False,
    )
    item.nodes.append(node)
    return item.id, node.id
