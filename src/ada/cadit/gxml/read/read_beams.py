import xml.etree.ElementTree as ET
from itertools import chain
from typing import List, Tuple

import numpy as np
from ada.sections.categories import BaseTypes

from ada import Beam, Direction, Node, Part
from ada.api.beams.justification import Justification
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
    """
    Import rule:
      - aligned_curve_offset: semantic only (justification), no numeric e1/e2
      - constant/linear curve_offset: numeric -> materialize into e1/e2 (eccentricity)
    """
    # ---- aligned (flush) ----
    aligned_el = bm_el.find("curve_offset/aligned_curve_offset")
    if aligned_el is not None:
        alignment_str = aligned_el.attrib.get("alignment")  # flush_top/flush_bottom/no_flush

        for seg in segs:
            # Preserve semantic intent
            if alignment_str == "flush_top":
                seg.justification = Justification.FLUSH_TOP
            elif alignment_str == "flush_bottom":
                seg.justification = Justification.FLUSH_BOTTOM
            else:
                seg.justification = Justification.NA

            # Keep original string for debugging / future logic
            if seg.metadata is None:
                seg.metadata = {}
            seg.metadata["aligned_curve_offset_alignment"] = alignment_str

        # IMPORTANT: don't set e1/e2 for aligned offsets
        return

    # ---- explicit numeric curve offsets ----
    o1, o2, use_local = get_offsets(bm_el)
    if o1 is None and o2 is None:
        return

    # If one of them is missing, treat as constant
    if o1 is None and o2 is not None:
        o1 = o2
    if o2 is None and o1 is not None:
        o2 = o1

    # Convert curve_offset -> eccentricity e (inverse of exporter convention)
    e1_global = curve_offset_to_eccentricity_global(o1, segs[0], use_local)
    e2_global = curve_offset_to_eccentricity_global(o2, segs[-1], use_local)

    segs[0].e1 = e1_global
    segs[-1].e2 = e2_global

    # explicit numeric offsets => custom justification semantics
    for seg in segs:
        seg.justification = Justification.CUSTOM

def _curve_offset_add_local(bm: "Beam") -> np.ndarray:
    """
    This MUST match the 'add' logic in OffsetHelper.curve_offset_local().

    Returns add vector in BEAM LOCAL coordinates (x,y,z components).
    """
    sec = bm.section
    p = sec.properties

    # default
    dy = 0.0
    dz = 0.0

    # These are the only special cases you currently have in OffsetHelper.curve_offset_local()
    if sec.type == BaseTypes.ANGULAR:
        # OffsetHelper uses: dz = Cgz - h
        cgz = float(getattr(p, "Cgz", 0.0) or 0.0)
        h = float(sec.h)
        dz = cgz - h

    elif sec.type == BaseTypes.TPROFILE:
        # OffsetHelper uses: dz = Cgz - h/2
        cgz = float(getattr(p, "Cgz", 0.0) or 0.0)
        h = float(sec.h)
        dz = cgz - h / 2.0

    return np.array([0.0, dy, dz], dtype=float)

def curve_offset_to_eccentricity_global(offset: np.ndarray, bm: "Beam", use_local: bool) -> "Direction":
    """
    Convert Genie XML curve_offset (constant_offset / offset_end1) to beam eccentricity e (global vector).

    In exporter / OffsetHelper.curve_offset_local():
        offset_local = -e_local + add_local
    Therefore:
        e_local = add_local - offset_local

    If use_local=True: offset is in beam local coordinates (x,y,z components).
    If use_local=False: offset is already a global vector and must be treated in global coordinates.
    """
    from ada import Direction

    offset = np.asarray(offset, dtype=float)
    add_local = _curve_offset_add_local(bm)

    if use_local:
        # offset is local components
        e_local = add_local - offset
        e_global = convert_offset_to_global_csys(e_local, bm)  # uses bm.xvec/yvec/up
        return Direction(*e_global)

    # offset is already global
    # convert add_local to global, then subtract
    add_global = convert_offset_to_global_csys(add_local, bm)
    e_global = add_global - offset
    return Direction(*e_global)


def get_offsets(bm_el: ET.Element) -> tuple[np.ndarray | None, np.ndarray | None, bool]:
    """
    Reads curve offset definitions from Genie XML.

    Returns:
      (end1_o, end2_o, use_local)

    where end1_o/end2_o are np.ndarray([x,y,z]) or None, and use_local indicates
    whether the offsets are expressed in the beam's local coordinate system.
    """
    linear_offset = bm_el.find("curve_offset/linear_varying_curve_offset")
    constant_offset = bm_el.find("curve_offset/constant_curve_offset")

    end1_o = None
    end2_o = None
    use_local = False
    src = "NONE"

    def _parse_use_local(attr_val: str | None) -> bool:
        if attr_val is None:
            return False
        v = attr_val.strip().lower()
        return v in ("true", "1", "yes")

    if constant_offset is not None:
        src = "constant_curve_offset"
        end1 = constant_offset.find("constant_offset")
        end2 = end1
        use_local = _parse_use_local(constant_offset.attrib.get("use_local_system"))

    elif linear_offset is not None:
        src = "linear_varying_curve_offset"
        end1 = linear_offset.find("offset_end1")
        end2 = linear_offset.find("offset_end2")
        use_local = _parse_use_local(linear_offset.attrib.get("use_local_system"))

    else:
        # legacy / fallback patterns
        src = "fallback .//offset_end1"
        end1 = bm_el.find(".//offset_end1")
        end2 = bm_el.find(".//offset_end2")
        # if fallback nodes exist but some parent carries use_local_system, respect it
        # (best-effort; harmless if not present)
        curve_offset_el = bm_el.find("curve_offset")
        if curve_offset_el is not None:
            use_local = _parse_use_local(curve_offset_el.attrib.get("use_local_system"))

    if end1 is not None:
        end1_o = np.array(xyz_to_floats(end1), dtype=float)
    if end2 is not None:
        end2_o = np.array(xyz_to_floats(end2), dtype=float)

    print(
        f"[get_offsets] beam={bm_el.attrib.get('name')} src={src} use_local={use_local} "
        f"end1={end1_o} end2={end2_o}"
    )
    return end1_o, end2_o, use_local

def convert_offset_to_global_csys(o: np.ndarray, bm: Beam):
    xv = bm.xvec
    yv = bm.yvec
    zv = bm.up
    return xv * o[0] + yv * o[1] + zv * o[2]


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
