from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .write_utils import add_local_system

if TYPE_CHECKING:
    from ada import Beam, Part


def add_beams(root: ET.Element, part: Part, sat_map: dict):
    from ada import Beam

    for beam in part.get_all_physical_objects(by_type=Beam):
        add_straight_beam(beam, root)


def add_straight_beam(beam: Beam, xml_root: ET.Element):
    structure_elem = ET.Element("structure")
    beam_elem = ET.Element("straight_beam", {"name": beam.name})
    structure_elem.append(beam_elem)
    beam_elem.append(add_local_system(beam.xvec, beam.yvec, beam.up))
    beam_elem.append(add_segments(beam))
    xml_root.append(structure_elem)


def add_segments(beam: Beam):
    segments = ET.Element("segments")
    props = dict(index="1", section_ref=beam.section.name, material_ref=beam.material.name)
    straight_segment = ET.SubElement(segments, "straight_segment", props)

    d = ["x", "y", "z"]

    geom = ET.SubElement(straight_segment, "geometry")
    wire = ET.SubElement(geom, "wire")
    guide = ET.SubElement(wire, "guide")
    for i, pos in enumerate([beam.n1, beam.n2], start=1):
        props = {d[i]: str(k) for i, k in enumerate(pos.p)}
        props.update(dict(end=str(i)))
        ET.SubElement(guide, "position", props)

    ET.SubElement(wire, "sat_reference")

    # TODO: add SAT embedded geometry and include the reference to the EDGE geometry here
    # ET.SubElement(sat_ref, "edge_ref", dict(edge_ref=""))

    return segments
