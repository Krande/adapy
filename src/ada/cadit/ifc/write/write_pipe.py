from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell

from ada.cadit.ifc.utils import create_local_placement
from ada.cadit.ifc.write.pipes.elbow_segment import write_pipe_elbow_seg
from ada.cadit.ifc.write.pipes.straight_segment import write_pipe_straight_seg
from ada.config import logger
from ada.core.constants import X, Z
from ada.core.guid import create_guid

if TYPE_CHECKING:
    from ada import Pipe, PipeSegElbow, PipeSegStraight
    from ada.cadit.ifc.store import IfcStore


def update_ifc_pipe(ifc_store: IfcStore, pipe: Pipe):
    logger.warning("Updating IFC pipe not implemented yet")


def write_ifc_pipe(ifc_store: IfcStore, pipe: Pipe):

    ifc_pipe = write_pipe_ifc_elem(ifc_store, pipe)

    segments = []

    for param_seg in pipe.segments:
        res = write_pipe_segment(ifc_store, param_seg)

        if res is None:
            logger.error(f'Branch "{param_seg.name}" was not converted to ifc element')
            continue

        segments.append(res)

    if segments:
        ifc_store.writer.add_related_elements_to_spatial_container(segments, ifc_pipe.GlobalId)

    return ifc_pipe


def write_pipe_segment(ifc_store: IfcStore, segment: PipeSegElbow | PipeSegStraight) -> ifcopenshell.entity_instance:
    from ada import PipeSegElbow, PipeSegStraight

    if isinstance(segment, PipeSegElbow):
        pipe_seg = write_pipe_elbow_seg(ifc_store, segment)
    elif isinstance(segment, PipeSegStraight):
        pipe_seg = write_pipe_straight_seg(ifc_store, segment)
    else:
        raise ValueError(f'Unrecognized Pipe Segment type "{type(segment)}"')

    f = ifc_store.f

    found_existing_relationship = False

    beam_type = ifc_store.get_beam_type(segment.section)
    if beam_type is None:
        raise ValueError(f"No beam type found for section {segment.section}")

    for ifcrel in f.by_type("IfcRelDefinesByType"):
        if ifcrel.RelatingType == beam_type:
            ifcrel.RelatedObjects = tuple([*ifcrel.RelatedObjects, pipe_seg])
            found_existing_relationship = True
            break

    if not found_existing_relationship:
        f.create_entity(
            "IfcRelDefinesByType",
            GlobalId=create_guid(),
            OwnerHistory=ifc_store.owner_history,
            Name=segment.section.type.value,
            Description=None,
            RelatedObjects=[pipe_seg],
            RelatingType=beam_type,
        )

    ifc_store.writer.associate_elem_with_material(segment.material, pipe_seg)

    return pipe_seg


def write_pipe_ifc_elem(ifc_store: IfcStore, pipe: Pipe):
    if pipe.parent is None:
        raise ValueError("Cannot build ifc element without parent")

    f = ifc_store.f
    owner_history = ifc_store.owner_history

    # parent = f.by_guid(pipe.parent.guid)
    parent = ifc_store.get_by_guid(pipe.parent.guid)

    placement = create_local_placement(
        f,
        origin=pipe.n1.p.astype(float).tolist(),
        loc_x=X,
        loc_z=Z,
        relative_to=parent.ObjectPlacement,
    )

    ifc_elem = f.create_entity(
        "IfcSpatialZone",
        pipe.guid,
        owner_history,
        pipe.name,
        "Description",
        None,
        placement,
        None,
        None,
        None,
    )

    f.createIfcRelAggregates(
        create_guid(),
        owner_history,
        "Pipe Container",
        None,
        parent,
        [ifc_elem],
    )

    return ifc_elem
