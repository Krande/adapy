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

from ada.comms.rest.catalog import slugify, validate_equipment_doc
from ada.serialize.xlsx import WorkbookSerializer

from . import attributes, class_table
from .equipment_defaults import build_default_doc
from .model import DexpiDocument, DexpiItem, ItemKind
from .nozzle_placers import NozzleSpec, nozzle_from_item, nozzle_from_node, port_names

__all__ = [
    "EquipmentTypeRow",
    "NozzleRow",
    "ResolvedEquipment",
    "branch_points",
    "connection_flow",
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


def equipment_items(doc: DexpiDocument) -> list[DexpiItem]:
    """The items an equipment definition is wanted for, in ID order.

    Membership is decided by ``is_a(cls, "ProcessEquipment")`` and nothing else. That is the
    explicit rule the class hierarchy demands: there is no DEXPI class called ``Equipment`` (that
    is only the Proteus element tag), and ``Chamber`` and ``Nozzle`` come straight off
    ``Core/ConceptualObject`` rather than off ``ProcessEquipment``, so neither is picked up here.
    Equipment nested inside other equipment is skipped too -- it is folded into its owner, the same
    way a chamber is.
    """
    out = [item for item in doc.items.values() if class_table.is_a(item.class_name, "ProcessEquipment")]
    owned = {
        item.id
        for item in out
        if any(class_table.is_a(a.class_name, "ProcessEquipment") for a in doc.ancestors(item.id))
    }
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
    owners: dict[str, set[str]] = {}
    for connection in doc.connections:
        owner = doc.items.get(connection.owner_id) if connection.owner_id else None
        if owner is None or owner.kind is not ItemKind.PIPING_SEGMENT:
            continue
        for item_id in (connection.from_item, connection.to_item):
            item = doc.items.get(item_id) if item_id else None
            if item is not None and item.kind is ItemKind.PIPING_COMPONENT:
                owners.setdefault(item.id, set()).add(owner.id)
    return {item_id: sorted(segments) for item_id, segments in sorted(owners.items()) if len(segments) > 1}


def connection_flow(doc: DexpiDocument) -> dict[str, str]:
    """Nozzle/node ID -> ``"in"`` or ``"out"``, derived from the connectivity graph.

    Proteus records a node's flow direction on the node itself (``@FlowIn``/``@FlowOut``); **DEXPI
    2.0 records none at all** -- direction is implied by which end of a ``Pipe`` the node sits on.
    So the graph is asked instead: an item at the SOURCE end of a connection has fluid leaving it
    (an outlet), one at the TARGET end has fluid arriving (an inlet). Used only as the fallback for
    a node that does not declare its own flow, so a Proteus document is unaffected and a 2.0 one
    stops producing nothing but ``INOUT`` ports.
    """
    out: dict[str, str] = {}
    for connection in doc.connections:
        for item_id, node_id, flow in (
            (connection.from_item, connection.from_node, "out"),
            (connection.to_item, connection.to_node, "in"),
        ):
            for key in (item_id, node_id):
                if key is not None:
                    out.setdefault(key, flow)
    return out


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

    ``overrides`` is a definition list -- a path to a JSON/XLSX file, an already-loaded dict, or
    None. Its keys are matched against the item's tag first and its DEXPI class second, so a
    per-tag entry wins over a per-class one, and both win over the shipped class default.

    Every document is validated on the way out, including the port-name uniqueness the catalog
    enforces, so nothing that leaves here can fail later inside the compiler.
    """
    table = _as_definitions(overrides)
    flow = connection_flow(dexpi_doc)
    out: list[ResolvedEquipment] = []
    used: set[str] = set()

    for item in equipment_items(dexpi_doc):
        class_name = class_table.resolve(item.class_name)
        tag = item.tag
        slug = _unique_slug(definition_slug(item), used)

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
def _as_definitions(overrides: Any) -> dict[str, dict]:
    if overrides is None:
        return {}
    if isinstance(overrides, (str, pathlib.Path)):
        return load_equipment_definitions(overrides)
    if isinstance(overrides, dict):
        return {str(key): dict(value) for key, value in overrides.items()}
    raise TypeError(f"overrides must be a path, a dict or None, got {type(overrides).__name__}")


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


def _unique_slug(slug: str, used: set[str]) -> str:
    """A slug not yet taken. Two items sharing a tag is a real (if sloppy) thing in P&ID files, and
    the catalog is keyed by slug, so the second must not overwrite the first."""
    candidate = slug or "equipment"
    suffix = 1
    while candidate in used:
        suffix += 1
        candidate = f"{slug}-{suffix}"
    used.add(candidate)
    return candidate
