import logging
import xml.etree.ElementTree as ET
from itertools import chain
from typing import List, Tuple

from ada import Beam, Node, Part
from ada.concepts.containers import Beams
from ada.core.exceptions import VectorNormalizeError


def get_beams(xml_root: ET.Element, parent: Part) -> Beams:
    all_beams = xml_root.findall(".//straight_beam") + xml_root.findall(".//curved_beam")
    return Beams(chain.from_iterable([el_to_beam(bm_el, parent) for bm_el in all_beams]), parent)


def el_to_beam(bm_el: ET.Element, parent: Part) -> List[Beam]:
    name = bm_el.attrib["name"]
    segs = []
    prev_bm = None
    for seg in bm_el.findall(".//straight_segment"):
        cur_bm = seg_to_beam(name, seg, parent, prev_bm)
        if cur_bm is None:
            continue
        prev_bm = cur_bm
        segs += [cur_bm]
    return segs


def get_offsets(bm_el: ET.Element) -> Tuple[tuple, tuple]:
    end1 = bm_el.find(".//offset_end1")
    end2 = bm_el.find(".//offset_end2")
    end1_o = None
    end2_o = None
    if end1 is not None:
        end1_o = xyz_to_floats(end1)
    if end2 is not None:
        end2_o = xyz_to_floats(end2)
    return end1_o, end2_o


def seg_to_beam(name: str, seg: ET.Element, parent: Part, prev_bm: Beam):
    index = seg.attrib["index"]
    sec = parent.sections.get_by_name(seg.attrib["section_ref"])
    mat = parent.materials.get_by_name(seg.attrib["material_ref"])
    pos = {p.attrib["end"]: xyz_to_floats(p) for p in seg.findall(".//position")}
    metadata = dict()
    if "reinforcement_ref" in seg.attrib.keys():
        metadata = dict(reinforced=True)

    if index != "1":
        name += f"_E{index}"
        if "cone" in seg.attrib["section_ref"].lower():
            sec = prev_bm.section
    n1 = parent.nodes.add(Node(pos_to_floats(pos["1"])))
    n2 = parent.nodes.add(Node(pos_to_floats(pos["2"])))
    try:
        bm = Beam(name, n1, n2, sec=sec, mat=mat, parent=parent, metadata=metadata)
    except VectorNormalizeError:
        logging.warning(f"Beam '{name}' has coincident nodes. Will skip for now")
        return None
    return bm


def xyz_to_floats(p: ET.Element) -> Tuple[float]:
    return tuple(pos_to_floats([p.attrib["x"], p.attrib["y"], p.attrib["z"]]))


def pos_to_floats(pos):
    return [float(x) for x in pos]
