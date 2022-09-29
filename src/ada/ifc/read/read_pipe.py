from __future__ import annotations

from ada import Assembly, PipeSegElbow, PipeSegStraight

from ..concepts import IfcRef
from .read_beam_section import import_section_from_ifc
from .read_materials import read_material
from .reader_utils import (
    get_associated_material,
    get_axis_polyline_points_from_product,
    get_ifc_body,
)


def import_pipe_segment(segment, name, ifc_ref: IfcRef, assembly: Assembly) -> PipeSegStraight | PipeSegElbow:

    if segment.is_a("IfcPipeSegment"):
        pipe_segment = read_pipe_straight_segment(segment, name, ifc_ref, assembly)
    else:
        pipe_segment = read_pipe_elbow(segment, name, ifc_ref, assembly)

    return pipe_segment


def read_pipe_straight_segment(segment, name, ifc_ref: IfcRef, assembly: Assembly) -> PipeSegStraight:
    p1, p2 = get_axis_polyline_points_from_product(segment)
    mat_ref = get_associated_material(segment)
    section = import_section_from_ifc(mat_ref.Profile)
    mat = read_material(mat_ref, ifc_ref, assembly)
    return PipeSegStraight(name, p1, p2, section, mat)


def read_pipe_elbow(segment, name, ifc_ref: IfcRef, assembly: Assembly) -> PipeSegElbow:
    _ = get_ifc_body(segment)

    return None
    # return PipeSegElbow()
