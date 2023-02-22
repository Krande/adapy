import logging
import xml.etree.ElementTree as ET
from itertools import chain
from typing import List, Tuple

import numpy as np

from ada import Beam, Node, Part
from ada.concepts.containers import Beams
from ada.core.exceptions import VectorNormalizeError


def get_beams(xml_root: ET.Element, parent: Part) -> Beams:
    all_beams = xml_root.findall(".//straight_beam") + xml_root.findall(".//curved_beam")
    return Beams(chain.from_iterable([el_to_beam(bm_el, parent) for bm_el in all_beams]), parent)


def el_to_beam(bm_el: ET.Element, parent: Part) -> List[Beam]:
    name = bm_el.attrib["name"]
    zv = get_orientation(bm_el)
    segs = []
    prev_bm = None
    for seg in bm_el.findall(".//straight_segment"):
        cur_bm = seg_to_beam(name, seg, parent, prev_bm, zv)
        if cur_bm is None:
            continue
        prev_bm = cur_bm
        segs += [cur_bm]

    # Check for offsets
    e1, e2 = get_offsets(bm_el)
    if e1 is not None:
        segs[0].n1.p += e1
    if e2 is not None:
        segs[-1].n2.p += e2

    return segs


def get_offsets(bm_el: ET.Element) -> tuple[np.ndarray, np.ndarray]:
    end1 = bm_el.find(".//offset_end1")
    end2 = bm_el.find(".//offset_end2")
    end1_o = None
    end2_o = None
    if end1 is not None:
        end1_o = np.array(xyz_to_floats(end1))
    if end2 is not None:
        end2_o = np.array(xyz_to_floats(end2))

    return end1_o, end2_o


def seg_to_beam(name: str, seg: ET.Element, parent: Part, prev_bm: Beam, zv):
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

    # Check for offsets

    try:
        bm = Beam(name, n1, n2, sec=sec, mat=mat, parent=parent, metadata=metadata, up=zv)
    except VectorNormalizeError:
        logging.warning(f"Beam '{name}' has coincident nodes. Will skip for now")
        return None
    return bm


def xyz_to_floats(p: ET.Element) -> Tuple[float]:
    return tuple(pos_to_floats([p.attrib["x"], p.attrib["y"], p.attrib["z"]]))


def pos_to_floats(pos):
    return [float(x) for x in pos]


def get_orientation(root: ET.Element) -> tuple:
    zv = None
    lsys = root.find("./curve_orientation/customizable_curve_orientation/orientation/local_system")
    if lsys is not None:
        vec = lsys.find("./zvector")
        zv = float(vec.attrib["x"]), float(vec.attrib["y"]), float(vec.attrib["z"])
    else:
        lsys = root.find("./local_system")
        for vec in lsys.findall("./vector"):
            direction = vec.attrib.get("dir")
            if direction == "z":
                zv = float(vec.attrib["x"]), float(vec.attrib["y"]), float(vec.attrib["z"])

    if zv is None:
        raise ValueError("Z or Y vector must be set")

    return zv
