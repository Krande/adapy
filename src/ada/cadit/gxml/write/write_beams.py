from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ...sat.write.writer import SatWriter
from .write_utils import add_local_system

if TYPE_CHECKING:
    from ada import Beam, Part


def add_beams(root: ET.Element, part: Part, sw: SatWriter = None):
    from ada import Beam

    for beam in part.get_all_physical_objects(by_type=Beam):
        add_straight_beam(beam, root)


def add_straight_beam(beam: Beam, xml_root: ET.Element):
    structure_elem = ET.SubElement(xml_root, "structure")
    straight_beam = ET.SubElement(structure_elem, "straight_beam", {"name": beam.name})
    # add_curve_orientation(beam, straight_beam)
    straight_beam.append(add_local_system(beam.xvec, beam.yvec, beam.up))
    straight_beam.append(add_segments(beam))
    curve_offset = ET.SubElement(straight_beam, "curve_offset")
    ET.SubElement(curve_offset, "reparameterized_beam_curve_offset")


def add_curve_orientation(beam: Beam, straight_beam: ET.Element):
    curve_orientation = ET.SubElement(straight_beam, "curve_orientation")
    cco = ET.SubElement(curve_orientation, "customizable_curve_orientation", {"use_default_rule": "true"})
    orientation = ET.SubElement(cco, "orientation")
    local_system = ET.SubElement(orientation, "local_system")
    ET.SubElement(local_system, "x_vector", {"x": str(beam.xvec[0]), "y": str(beam.xvec[1]), "z": str(beam.xvec[2])})
    ET.SubElement(local_system, "y_vector", {"x": str(beam.yvec[0]), "y": str(beam.yvec[1]), "z": str(beam.yvec[2])})
    ET.SubElement(local_system, "up_vector", {"x": str(beam.up[0]), "y": str(beam.up[1]), "z": str(beam.up[2])})


def add_segments(beam: Beam):
    segments = ET.Element("segments")
    props = dict(index="1", section_ref=beam.section.name, material_ref=beam.material.name)
    straight_segment = ET.SubElement(segments, "straight_segment", props)

    d = ["x", "y", "z"]
    origin = beam.parent.placement.get_absolute_placement().origin

    geom = ET.SubElement(straight_segment, "geometry")
    wire = ET.SubElement(geom, "wire")
    guide = ET.SubElement(wire, "guide")
    for i, pos in enumerate([beam.n1, beam.n2], start=1):
        props = {d[i]: str(k) for i, k in enumerate(origin + pos.p)}
        props.update(dict(end=str(i)))
        ET.SubElement(guide, "position", props)

    ET.SubElement(wire, "sat_reference")

    # TODO: add SAT embedded geometry and include the reference to the EDGE geometry here
    # ET.SubElement(sat_ref, "edge_ref", dict(edge_ref=""))

    return segments
