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


def write_ifc_systems(ifc_store: IfcStore, systems: list[System]) -> int:
    """Fold each System onto its route pipe's IfcDistributionSystem: system
    name + PredefinedType, equipment membership, and IfcRelConnectsPorts
    between the routed run's endpoint ports."""
    f = ifc_store.f
    num = 0
    for system in systems:
        ifc_system = None
        for pipe in system.route_geometry:
            ifc_system = _get_by_guid_or_none(ifc_store, pipe.guid)
            if ifc_system is not None:
                break
        if ifc_system is None or not ifc_system.is_a("IfcDistributionSystem"):
            logger.warning(f"System {system.name!r} has no written route pipe; skipping IFC system grouping")
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
            port_start = _get_by_guid_or_none(ifc_store, p_start.guid)
            port_end = _get_by_guid_or_none(ifc_store, p_end.guid)
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
