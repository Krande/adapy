import xml.etree.ElementTree as ET
from itertools import chain
from typing import List, Tuple

import numpy as np

from ada import Beam, Node, Part
from ada.concepts.containers import Beams
from ada.config import get_logger
from ada.core.exceptions import VectorNormalizeError

logger = get_logger()


def get_beams(xml_root: ET.Element, parent: Part) -> Beams:
    all_beams = xml_root.findall(".//straight_beam") + xml_root.findall(".//curved_beam")
    return Beams(chain.from_iterable([el_to_beam(bm_el, parent) for bm_el in all_beams]), parent)


def el_to_beam(bm_el: ET.Element, parent: Part) -> List[Beam]:
    name = bm_el.attrib["name"]
    xv, yv, zv = get_curve_orientation(bm_el)
    segs = []
    prev_bm = None
    for seg in bm_el.findall(".//straight_segment"):
        cur_bm = seg_to_beam(name, seg, parent, prev_bm, xv, yv, zv)
        if cur_bm is None:
            continue
        prev_bm = cur_bm
        segs += [cur_bm]

    # Check for offsets
    e1, e2, use_local = get_offsets(bm_el)

    if len(segs) > 1 and (e1 is not None or e2 is not None):
        logger.debug(f"Offset at end 1 for beam {name} is ignored as there are more than 1 segments")

    if e1 is not None:
        e1_conv = convert_offset_to_global_csys(e1, segs[0])
        if use_local:
            e1_global = e1_conv
        else:
            e1_global = e1
        segs[0].n1 = Node(segs[0].n1.p + e1_global, parent=parent)
    if e2 is not None:
        if use_local:
            e2_global = convert_offset_to_global_csys(e2, segs[-1])
        else:
            e2_global = e2
        segs[-1].n2 = Node(segs[-1].n2.p + e2_global, parent=parent)

    return segs


def get_offsets(bm_el: ET.Element) -> tuple[np.ndarray, np.ndarray, bool]:
    linear_offset = bm_el.find("curve_offset/linear_varying_curve_offset")
    end1_o = None
    end2_o = None
    if linear_offset is None:
        end1 = bm_el.find(".//offset_end1")
        end2 = bm_el.find(".//offset_end2")
        if end1 is not None or end2 is not None:
            raise NotImplementedError("Non-linear offsets are not supported")

        return end1_o, end2_o, None

    end1 = linear_offset.find("offset_end1")
    end2 = linear_offset.find("offset_end2")
    use_local = False if linear_offset.attrib["use_local_system"] == "false" else True

    if end1 is not None:
        end1_o = np.array(xyz_to_floats(end1))
    if end2 is not None:
        end2_o = np.array(xyz_to_floats(end2))

    return end1_o, end2_o, use_local


def convert_offset_to_global_csys(o: np.ndarray, bm: Beam):
    xv = bm.xvec
    yv = bm.yvec
    zv = bm.up
    return xv * o + yv * o + zv * o


def apply_offset(o: np.ndarray, n: Node, bm: Beam):
    res = convert_offset_to_global_csys(o, bm)
    return n.p + res


def seg_to_beam(name: str, seg: ET.Element, parent: Part, prev_bm: Beam, xv, yv, zv):
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
        logger.warning(f"Beam '{name}' has coincident nodes. Will skip for now")
        return None

    return bm


def xyz_to_floats(p: ET.Element) -> Tuple[float]:
    return tuple(pos_to_floats([p.attrib["x"], p.attrib["y"], p.attrib["z"]]))


def pos_to_floats(pos):
    return [float(x) for x in pos]


def get_curve_orientation(root: ET.Element) -> tuple:
    def to_floats(v):
        return float(v.attrib["x"]), float(v.attrib["y"]), float(v.attrib["z"])

    zv = None
    yv = None
    xv = None
    lsys = root.find("./curve_orientation/customizable_curve_orientation/orientation/local_system")
    if lsys is not None:
        xvec = lsys.find("./xvector")
        yvec = lsys.find("./yvector")
        zvec = lsys.find("./zvector")
        xv = to_floats(xvec)
        yv = to_floats(yvec)
        zv = to_floats(zvec)
    else:
        lsys = root.find("./local_system")
        for vec in lsys.findall("./vector"):
            direction = vec.attrib.get("dir")
            if direction == "z":
                zv = to_floats(vec)
            elif direction == "y":
                yv = to_floats(vec)
            elif direction == "x":
                xv = to_floats(vec)

    if zv is None:
        raise ValueError("Z or Y vector must be set")

    return xv, yv, zv
