from __future__ import annotations

from typing import TYPE_CHECKING

from ada import ArcSegment, Node, PipeSegElbow, PipeSegStraight
from ada.config import logger

from .read_beam_section import import_section_from_ifc
from .read_materials import read_material
from .reader_utils import (
    get_associated_material,
    get_axis_polyline_points_from_product,
    get_ifc_property_sets,
    get_swept_area,
)

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def import_pipe_segment(segment, name, ifc_store: IfcStore) -> PipeSegStraight | PipeSegElbow:
    if segment.is_a("IfcPipeSegment"):
        pipe_segment = read_pipe_straight_segment(segment, name, ifc_store)
    else:
        pipe_segment = read_pipe_elbow(segment, name, ifc_store)

    pipe_segment.section.parent = pipe_segment
    pipe_segment.material.parent = pipe_segment

    return pipe_segment


def read_pipe_straight_segment(segment, name, ifc_store: IfcStore) -> PipeSegStraight:
    p1, p2 = get_axis_polyline_points_from_product(segment)
    mat_ref = get_associated_material(segment)
    swept_area = get_swept_area(segment)
    section = import_section_from_ifc(swept_area)
    mat = read_material(mat_ref, ifc_store)

    pipe_segment = PipeSegStraight(name, Node(p1), Node(p2), section, mat, guid=segment.GlobalId)

    return pipe_segment


def read_pipe_elbow(segment, name, ifc_store: IfcStore) -> PipeSegElbow:
    p1, p2, p3 = [Node(x) for x in get_axis_polyline_points_from_product(segment)]
    pset = get_ifc_property_sets(segment)
    bend_radius = pset.get("Properties", dict()).get("bend_radius", None)
    arc_p1 = pset.get("Properties", dict()).get("p1", None)
    arc_p2 = pset.get("Properties", dict()).get("p2", None)
    arc_midpoint = pset.get("Properties", dict()).get("midpoint", None)

    if bend_radius is None or arc_midpoint is None:
        logger.error("The current Elbow interpretation requires a specific property with bend radius to be imported")
        return None

    bend_radius = float(bend_radius)
    mat_ref = get_associated_material(segment)
    swept_area = get_swept_area(segment)
    section = import_section_from_ifc(swept_area)
    mat = read_material(mat_ref, ifc_store)

    arc = ArcSegment(arc_p1, arc_p2, arc_midpoint, bend_radius)
    elbow = PipeSegElbow(name, p1, p2, p3, bend_radius, section, mat, arc_seg=arc, guid=segment.GlobalId)
    return elbow
