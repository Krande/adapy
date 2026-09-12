"""Equipment the import places: the P&ID's own, and the small bodies it synthesises.

The resolved equipment becomes a :class:`~ada.topo_model.layout.LayoutItem` each, named by its
tag. Three kinds of thing the P&ID does not call equipment are materialised as equipment anyway,
because a run has to terminate on something with a port: a **branch point** (a tee two or more
segments end at, always), an **instrument** (a controller, a transmitter, the actuator on a control
valve -- so a signal line has a body at each end), and, on request, each **in-line component**
(a valve, a strainer). Every one of them lands in the same name pool, the same catalog and the
same provenance map as the real equipment; the envelopes and IFC classes they get are the
conventions in :mod:`~ada.cadit.dexpi.read.conventions`.
"""

from __future__ import annotations

import dataclasses

from ada.core.text_utils import slugify
from ada.topo_model.layout import LayoutItem

from ... import attributes, class_table
from ...equipment_defaults import build_default_doc
from ...equipment_list import (
    ResolvedEquipment,
    instrument_items,
    nozzle_specs_for,
    operated_component,
    signal_lines,
    signal_terminal,
)
from ...model import DexpiDocument, DexpiItem
from ...nozzle_placers import NozzleSpec, nozzle_from_node, port_names
from ..connectivity import ConnectionIndex
from ..conventions import (
    INLINE_BBOX,
    INLINE_IFC,
    INSTRUMENT_BBOX,
    INSTRUMENT_IFC,
    INSTRUMENT_IFC_DEFAULT,
    JUNCTION_IFC,
    SIGNAL_PORT_KEY,
)
from ..naming import unique_name
from .resolve import _descendants, _Index, _SegmentSpec, _system_name

__all__ = [
    "_chambers",
    "_equipment_metadata",
    "_equipment_names",
    "_inline_equipment",
    "_instrument_equipment",
    "_instrument_ifc",
    "_junction_equipment",
    "_layout_item",
]


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


def _junction_equipment(
    doc: DexpiDocument,
    junctions: dict[str, list[str]],
    catalog: dict,
    index: _Index,
    provenance: dict[str, dict],
    taken: set[str],
    connections: ConnectionIndex,
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
    flow = connections.flows
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
    connections: ConnectionIndex,
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
    flow = connections.flows
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
    connections: ConnectionIndex,
) -> list[LayoutItem]:
    """Materialise each segment's in-line components as their own small equipment.

    Off by default, because a real P&ID carries dozens of valves per unit and they would otherwise
        flood the equipment listing and the mass rollup. When it is on, each component gets its own
        catalog entry with ports generated from its actual connection nodes -- so it is a real placed
        object with real nozzles, just not one the run passes through.
    """
    flow = connections.flows
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
