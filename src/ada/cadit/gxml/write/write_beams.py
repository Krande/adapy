from __future__ import annotations

import itertools
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.api.beams.justification import Justification
from ada.api.spatial.eq_types import EquipRepr
from ada.api.spatial.equipment import Equipment
from ada.cadit.sat.write.writer import SatWriter
from ada.config import get_logger
from ada.sections.categories import BaseTypes

from .write_utils import add_local_system

if TYPE_CHECKING:
    from ada import Beam, Part


def add_beams(root: ET.Element, part: Part, sw: SatWriter = None):
    from ada import Beam, BeamTapered
    from ada.api.beams import BeamRevolve, BeamSweep

    iter_beams = part.get_all_physical_objects(by_type=Beam)
    iter_taper = part.get_all_physical_objects(by_type=BeamTapered)
    # Curved-axis beams: Genie XML's curved_beam element reads back as chord
    # segments anyway (see read_beams.seg_to_beam), so emit the straight chord
    # rather than silently dropping the member — mirrors stream_xml.
    iter_revolve = part.get_all_physical_objects(by_type=BeamRevolve)
    iter_sweep = part.get_all_physical_objects(by_type=BeamSweep)

    for beam in itertools.chain(iter_beams, iter_taper, iter_revolve, iter_sweep):
        parent = beam.parent
        if isinstance(parent, Equipment) and parent.eq_repr != EquipRepr.AS_IS:
            continue
        if isinstance(beam, (BeamRevolve, BeamSweep)):
            logger.warning(
                f"gxml-write: {type(beam).__name__} {beam.name!r} written as a straight chord beam "
                "(curved axis not supported by the Genie XML writer)"
            )
        add_straight_beam(beam, root, sw)


def add_straight_beam(beam: Beam, xml_root: ET.Element, sw: SatWriter = None):
    import numpy as np

    from ada import Placement

    structure_elem = ET.SubElement(xml_root, "structure")
    straight_beam = ET.SubElement(structure_elem, "straight_beam", {"name": beam.name})

    xvec = beam.xvec
    yvec = beam.yvec
    up = beam.up

    # placement rotation for curve_orientation vectors (same as you already do)
    if beam.placement.is_identity() is False:
        ident_place = Placement()
        place_abs = beam.placement.get_absolute_placement(include_rotations=True)
        if not np.allclose(place_abs.rot_matrix, ident_place.rot_matrix):
            ori_vectors = place_abs.transform_array_from_other_place(
                np.asarray([xvec, yvec, up]), ident_place, ignore_translation=True
            )
            xvec = ori_vectors[0]
            yvec = ori_vectors[1]
            up = ori_vectors[2]

    straight_beam.append(add_local_system(xvec, yvec, up))
    straight_beam.append(add_segments(beam, sw))

    if beam.hinge1 is not None:
        ET.SubElement(straight_beam, "end1", {"hinge_ref": beam.hinge1.name})
    if beam.hinge2 is not None:
        ET.SubElement(straight_beam, "end2", {"hinge_ref": beam.hinge2.name})

    # ---------------------------------------------------------------------
    # Decide whether to write aligned_curve_offset (preferred) or constants
    # ---------------------------------------------------------------------
    force_constant_offsets = False  # set True for debugging if needed

    curve_offset = ET.SubElement(straight_beam, "curve_offset")
    data = beam.offset_helper.curve_offset_local()
    (ox1, oy1, oz1) = data.end1
    (ox2, oy2, oz2) = data.end2

    # 1) Varying offset: always explicit numeric
    if data.is_varying:
        lvo = ET.SubElement(curve_offset, "linear_varying_curve_offset", {"use_local_system": "true"})
        ET.SubElement(lvo, "offset_end1", {"x": f"{ox1:.12g}", "y": f"{oy1:.12g}", "z": f"{oz1:.12g}"})
        ET.SubElement(lvo, "offset_end2", {"x": f"{ox2:.12g}", "y": f"{oy2:.12g}", "z": f"{oz2:.12g}"})
        return

    # 2) Constant case: if justification requests FLUSH semantics, write aligned_curve_offset
    #    IMPORTANT: do this BEFORE the "offset is zero" early-return.
    if (not force_constant_offsets) and beam.justification in (
        Justification.FLUSH_TOP,
        Justification.FLUSH_BOTTOM,
    ):

        if beam.justification == Justification.FLUSH_TOP:
            alignment = "flush_top"
        elif beam.justification == Justification.FLUSH_BOTTOM:
            alignment = "flush_bottom"
        else:
            # Legacy mapping (keep while you transition/verify)
            # NOTE: Genie has no TPROFILE; your exporter writes unsymm I for T -> keep this legacy special-case.
            alignment = "flush_bottom" if beam.section.type == BaseTypes.TPROFILE else "flush_top"

        ET.SubElement(curve_offset, "aligned_curve_offset", {"alignment": alignment, "constant_value": "0"})
        return

    # 3) Constant numeric offset: only write if non-zero
    if ox1 == oy1 == oz1 == 0:
        return

    cco = ET.SubElement(curve_offset, "constant_curve_offset", {"use_local_system": "true"})
    ET.SubElement(
        cco,
        "constant_offset",
        {"x": f"{float(ox1):.12g}", "y": f"{float(oy1):.12g}", "z": f"{float(oz1):.12g}"},
    )


def add_curve_orientation(beam: Beam, straight_beam: ET.Element):
    curve_orientation = ET.SubElement(straight_beam, "curve_orientation")
    cco = ET.SubElement(curve_orientation, "customizable_curve_orientation", {"use_default_rule": "true"})
    orientation = ET.SubElement(cco, "orientation")
    local_system = ET.SubElement(orientation, "local_system")
    ET.SubElement(local_system, "x_vector", {"x": str(beam.xvec[0]), "y": str(beam.xvec[1]), "z": str(beam.xvec[2])})
    ET.SubElement(local_system, "y_vector", {"x": str(beam.yvec[0]), "y": str(beam.yvec[1]), "z": str(beam.yvec[2])})
    ET.SubElement(local_system, "up_vector", {"x": str(beam.up[0]), "y": str(beam.up[1]), "z": str(beam.up[2])})


def add_segments(beam: Beam, sw: SatWriter = None):
    from ada import BeamTapered

    segments = ET.Element("segments")
    props = dict(index="1", section_ref=beam.section.name, material_ref=beam.material.name)
    if isinstance(beam, BeamTapered):
        props.update(dict(section_ref=f"{beam.section.name}_{beam.taper.name}"))

    straight_segment = ET.SubElement(segments, "straight_segment", props)

    d = ["x", "y", "z"]
    p1, p2 = beam.axis_global()

    geom = ET.SubElement(straight_segment, "geometry")
    wire = ET.SubElement(geom, "wire")
    guide = ET.SubElement(wire, "guide")
    for i, pos in enumerate([p1, p2], start=1):
        props = {d[i]: str(k) for i, k in enumerate(pos)}
        props.update(dict(end=str(i)))
        ET.SubElement(guide, "position", props)

    sat_ref = ET.SubElement(wire, "sat_reference")
    # Name the edges the beam's axis became in the embedded body. Left empty,
    # Genie rebuilds the beam's ACIS wire itself on import, which on a large
    # frame dominates load time. The axis is split wherever a plate crosses it,
    # so a beam can reference several edges.
    if sw is not None:
        for edge_ref in sw.edge_map.get(beam.guid, []):
            ET.SubElement(sat_ref, "edge", {"edge_ref": edge_ref})

    return segments


logger = get_logger()
