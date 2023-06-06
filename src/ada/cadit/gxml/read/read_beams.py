import xml.etree.ElementTree as ET
from itertools import chain
from typing import List, Tuple

import numpy as np

from ada import Beam, Node, Part
from ada.api.containers import Beams
from ada.config import logger
from ada.core.exceptions import VectorNormalizeError


def get_beams(xml_root: ET.Element, parent: Part) -> Beams:
    all_beams = xml_root.findall(".//straight_beam") + xml_root.findall(".//curved_beam")
    return Beams(chain.from_iterable([el_to_beam(bm_el, parent) for bm_el in all_beams]), parent)


def el_to_beam(bm_el: ET.Element, parent: Part) -> List[Beam]:
    name = bm_el.attrib["name"]
    xv, yv, zv = get_curve_orientation(bm_el)
    segs = []
    prev_bm = None
    for seg in bm_el.iterfind(".//straight_segment"):
        cur_bm = seg_to_beam(name, seg, parent, prev_bm, zv)
        if cur_bm is None:
            continue

        prev_bm = cur_bm
        segs += [cur_bm]

    for seg in bm_el.iterfind(".//curved_segment"):
        cur_bm = seg_to_beam(name, seg, parent, prev_bm, zv)
        if cur_bm is None:
            continue

        prev_bm = cur_bm
        segs += [cur_bm]

    if len(segs) > 0:
        apply_offsets_and_alignments(name, bm_el, segs)
    else:
        logger.warning(f"No segments found for beam {name}")

    return segs


def apply_offsets_and_alignments(name: str, bm_el: ET.Element, segs: list[Beam]):
    e1, e2, use_local = get_offsets(bm_el)
    alignment = get_alignment(bm_el, segs)

    if len(segs) > 1 and (e1 is not None or e2 is not None):
        logger.debug(f"Offset at end 1 for beam {name} is ignored as there are more than 1 segments")

    if e1 is not None:
        if use_local:
            e1_global = convert_offset_to_global_csys(e1, segs[0])
        else:
            e1_global = e1
        segs[0].e1 = e1_global
    if e2 is not None:
        if use_local:
            e2_global = convert_offset_to_global_csys(e2, segs[-1])
        else:
            e2_global = e2
        segs[-1].e2 = e2_global

    if alignment is not None:
        for seg in segs:
            seg.e1 = alignment if seg.e1 is None else seg.e1 + alignment
            seg.e2 = alignment if seg.e2 is None else seg.e2 + alignment


def get_offsets(bm_el: ET.Element) -> tuple[np.ndarray, np.ndarray, bool]:
    linear_offset = bm_el.find("curve_offset/linear_varying_curve_offset")

    end1_o = None
    end2_o = None
    use_local = False
    if linear_offset is not None:
        end1 = linear_offset.find("offset_end1")
        end2 = linear_offset.find("offset_end2")
        use_local = False if linear_offset.attrib["use_local_system"] == "false" else True
    else:
        end1 = bm_el.find(".//offset_end1")
        end2 = bm_el.find(".//offset_end2")

    if end1 is not None:
        end1_o = np.array(xyz_to_floats(end1))
    if end2 is not None:
        end2_o = np.array(xyz_to_floats(end2))

    return end1_o, end2_o, use_local


def get_alignment(bm_el: ET.Element, segments: list[Beam]):
    seg0 = segments[0]
    sec0 = seg0.section
    zv = seg0.up
    aligned_offset = bm_el.find("curve_offset/aligned_curve_offset")

    if aligned_offset is None:
        # if sec0.type == sec0.TYPES.ANGULAR:
        #     offset = zv * sec0.h / 2
        #     return offset
        return None

    alignment = aligned_offset.attrib.get("alignment")
    aligned_offset.attrib.get("constant_value")
    if alignment == "flush_top":
        if sec0.type == sec0.TYPES.ANGULAR:
            pass  # Angular profiles are already flush
        elif sec0.type == sec0.TYPES.TUBULAR:
            offset = -zv * sec0.r
            return offset
        else:
            offset = -zv * sec0.h / 2
            return offset


def convert_offset_to_global_csys(o: np.ndarray, bm: Beam):
    xv = bm.xvec
    yv = bm.yvec
    zv = bm.up
    return xv * o[0] + yv * o[1] + zv * o[2]


def apply_offset(o: np.ndarray, n: Node, bm: Beam):
    res = convert_offset_to_global_csys(o, bm)
    return n.p + res


def seg_to_beam(name: str, seg: ET.Element, parent: Part, prev_bm: Beam, zv):
    metadata = dict()

    index = seg.attrib["index"]
    sec = parent.sections.get_by_name(seg.attrib["section_ref"])
    material_ref = seg.attrib.get("material_ref", None)
    if material_ref is None:
        raise ValueError(f"Material not found for beam '{name}'. Please check your xml file")

    mat = parent.materials.get_by_name(material_ref)
    mdf = seg.attrib.get("mass_density_factor_ref", None)
    if mdf is not None:
        metadata["mass_density_factor_ref"] = mdf

    pos = {p.attrib["end"]: xyz_to_floats(p) for p in seg.findall(".//position")}
    if "reinforcement_ref" in seg.attrib.keys():
        metadata["reinforced"] = True

    if index != "1":
        name += f"_E{index}"
        if "cone" in seg.attrib["section_ref"].lower():
            sec = prev_bm.section

    n1 = parent.nodes.add(Node(pos_to_floats(pos["1"])))
    n2 = parent.nodes.add(Node(pos_to_floats(pos["2"])))

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
