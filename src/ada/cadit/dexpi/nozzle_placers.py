"""Turning a P&ID's nozzles into 3D positions on an equipment box.

A DEXPI item tells us exactly *which* nozzles it has -- their tags, sizes, piping class and flow
direction -- and nothing at all about where they sit in space, because a P&ID has no space. The
placers here answer that half: given an equipment envelope and the item's nozzles, they produce
equipment-local ``(position, direction_vector)`` pairs in the frame :class:`ada.Port` uses -- origin
at the footprint centre of the base, so the box spans ``x in [-lx/2, lx/2]``, ``y in [-ly/2, ly/2]``
and ``z in [0, lz]``.

Nozzle *count* varies item by item, so nothing here is a fixed per-class port list: the class
default picks a **strategy** (see ``resources/dexpi_equipment_defaults.json``) and the strategy
lays out whatever nozzles the item actually has.

**Everything is deterministic.** Nozzles are sorted by ID with a natural-number key before anything
is placed, groups are built by list comprehension rather than by iterating a dict or a set, and the
uniqueness pass at the end walks that same fixed order. A re-import must produce byte-identical
positions or every roundtrip diff is noise.

**Categories are load-bearing, not cosmetic.** ``System.connect`` raises on a category mismatch and
the procedural compiler then drops the whole system with only a warning, so a nozzle typed
``process`` when it should be ``signal`` silently loses a run. :func:`category_for` is therefore
explicit about the one trap in the DEXPI hierarchy: ``Nozzle`` itself inherits from
``ActuatingElectricalLocation`` *and* ``SensingLocation``, so a naive "is it an electrical location?"
test types every process nozzle as electrical. The nozzle branch is tested first for exactly that
reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from ada.core.text_utils import slugify

from . import attributes, class_table
from .model import DexpiItem, DexpiNode
from .read.conventions import (
    DIRECTION_IN,
    DIRECTION_INOUT,
    DIRECTION_OUT,
    ELECTRICAL_FACE,
    ELECTRICAL_SUPERTYPES,
    SIGNAL_FACE,
    SIGNAL_SUPERTYPES,
    Z_DRAIN,
    Z_ELECTRICAL,
    Z_SHELL,
    Z_SIGNAL,
    PortDirectionToken,
    direction_for,
)
from .read.naming import unique_name

__all__ = [
    "NozzleSpec",
    "PLACERS",
    "category_for",
    "direction_for",
    "nozzle_from_item",
    "nozzle_from_node",
    "place_nozzles",
    "port_names",
]

# Faces, reserved heights and the category supertypes are conventions, held in
# :mod:`ada.cadit.dexpi.read.conventions` alongside every other one the import runs on.

_DIGITS = re.compile(r"(\d+)")


@dataclass(frozen=True)
class NozzleSpec:
    """One connection to place: everything the placer and the emitted port need, and nothing that
    ties it to a wire format.

    ``hint`` is the nozzle's position in the *schematic* frame when the source document gave one.
    It is a tie-break only -- drawing millimetres are never plant coordinates -- and today it is
    unused by the strategies; it rides along so a future face-selection rule has it.
    """

    id: str
    name: str
    tag: str | None = None
    category: str = "process"
    direction: PortDirectionToken = DIRECTION_INOUT
    nominal_diameter: float | None = None
    spec: str | None = None
    hint: tuple[float, float] | None = None


def category_for(class_name: str | None = None, node_type: str | None = None) -> str:
    """The port category for a connection of DEXPI class ``class_name`` on a node of type
    ``node_type``.

    An explicit node type wins, because an emitter that bothered to type the node knows more than
    the class does. Otherwise the class decides, and the ``Nozzle`` family is resolved first: a
    plain ``Nozzle`` is a process connection even though the spec also makes it an
    ``ActuatingElectricalLocation`` and a ``SensingLocation``. Anything unrecognised is
    ``process`` -- the category a piping run needs, and the one an item on a P&ID almost always is.
    """
    typed = _category_from_node_type(node_type)
    if typed is not None:
        return typed

    if not class_name:
        return "process"

    name = class_table.resolve(class_name)
    if class_table.is_a(name, "Nozzle"):
        return "signal" if class_table.is_a(name, "InstrumentNozzle") else "process"
    if any(class_table.is_a(name, supertype) for supertype in SIGNAL_SUPERTYPES):
        return "signal"
    if any(class_table.is_a(name, supertype) for supertype in ELECTRICAL_SUPERTYPES):
        return "electrical"
    return "process"


def _category_from_node_type(node_type: str | None) -> str | None:
    if not node_type:
        return None
    token = node_type.strip().lower()
    if "signal" in token or "instrument" in token:
        return "signal"
    if "electric" in token or "power" in token:
        return "electrical"
    if "process" in token or "piping" in token:
        return "process"
    return None


def nozzle_from_node(
    node: DexpiNode,
    *,
    owner_class: str | None = None,
    name: str | None = None,
    tag: str | None = None,
    spec: str | None = None,
    category: str | None = None,
    flow: str | None = None,
) -> NozzleSpec:
    """A :class:`NozzleSpec` for a bare connection node -- an item whose connection points are on
    the item itself rather than on child ``Nozzle`` objects.

    ``flow`` is the fallback used when the node itself does not declare one; see
    :func:`nozzle_from_item` for why it exists.
    """
    node_tag = tag if tag is not None else node.tag
    return NozzleSpec(
        id=node.id,
        name=slugify(name or node_tag or node.id) or slugify(node.id) or "port",
        tag=node_tag,
        category=category or category_for(owner_class, node.node_type),
        direction=direction_for(node.flow or flow),
        nominal_diameter=node.nominal_diameter,
        spec=spec,
        hint=_hint(node),
    )


def nozzle_from_item(item: DexpiItem, *, spec: str | None = None, flow: str | None = None) -> NozzleSpec:
    """A :class:`NozzleSpec` for a DEXPI ``Nozzle`` item.

    Tag, nominal diameter and piping class come off the nozzle's own attributes; flow comes from
    its first process node, which is where Proteus records ``FlowIn``/``FlowOut``. The anchor node
    is skipped -- it is the symbol's insertion point, not a connection.

    ``flow`` (``"in"``/``"out"``) is the fallback for when the node does not declare one, and it is
    not an optional nicety: **DEXPI 2.0 has no** ``FlowIn``/``FlowOut`` **at all** -- direction is
    implied by which end of a ``Pipe`` a node sits on -- so without it every port of a 2.0 document
    would come out ``INOUT`` and every vessel would have its feed and its draw-off on the shell.
    :func:`ada.cadit.dexpi.equipment_list.connection_flow` derives the fallback from the
    connectivity, which answers for both flavours.
    """
    node = next(iter(item.process_nodes), None)
    tag = item.tag
    return NozzleSpec(
        id=item.id,
        name=slugify(tag or item.id) or slugify(item.id) or "port",
        tag=tag,
        category=category_for(item.class_name, node.node_type if node is not None else None),
        direction=direction_for((node.flow if node is not None else None) or flow),
        nominal_diameter=attributes.nominal_diameter_of(item),
        spec=spec if spec is not None else attributes.spec_of(item),
        hint=_hint(node) if node is not None else None,
    )


def _hint(node: DexpiNode | None) -> tuple[float, float] | None:
    if node is None or node.position is None:
        return None
    return (float(node.position[0]), float(node.position[1]))


def place_nozzles(
    bbox: Any,
    nozzles: Iterable[NozzleSpec],
    strategy: str = "generic",
) -> list[dict]:
    """Lay ``nozzles`` out on the ``bbox`` envelope and return catalog port documents.

    ``bbox`` is either ``[lx, ly, lz]`` or the catalog's ``{"lx": .., "ly": .., "lz": ..}``. The
    result is a list of dicts in the shape ``CatalogPort`` validates (``ada.comms.rest.catalog``),
    ordered by nozzle ID, with names uniquified so the document always passes
    ``validate_equipment_doc``. Positions are rounded to a micrometre: a float wobble in the sixth
    decimal is not a change, and rounding it away keeps xlsx and JSON round-trips exact.
    """
    placer = PLACERS.get(strategy)
    if placer is None:
        raise ValueError(f"unknown nozzle-layout strategy {strategy!r} (known: {sorted(PLACERS)})")

    lx, ly, lz = _envelope(bbox)
    ordered = _ordered(nozzles)
    if not ordered:
        return []

    placements = placer(lx, ly, lz, ordered)
    _ensure_unique(placements)

    names = _unique_names(ordered)
    ports: list[dict] = []
    for spec, name, (position, direction_vector) in zip(ordered, names, placements):
        ports.append(
            {
                "name": name,
                "position": [round(float(v), 6) for v in position],
                "direction_vector": [round(float(v), 6) for v in direction_vector],
                "direction": spec.direction,
                "category": spec.category,
                "tag": spec.tag,
                "nominal_diameter": (
                    round(float(spec.nominal_diameter), 6) if spec.nominal_diameter is not None else None
                ),
                "spec": spec.spec,
            }
        )
    return ports


def port_names(nozzles: Iterable[NozzleSpec]) -> dict[str, str]:
    """Nozzle ID -> the port name :func:`place_nozzles` emits for that nozzle.

    The importer writes ``SystemConnection.PORT`` from this map, so a run's endpoint names the same
    string the catalog document does and the wiring is name-matched end to end. It has to be asked
    for rather than assumed, because :func:`place_nozzles` deduplicates: two nozzles sharing a tag
    do not both keep it, and only the placer knows which one won.
    """
    ordered = _ordered(nozzles)
    return dict(zip((spec.id for spec in ordered), _unique_names(ordered)))


# ---------------------------------------------------------------------------
# Ordering and naming
# ---------------------------------------------------------------------------
def _ordered(nozzles: Iterable[NozzleSpec]) -> list[NozzleSpec]:
    """The nozzles in the one order everything downstream assumes: by ID, naturally sorted."""
    return sorted(nozzles, key=lambda spec: _sort_key(spec.id))


def _sort_key(value: str) -> tuple:
    """Natural sort key: ``N2`` before ``N10``, and total over any pair of strings.

    Each chunk is tagged with its kind before comparison so a number never has to be compared with
    a word -- the reason a plain ``re.split`` key raises on mixed IDs.
    """
    return tuple((0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk) for chunk in _DIGITS.split(value) if chunk)


def _unique_names(specs: Sequence[NozzleSpec]) -> list[str]:
    """Port names for ``specs``, deduplicated in order.

    ``Equipment.add_port`` raises on a duplicate name and ``validate_equipment_doc`` rejects the
    document, but two nozzles with the same tag is a thing real files do. The first keeps the bare
    name and the rest get ``-2``, ``-3``; because the input is already sorted by ID, which one wins
    is stable across imports.
    """
    seen: set[str] = set()
    return [unique_name(spec.name, seen, fallback="port", style="on-name") for spec in specs]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
_Placement = tuple[tuple[float, float, float], tuple[float, float, float]]
_Indexed = list[tuple[int, NozzleSpec]]


def _envelope(bbox: Any) -> tuple[float, float, float]:
    if isinstance(bbox, dict):
        values = (bbox.get("lx", 1.0), bbox.get("ly", 1.0), bbox.get("lz", 1.0))
    else:
        values = tuple(bbox)  # type: ignore[assignment]
    if len(values) != 3:
        raise ValueError(f"bbox must be [lx, ly, lz] or {{lx, ly, lz}}, got {bbox!r}")
    lx, ly, lz = (float(v) for v in values)
    if lx <= 0 or ly <= 0 or lz <= 0:
        raise ValueError(f"bbox must be positive in every axis, got {[lx, ly, lz]}")
    return lx, ly, lz


def _spread(length: float, index: int, count: int) -> float:
    """The ``index``-th of ``count`` evenly spaced offsets across a face of size ``length``,
    centred on zero and never touching either edge."""
    return -length / 2.0 + length * (index + 1) / (count + 1)


def _on_face(face: str, lx: float, ly: float, lz: float, index: int, count: int, z: float) -> _Placement:
    """A point on one vertical face, spread along that face's free axis, with the outward normal."""
    if face == "+X":
        return (lx / 2.0, _spread(ly, index, count), z), (1.0, 0.0, 0.0)
    if face == "-X":
        return (-lx / 2.0, _spread(ly, index, count), z), (-1.0, 0.0, 0.0)
    if face == "+Y":
        return (_spread(lx, index, count), ly / 2.0, z), (0.0, 1.0, 0.0)
    if face == "-Y":
        return (_spread(lx, index, count), -ly / 2.0, z), (0.0, -1.0, 0.0)
    raise ValueError(f"not a vertical face: {face!r}")


