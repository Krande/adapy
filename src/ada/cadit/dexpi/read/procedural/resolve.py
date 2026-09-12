"""From a DEXPI segment to a two-ended system entity: endpoints, identity and resolution.

Everything here settles *what is connected to what* and nothing about where it sits. An
:class:`_Endpoint` is one end of a run on its way to a
:class:`~ada.topology.entities.SystemConnection`; :class:`_Index` is the map from DEXPI identity
(a nozzle id, a bare node id, an equipment or chamber id) to the adapy name and port the import
placed it under; :func:`_segment_spec` and :func:`_signal_specs` turn a piping segment and a signal
line respectively into a :class:`_SegmentSpec`, or report exactly why they could not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from ada.core.text_utils import slugify
from ada.topology.entities import SystemConnection, TopoSystem

from ... import attributes, class_table
from ...equipment_list import ResolvedEquipment, signal_lines, signal_terminal
from ...model import DexpiDocument, DexpiItem, ItemKind
from ..connectivity import ConnectionIndex
from ..conventions import DIRECTION_IN, DIRECTION_OUT, PortDirectionToken
from .reports import DexpiImportReport

__all__ = [
    "_Endpoint",
    "_Index",
    "_SegmentSpec",
    "_component_metadata",
    "_descendants",
    "_off_page_direction",
    "_resolve_endpoint",
    "_routable_segments",
    "_segment_spec",
    "_signal_endpoint",
    "_signal_specs",
    "_system_name",
    "_system_type",
]


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


def _segment_spec(
    doc: DexpiDocument,
    segment: DexpiItem,
    index: _Index,
    report: DexpiImportReport,
    junctions: dict[str, list[str]],
    connections: ConnectionIndex,
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

    ends = [
        _Endpoint(item_id=item_id, node_id=node_id, role=role)
        for item_id, node_id, role in connections.ends_of(segment.id)
        if item_id not in inner
    ]

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
