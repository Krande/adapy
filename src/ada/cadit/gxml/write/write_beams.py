from __future__ import annotations

import itertools
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.api.beams.justification import Justification
from ada.api.spatial.eq_types import EquipRepr
from ada.api.spatial.equipment import Equipment
from ada.cadit.sat.write.writer import SatWriter
from ada.config import get_logger

from .write_utils import add_local_system

if TYPE_CHECKING:
    from ada import Beam, Part


def add_beams(root: ET.Element, part: Part, sw: SatWriter = None):
    from ada import Beam, BeamTapered
    from ada.api.beams import BeamCurved, BeamRevolve, BeamSweep

    iter_beams = part.get_all_physical_objects(by_type=Beam)
    iter_taper = part.get_all_physical_objects(by_type=BeamTapered)
    iter_revolve = part.get_all_physical_objects(by_type=BeamRevolve)
    iter_sweep = part.get_all_physical_objects(by_type=BeamSweep)
    iter_curved = part.get_all_physical_objects(by_type=BeamCurved)

    for beam in itertools.chain(iter_beams, iter_taper, iter_revolve, iter_sweep, iter_curved):
        parent = beam.parent
        if isinstance(parent, Equipment) and parent.eq_repr != EquipRepr.AS_IS:
            continue
        # A curved-axis beam round-trips as a Genie <curved_beam> only when its
        # arc was authored into the embedded SAT (an EDGE it can point at). The
        # arc's curvature lives nowhere else — a <curved_beam> whose guide holds
        # only two endpoints and names no edge is not a curve — so without that
        # edge fall back to the straight chord rather than emit an ambiguous one.
        if isinstance(beam, (BeamRevolve, BeamSweep, BeamCurved)):
            edge_refs = sw.edge_map.get(beam.guid, []) if sw is not None else []
            if edge_refs:
                add_curved_beam(beam, root, edge_refs, sw)
                continue
            logger.warning(
                f"gxml-write: {type(beam).__name__} {beam.name!r} written as a straight chord beam "
                "(no SAT arc edge available; curved axis not preserved)"
            )
        add_straight_beam(beam, root, sw)


def add_curved_beam(beam: Beam, xml_root: ET.Element, edge_refs: list[str], sw: SatWriter = None) -> None:
    """Emit a Genie ``<curved_beam>`` that points at its arc's SAT edge(s).

    Mirrors :func:`add_straight_beam` but writes a ``<curved_segment>`` whose
    ``<wire>`` names the ACIS edge the arc was authored as. The guide still
    carries the two endpoints, as Genie's own curved beams do — the edge is what
    makes it a curve on import.
    """
    structure_elem = ET.SubElement(xml_root, "structure")
    curved_beam = ET.SubElement(structure_elem, "curved_beam", {"name": beam.name})

    curved_beam.append(add_local_system(beam.xvec, beam.yvec, beam.up))

    segments = ET.Element("segments")
    props = dict(index="1", section_ref=beam.section.name, material_ref=beam.material.name)
    curved_segment = ET.SubElement(segments, "curved_segment", props)

    d = ["x", "y", "z"]
    p1, p2 = beam.axis_global()
    geom = ET.SubElement(curved_segment, "geometry")
    wire = ET.SubElement(geom, "wire")
    guide = ET.SubElement(wire, "guide")
    for i, pos in enumerate([p1, p2], start=1):
        pos_props = {d[j]: str(k) for j, k in enumerate(pos)}
        pos_props.update(dict(end=str(i)))
        ET.SubElement(guide, "position", pos_props)

    sat_ref = ET.SubElement(wire, "sat_reference")
    for edge_ref in edge_refs:
        ET.SubElement(sat_ref, "edge", {"edge_ref": edge_ref})

    curved_beam.append(segments)

    add_curve_offset(beam, curved_beam)


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

    add_curve_offset(beam, straight_beam)


def add_curve_offset(beam: Beam, straight_beam: ET.Element) -> None:
    """Write the beam's ``<curve_offset>``.

    Genie rejects an empty ``<curve_offset/>`` ("Unable to build model from
    element"), so the element is only created once it is known which child goes
    in it. A beam with no offset still needs one: ``reparameterized_beam_curve_
    offset`` names the same rule the exported journal sets as the default
    (``GenieRules.BeamCreation.DefaultCurveOffset``, see gxml/utils.py).
    """
    data = beam.offset_helper.curve_offset_local()
    (ox1, oy1, oz1) = data.end1
    (ox2, oy2, oz2) = data.end2

    curve_offset = ET.Element("curve_offset")

    if data.is_varying:
        # Ends differ: only explicit numerics can express that.
        lvo = ET.SubElement(curve_offset, "linear_varying_curve_offset", {"use_local_system": "true"})
        ET.SubElement(lvo, "offset_end1", {"x": f"{ox1:.12g}", "y": f"{oy1:.12g}", "z": f"{oz1:.12g}"})
        ET.SubElement(lvo, "offset_end2", {"x": f"{ox2:.12g}", "y": f"{oy2:.12g}", "z": f"{oz2:.12g}"})
    elif beam.justification in (Justification.FLUSH_TOP, Justification.FLUSH_BOTTOM):
        # Flush is semantic — let Genie re-derive the numbers from the section,
        # as its own exports do. Checked before the zero-offset case below:
        # flush is meaningful even when the resolved offset is zero.
        alignment = "flush_top" if beam.justification == Justification.FLUSH_TOP else "flush_bottom"
        ET.SubElement(curve_offset, "aligned_curve_offset", {"alignment": alignment, "constant_value": "0"})
    elif ox1 == oy1 == oz1 == 0:
        # No offset to state. The default rule, named explicitly.
        ET.SubElement(curve_offset, "reparameterized_beam_curve_offset")
    else:
        cco = ET.SubElement(curve_offset, "constant_curve_offset", {"use_local_system": "true"})
        ET.SubElement(
            cco,
            "constant_offset",
            {"x": f"{float(ox1):.12g}", "y": f"{float(oy1):.12g}", "z": f"{float(oz1):.12g}"},
        )

    straight_beam.append(curve_offset)


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