def _on_top(lx: float, ly: float, lz: float, index: int, count: int) -> _Placement:
    return (_spread(lx, index, count), 0.0, lz), (0.0, 0.0, 1.0)


def _perimeter(lx: float, ly: float, fraction: float) -> tuple[float, float, tuple[float, float, float]]:
    """A point on the footprint rectangle at ``fraction`` of the way round it, plus its outward
    normal. Starts at the ``-Y`` end of the ``+X`` face and runs anticlockwise, so a fan-out is
    stable under any envelope."""
    perimeter = 2.0 * (lx + ly)
    distance = (fraction % 1.0) * perimeter
    if distance < ly:
        return lx / 2.0, -ly / 2.0 + distance, (1.0, 0.0, 0.0)
    distance -= ly
    if distance < lx:
        return lx / 2.0 - distance, ly / 2.0, (0.0, 1.0, 0.0)
    distance -= lx
    if distance < ly:
        return -lx / 2.0, ly / 2.0 - distance, (-1.0, 0.0, 0.0)
    distance -= ly
    return -lx / 2.0 + distance, -ly / 2.0, (0.0, -1.0, 0.0)


def _tangent(direction_vector: tuple[float, float, float]) -> tuple[float, float, float]:
    """A unit vector along the face that ``direction_vector`` points out of -- the axis a colliding
    port is nudged along."""
    dx, dy, dz = direction_vector
    if abs(dz) > 0.5:
        return (1.0, 0.0, 0.0)
    if abs(dx) > 0.5:
        return (0.0, 1.0, 0.0)
    return (1.0, 0.0, 0.0)


