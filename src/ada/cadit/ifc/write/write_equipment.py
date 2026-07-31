"""IFC writers for Equipment (proper distribution elements + ports) and Systems.

An :class:`ada.Equipment` becomes the IFC element named by its
``ifc_element_class`` (e.g. ``IfcPump``/``IfcTank``), aggregated under its
parent like any decomposed element, with each :class:`ada.Port` nested on it as
an ``IfcDistributionPort`` (``IfcRelNests``).

A :class:`ada.System` piggybacks on the route pipe's ``IfcDistributionSystem``
(written by ``write_pipe``): the group is renamed to the system, given the
category's ``PredefinedType``, extended with the connected equipment elements,
and the routed run's endpoint ports are joined by ``IfcRelConnectsPorts``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell

from ada.cadit.ifc.utils import create_local_placement, write_elem_property_sets
from ada.config import logger
from ada.core.guid import create_guid

if TYPE_CHECKING:
    from ada.api.spatial.equipment import Equipment
    from ada.api.systems.base import System
    from ada.api.systems.ports import Port
    from ada.cadit.ifc.store import IfcStore

_FLOW_DIRECTION_MAP = {"IN": "SINK", "OUT": "SOURCE", "INOUT": "SOURCEANDSINK"}


def _get_by_guid_or_none(ifc_store: IfcStore, guid: str) -> ifcopenshell.entity_instance | None:
    try:
        return ifc_store.get_by_guid(guid)
    except RuntimeError:
        return None


def _system_predefined_type(system: System) -> str:
    from ada.api.systems.base import (
        CableSystem,
        DuctSystem,
        ElectricalSystem,
        PipingSystem,
    )

    if isinstance(system, ElectricalSystem):
        return "ELECTRICAL"
    if isinstance(system, CableSystem):
        return "SIGNAL"
    if isinstance(system, DuctSystem):
        return "VENTILATION"
    if isinstance(system, PipingSystem):
        return "WATERSUPPLY"
    return "NOTDEFINED"


def write_ifc_equipment(ifc_store: IfcStore, equipment: Equipment) -> ifcopenshell.entity_instance:
    """Write ``equipment`` as its ``ifc_element_class`` element (aggregated under
    the parent) and nest its ports as IfcDistributionPorts."""
    if equipment.parent is None:
        raise ValueError("Cannot build ifc element without parent")

    f = ifc_store.f
    owner_history = ifc_store.owner_history
    parent = ifc_store.get_by_guid(equipment.parent.guid)

    placement = create_local_placement(
        f,
        origin=equipment.placement.origin,
        loc_x=equipment.placement.xdir,
        loc_z=equipment.placement.zdir,
        relative_to=parent.ObjectPlacement,
    )

    ifc_elem = f.create_entity(
        equipment.ifc_element_class,
        GlobalId=equipment.guid,
        OwnerHistory=owner_history,
        Name=equipment.name,
        Description=equipment.metadata.get("Description", None),
        ObjectType=None,
        ObjectPlacement=placement,
        Representation=None,
    )

    existing_rel_agg = ifc_store.get_rel_aggregates(parent)
    if existing_rel_agg is not None:
        existing_rel_agg.RelatedObjects = tuple([*existing_rel_agg.RelatedObjects, ifc_elem])
    else:
        new_rel_agg = f.create_entity(
            "IfcRelAggregates",
            GlobalId=create_guid(),
            OwnerHistory=owner_history,
            Name="Site Container",
            Description=None,
            RelatingObject=parent,
            RelatedObjects=[ifc_elem],
        )
        ifc_store.register_rel_aggregates(parent, new_rel_agg)

    if equipment.ports:
        port_elems = [_write_distribution_port(ifc_store, port, ifc_elem) for port in equipment.ports]
        f.create_entity(
            "IfcRelNests",
            GlobalId=create_guid(),
            OwnerHistory=owner_history,
            Name=f"{equipment.name}_ports",
            Description=None,
            RelatingObject=ifc_elem,
            RelatedObjects=port_elems,
        )

    write_elem_property_sets(equipment.metadata, ifc_elem, f, owner_history)

    return ifc_elem


def _write_distribution_port(
    ifc_store: IfcStore, port: Port, ifc_parent: ifcopenshell.entity_instance
) -> ifcopenshell.entity_instance:
    f = ifc_store.f
    placement = create_local_placement(
        f,
        origin=[float(v) for v in port.position],
        relative_to=ifc_parent.ObjectPlacement,
    )
    return f.create_entity(
        "IfcDistributionPort",
        GlobalId=port.guid,
        OwnerHistory=ifc_store.owner_history,
        Name=port.name,
        Description=None,
        ObjectType=port.category,
        ObjectPlacement=placement,
        Representation=None,
        FlowDirection=_FLOW_DIRECTION_MAP[port.direction.value],
    )


def _write_site_port(ifc_store: IfcStore, port: Port) -> ifcopenshell.entity_instance:
    """A site-boundary terminal has no equipment parent, so it is written as a
    free-standing ``IfcDistributionPort`` at its world position — the model's
    external interface for that system run."""
    f = ifc_store.f
    placement = create_local_placement(f, origin=[float(v) for v in port.position])
    return f.create_entity(
        "IfcDistributionPort",
        GlobalId=port.guid,
        OwnerHistory=ifc_store.owner_history,
        Name=port.name,
        Description="site",
        ObjectType=port.category,
        ObjectPlacement=placement,
        Representation=None,
        FlowDirection=_FLOW_DIRECTION_MAP[port.direction.value],
    )


def _resolve_port_entity(ifc_store: IfcStore, port: Port) -> ifcopenshell.entity_instance | None:
    """The IFC port for ``port``: the one nested on its equipment if written,
    or a fresh site terminal for a boundary (site input/output) port."""
    ent = _get_by_guid_or_none(ifc_store, port.guid)
    if ent is not None:
        return ent
    if getattr(port, "is_site", False):
        return _write_site_port(ifc_store, port)
    return None


def _write_beam_run_distribution_system(ifc_store: IfcStore, system: System, beams: list) -> ifcopenshell.entity_instance:
    """Write a routed duct/cable-tray run (straight :class:`ada.Beam` segments)
    as a proper IFC distribution system: each beam becomes its
    ``segment_ifc_class`` flow element (IfcDuctSegment / IfcCableSegment),
    contained in the spatial structure and grouped by an IfcDistributionSystem
    (which services that spatial element). Mirrors ``write_ifc_pipe`` for the
    beam-based services. The system carries the first segment's GUID so the
    resolve-by-route-geometry lookup keeps working."""
    from ada.cadit.ifc.write.write_beams import IfcBeamWriter
    from ada.cadit.ifc.write.write_pipe import _resolve_spatial_parent

    f = ifc_store.f
    owner_history = ifc_store.owner_history
    spatial = _resolve_spatial_parent(ifc_store, beams[0].parent)

    writer = IfcBeamWriter(ifc_store)
    segments = []
    for beam in beams:
        seg_class = (beam.metadata or {}).get("segment_ifc_class", "IfcDuctSegment")
        segments.append(writer.create_ifc_beam(beam, entity_class=seg_class))

    ifc_store.writer.add_related_elements_to_spatial_container(segments, spatial.GlobalId)

    ifc_system = f.create_entity(
        "IfcDistributionSystem", beams[0].guid, owner_history, system.name, None, None, None, "NOTDEFINED"
    )
    f.create_entity(
        "IfcRelAssignsToGroup",
        create_guid(),
        owner_history,
        system.name,
        None,
        RelatedObjects=segments,
        RelatingGroup=ifc_system,
    )
    f.create_entity(
        "IfcRelServicesBuildings",
        create_guid(),
        owner_history,
        system.name,
        None,
        RelatingSystem=ifc_system,
        RelatedBuildings=[spatial],
    )
    return ifc_system


def _resolve_distribution_system(ifc_store: IfcStore, system: System) -> ifcopenshell.entity_instance | None:
    """The IfcDistributionSystem for ``system``: the one a route pipe already
    wrote, or a fresh one built from the route's beam segments (ducts/cable
    trays). ``None`` if the system has no written route geometry."""
    from ada import Beam

    for geom in system.route_geometry:
        ent = _get_by_guid_or_none(ifc_store, geom.guid)
        if ent is not None and ent.is_a("IfcDistributionSystem"):
            return ent
    beams = [g for g in system.route_geometry if isinstance(g, Beam)]
    if beams:
        return _write_beam_run_distribution_system(ifc_store, system, beams)
    return None


def write_ifc_systems(ifc_store: IfcStore, systems: list[System]) -> int:
    """Fold each System onto its route's IfcDistributionSystem: system name +
    PredefinedType, equipment membership, and IfcRelConnectsPorts between the
    routed run's endpoint ports. Piping runs reuse the IfcDistributionSystem the
    pipe writer produced; duct/cable-tray runs (beam segments) get one written
    here."""
    f = ifc_store.f
    num = 0
    for system in systems:
        ifc_system = _resolve_distribution_system(ifc_store, system)
        if ifc_system is None or not ifc_system.is_a("IfcDistributionSystem"):
            logger.warning(f"System {system.name!r} has no written route geometry; skipping IFC system grouping")
            continue

        ifc_system.Name = system.name
        ifc_system.PredefinedType = _system_predefined_type(system)

        eq_elems = []
        for eq in system.connected_equipment:
            elem = _get_by_guid_or_none(ifc_store, eq.guid)
            if elem is not None:
                eq_elems.append(elem)
        if eq_elems:
            for rel in f.by_type("IfcRelAssignsToGroup"):
                if rel.RelatingGroup == ifc_system:
                    existing = set(rel.RelatedObjects)
                    rel.RelatedObjects = [*rel.RelatedObjects, *[e for e in eq_elems if e not in existing]]
                    break

        if len(system.ports) >= 2:
            p_start, p_end = system.ports[0], system.ports[-1]
            port_start = _resolve_port_entity(ifc_store, p_start)
            port_end = _resolve_port_entity(ifc_store, p_end)
            if port_start is not None and port_end is not None:
                f.create_entity(
                    "IfcRelConnectsPorts",
                    GlobalId=create_guid(),
                    OwnerHistory=ifc_store.owner_history,
                    Name=f"{system.name}_run",
                    Description=None,
                    RelatingPort=port_start,
                    RelatedPort=port_end,
                    RealizingElement=None,
                )
        num += 1
    return num
