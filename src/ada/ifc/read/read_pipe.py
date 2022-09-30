from __future__ import annotations

import logging

from ada import ArcSegment, Assembly, Node, PipeSegElbow, PipeSegStraight

from ..concepts import IfcRef
from .read_beam_section import import_section_from_ifc
from .read_materials import read_material
from .reader_utils import (
    get_associated_material,
    get_axis_polyline_points_from_product,
    get_ifc_property_sets,
)


def import_pipe_segment(segment, name, ifc_ref: IfcRef, assembly: Assembly) -> PipeSegStraight | PipeSegElbow:
    if segment.is_a("IfcPipeSegment"):
        pipe_segment = read_pipe_straight_segment(segment, name, ifc_ref, assembly)
    else:
        pipe_segment = read_pipe_elbow(segment, name, ifc_ref, assembly)

    pipe_segment.section.parent = pipe_segment
    pipe_segment.material.parent = pipe_segment

    return pipe_segment


def read_pipe_straight_segment(segment, name, ifc_ref: IfcRef, assembly: Assembly) -> PipeSegStraight:
    p1, p2 = get_axis_polyline_points_from_product(segment)
    mat_ref = get_associated_material(segment)
    section = import_section_from_ifc(mat_ref.Profile)
    mat = read_material(mat_ref, ifc_ref, assembly)

    pipe_segment = PipeSegStraight(name, Node(p1), Node(p2), section, mat, guid=segment.GlobalId, ifc_elem=segment)

    return pipe_segment


def read_pipe_elbow(segment, name, ifc_ref: IfcRef, assembly: Assembly) -> PipeSegElbow:
    p1, p2, p3 = [Node(x) for x in get_axis_polyline_points_from_product(segment)]
    pset = get_ifc_property_sets(segment)
    bend_radius = pset.get("Properties", dict()).get("bend_radius", None)
    arc_p1 = pset.get("Properties", dict()).get("p1", None)
    arc_p2 = pset.get("Properties", dict()).get("p2", None)
    arc_midpoint = pset.get("Properties", dict()).get("midpoint", None)

    if bend_radius is None or arc_midpoint is None:
        logging.error("The current Elbow interpretation requires a specific property with bend radius to be imported")
        return None

    bend_radius = float(bend_radius)
    mat_ref = get_associated_material(segment)
    section = import_section_from_ifc(mat_ref.Profile)
    mat = read_material(mat_ref, ifc_ref, assembly)

    arc = ArcSegment(arc_p1, arc_p2, arc_midpoint, bend_radius)
    guid = segment.GlobalId
    elbow = PipeSegElbow(name, p1, p2, p3, bend_radius, section, mat, arc_seg=arc, guid=guid, ifc_elem=segment)
    return elbow