def _ensure_unique(placements: list[_Placement]) -> None:
    """Last-resort guarantee that no two ports share a position, in place.

    The strategies reserve a distinct height per service and spread each group across its face, so
    a collision needs an unusual nozzle count on an unusual envelope. It is still possible, and two
    ports at one point would route as one, so a repeat is nudged along its own face by a widening
    step. Deterministic: the input order is already fixed, and so is the step sequence.
    """
    seen: set[tuple[int, int, int]] = set()
    for i, (position, direction_vector) in enumerate(placements):
        key = _key(position)
        if key not in seen:
            seen.add(key)
            continue
        tangent = _tangent(direction_vector)
        attempt = 1
        while key in seen:
            step = 0.02 * ((attempt + 1) // 2) * (1 if attempt % 2 else -1)
            position = tuple(p + t * step for p, t in zip(position, tangent))  # type: ignore[assignment]
            key = _key(position)
            attempt += 1
        placements[i] = (position, direction_vector)
        seen.add(key)


def _key(position: tuple[float, float, float]) -> tuple[int, int, int]:
    """Position as micrometre integers -- the resolution the emitted ports are rounded to, so two
    ports compare equal here exactly when they compare equal in the document."""
    x, y, z = (int(round(v * 1_000_000)) for v in position)
    return x, y, z


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
def _split(nozzles: Sequence[NozzleSpec]) -> tuple[_Indexed, _Indexed, _Indexed]:
    """(process, signal, electrical), each keeping its index in the caller's ordering."""
    indexed = list(enumerate(nozzles))
    signal = [pair for pair in indexed if pair[1].category == "signal"]
    electrical = [pair for pair in indexed if pair[1].category == "electrical"]
    process = [pair for pair in indexed if pair[1].category not in ("signal", "electrical")]
    return process, signal, electrical


def _services(
    out: list[_Placement | None],
    lx: float,
    ly: float,
    lz: float,
    signal: _Indexed,
    electrical: _Indexed,
) -> None:
    """Place the non-process connections. Common to every strategy: instrument connections on the
    ``+Y`` face high up, electrical supply on the ``+X`` face low down, each at its own reserved
    height so it can never coincide with a process nozzle on the same face."""
    for i, (index, _) in enumerate(signal):
        out[index] = _on_face(SIGNAL_FACE, lx, ly, lz, i, len(signal), Z_SIGNAL * lz)
    for i, (index, _) in enumerate(electrical):
        out[index] = _on_face(ELECTRICAL_FACE, lx, ly, lz, i, len(electrical), Z_ELECTRICAL * lz)


def _finish(out: list[_Placement | None], lx: float, ly: float, lz: float) -> list[_Placement]:
    """Fill any slot a strategy left empty with a top-face point, so a placer can never return
    ``None`` for a nozzle it did not think about."""
    missing = [i for i, placement in enumerate(out) if placement is None]
    for i, index in enumerate(missing):
        out[index] = _on_top(lx, ly, lz, i, len(missing))
    return [placement for placement in out if placement is not None]


def _vessel(lx: float, ly: float, lz: float, nozzles: Sequence[NozzleSpec]) -> list[_Placement]:
    """Columns, tanks, separators, filters: feeds enter the top, product leaves the bottom, and
    anything undirected sits on the shell.

    Inlets go on the top head spread along X; outlets go low on the ``-X`` face, which is where a
    drain or a bottoms draw-off belongs and which keeps them clear of the top nozzles; the rest fan
    out around the shell at mid height.
    """
    out: list[_Placement | None] = [None] * len(nozzles)
    process, signal, electrical = _split(nozzles)
    _services(out, lx, ly, lz, signal, electrical)

    top = [pair for pair in process if pair[1].direction == DIRECTION_IN]
    bottom = [pair for pair in process if pair[1].direction == DIRECTION_OUT]
    shell = [pair for pair in process if pair[1].direction not in (DIRECTION_IN, DIRECTION_OUT)]

    for i, (index, _) in enumerate(top):
        out[index] = _on_top(lx, ly, lz, i, len(top))
    for i, (index, _) in enumerate(bottom):
        out[index] = _on_face("-X", lx, ly, lz, i, len(bottom), Z_DRAIN * lz)
    for i, (index, _) in enumerate(shell):
        x, y, normal = _perimeter(lx, ly, (i + 0.5) / len(shell))
        out[index] = ((x, y, Z_SHELL * lz), normal)
    return _finish(out, lx, ly, lz)


def _pump(lx: float, ly: float, lz: float, nozzles: Sequence[NozzleSpec]) -> list[_Placement]:
    """Pumps, compressors, blowers and fans: suction in on the ``-X`` side at mid height,
    discharge out of the top.

    The same layout ``create_pump`` uses in :mod:`ada.topo_model.equipment`, so an imported pump and
    a hand-built one present the same nozzle geometry to the router. Undirected process nozzles
    (a vent, a drain, a seal flush) take the ``-Y`` face.
    """
    out: list[_Placement | None] = [None] * len(nozzles)
    process, signal, electrical = _split(nozzles)
    _services(out, lx, ly, lz, signal, electrical)

    suction = [pair for pair in process if pair[1].direction == DIRECTION_IN]
    discharge = [pair for pair in process if pair[1].direction == DIRECTION_OUT]
    auxiliary = [pair for pair in process if pair[1].direction not in (DIRECTION_IN, DIRECTION_OUT)]

    for i, (index, _) in enumerate(suction):
        out[index] = _on_face("-X", lx, ly, lz, i, len(suction), Z_SHELL * lz)
    for i, (index, _) in enumerate(discharge):
        out[index] = _on_top(lx, ly, lz, i, len(discharge))
    for i, (index, _) in enumerate(auxiliary):
        out[index] = _on_face("-Y", lx, ly, lz, i, len(auxiliary), Z_SHELL * lz)
    return _finish(out, lx, ly, lz)


def _exchanger(lx: float, ly: float, lz: float, nozzles: Sequence[NozzleSpec]) -> list[_Placement]:
    """Heat exchangers: process connections pair up on the two ends.

    Alternating ends by position in the sorted order puts a shell/tube pair at opposite faces
    without needing to know which service a nozzle carries -- the P&ID does not reliably say. Each
    end spreads its nozzles vertically over the middle half of the envelope.
    """
    out: list[_Placement | None] = [None] * len(nozzles)
    process, signal, electrical = _split(nozzles)
    _services(out, lx, ly, lz, signal, electrical)

    ends: dict[str, _Indexed] = {"-X": [], "+X": []}
    for i, pair in enumerate(process):
        ends["-X" if i % 2 == 0 else "+X"].append(pair)
    for face in ("-X", "+X"):
        group = ends[face]
        for i, (index, _) in enumerate(group):
            z = lz * (0.25 + 0.5 * (i + 1) / (len(group) + 1))
            position, normal = _on_face(face, lx, ly, lz, 0, 1, z)
            out[index] = (position, normal)
    return _finish(out, lx, ly, lz)


def _generic(lx: float, ly: float, lz: float, nozzles: Sequence[NozzleSpec]) -> list[_Placement]:
    """Anything with no better rule: an even fan-out around the footprint at mid height, in nozzle
    ID order. Unremarkable on purpose -- it must never look like a considered layout."""
    out: list[_Placement | None] = [None] * len(nozzles)
    process, signal, electrical = _split(nozzles)
    _services(out, lx, ly, lz, signal, electrical)

    for i, (index, _) in enumerate(process):
        x, y, normal = _perimeter(lx, ly, (i + 0.5) / len(process))
        out[index] = ((x, y, Z_SHELL * lz), normal)
    return _finish(out, lx, ly, lz)


#: Strategy name -> placer, as named by ``resources/dexpi_equipment_defaults.json``.
PLACERS: dict[str, Callable[[float, float, float, Sequence[NozzleSpec]], list[_Placement]]] = {
    "vessel": _vessel,
    "pump": _pump,
    "exchanger": _exchanger,
    "generic": _generic,
}
