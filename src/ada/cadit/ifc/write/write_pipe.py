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


def write_ifc_pipe(pipe: Pipe):
    ifc_pipe = write_pipe_ifc_elem(pipe)

    a = pipe.get_assembly()
    ifc_store = a.ifc_store
    f = ifc_store.f

    segments = []
    for param_seg in pipe.segments:
        res = write_pipe_segment(param_seg)
        if res is None:
            logger.error(f'Branch "{param_seg.name}" was not converted to ifc element')
        f.add(res)
        segments += [res]

    ifc_store.writer.add_related_elements_to_spatial_container(segments, ifc_pipe.GlobalId)

    return ifc_pipe


def write_pipe_segment(segment: PipeSegElbow | PipeSegStraight) -> ifcopenshell.entity_instance:
    from ada import PipeSegElbow, PipeSegStraight

    if isinstance(segment, PipeSegElbow):
        pipe_seg = write_pipe_elbow_seg(segment)
    elif isinstance(segment, PipeSegStraight):
        pipe_seg = write_pipe_straight_seg(segment)
    else:
        raise ValueError(f'Unrecognized Pipe Segment type "{type(segment)}"')

    assembly = segment.get_assembly()
    ifc_store = assembly.ifc_store

    found_existing_relationship = False

    beam_type = ifc_store.get_beam_type(segment.section)
    if beam_type is None:
        raise ValueError()

    for ifcrel in ifc_store.f.by_type("IfcRelDefinesByType"):
        if ifcrel.RelatingType == beam_type:
            ifcrel.RelatedObjects = tuple([*ifcrel.RelatedObjects, pipe_seg])
            found_existing_relationship = True
            break

    if found_existing_relationship is False:
        ifc_store.f.create_entity(
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


def write_pipe_ifc_elem(pipe: Pipe):
    if pipe.parent is None:
        raise ValueError("Cannot build ifc element without parent")

    a = pipe.get_assembly()
    f = a.ifc_store.f

    owner_history = a.ifc_store.owner_history
    parent = f.by_guid(pipe.parent.guid)

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
        "Site Container",
        None,
        parent,
        [ifc_elem],
    )

    return ifc_elem
