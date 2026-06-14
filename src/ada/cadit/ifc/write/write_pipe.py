from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell

from ada.cadit.ifc.write.pipes.elbow_segment import write_pipe_elbow_seg
from ada.cadit.ifc.write.pipes.straight_segment import write_pipe_straight_seg
from ada.config import logger
from ada.core.guid import create_guid

if TYPE_CHECKING:
    from ada import Pipe, PipeSegElbow, PipeSegStraight
    from ada.cadit.ifc.store import IfcStore


def update_ifc_pipe(ifc_store: IfcStore, pipe: Pipe):
    logger.warning("Updating IFC pipe not implemented yet")


def _resolve_spatial_parent(ifc_store: IfcStore, part) -> ifcopenshell.entity_instance:
    """Nearest IfcSpatialElement ancestor for ``part``.

    Plant-agnostic: ``IfcRelContainedInSpatialStructure``/``IfcRelServicesBuildings`` accept any
    IfcSpatialElement (IfcSite/IfcSpatialZone/IfcBuilding in IFC4; IfcFacility/IfcFacilityPart in
    IFC4x3), so a pipe on a site or plant area works. Walks up when a Part maps to a non-spatial
    IFC type (e.g. IfcElementAssembly)."""
    p = part
    while p is not None:
        ifc_elem = ifc_store.get_by_guid(p.guid)
        if ifc_elem is not None and ifc_elem.is_a("IfcSpatialElement"):
            return ifc_elem
        p = p.parent
    raise ValueError(f"No IfcSpatialElement ancestor found for pipe parent {part}")


def write_ifc_pipe(ifc_store: IfcStore, pipe: Pipe):
    """Write a Pipe as a proper IFC distribution system.

    The flow elements (IfcPipeSegment / IfcPipeFitting) are contained in the real spatial
    structure and grouped by an IfcDistributionSystem (IfcRelAssignsToGroup), which services that
    spatial element (IfcRelServicesBuildings) — NOT modelled as an IfcSpatialZone."""
    if pipe.parent is None:
        raise ValueError("Cannot build ifc element without parent")

    f = ifc_store.f
    owner_history = ifc_store.owner_history

    spatial = _resolve_spatial_parent(ifc_store, pipe.parent)

    segments = []
    for param_seg in pipe.segments:
        res = write_pipe_segment(ifc_store, param_seg)
        if res is None:
            logger.error(f'Pipe segment "{param_seg.name}" was not converted to ifc element')
            continue
        segments.append(res)

    if not segments:
        return None

    # Contain the flow elements directly in the spatial structure (site/zone/facility/…).
    ifc_store.writer.add_related_elements_to_spatial_container(segments, spatial.GlobalId)

    # Group them as a distribution system — the functional grouping for a pipe run. The system
    # carries the pipe's GUID so get_by_guid(pipe.guid) keeps resolving.
    system = f.create_entity(
        "IfcDistributionSystem",
        pipe.guid,
        owner_history,
        pipe.name,
        None,
        None,
        None,
        "NOTDEFINED",
    )
    f.create_entity(
        "IfcRelAssignsToGroup",
        create_guid(),
        owner_history,
        pipe.name,
        None,
        RelatedObjects=segments,
        RelatingGroup=system,
    )
    f.create_entity(
        "IfcRelServicesBuildings",
        create_guid(),
        owner_history,
        pipe.name,
        None,
        RelatingSystem=system,
        RelatedBuildings=[spatial],
    )

    return system


def write_pipe_segment(ifc_store: IfcStore, segment: PipeSegElbow | PipeSegStraight) -> ifcopenshell.entity_instance:
    from ada import PipeSegElbow, PipeSegStraight

    if isinstance(segment, PipeSegElbow):
        pipe_seg = write_pipe_elbow_seg(ifc_store, segment)
    elif isinstance(segment, PipeSegStraight):
        pipe_seg = write_pipe_straight_seg(ifc_store, segment)
    else:
        raise ValueError(f'Unrecognized Pipe Segment type "{type(segment)}"')

    beam_type = ifc_store.get_beam_type(segment.section)
    if beam_type is None:
        raise ValueError(f"No beam type found for section {segment.section}")

    # Defer aggregate membership (see queue_rel_defines_by_type) to avoid the
    # O(N²) per-segment re-walk of the shared IfcRelDefinesByType.
    ifc_store.queue_rel_defines_by_type(beam_type, pipe_seg, segment.section.type.value)

    ifc_store.writer.associate_elem_with_material(segment.material, pipe_seg)

    return pipe_seg
