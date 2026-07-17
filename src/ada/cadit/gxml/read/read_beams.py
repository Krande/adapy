import xml.etree.ElementTree as ET
from itertools import chain
from typing import List, Tuple

import numpy as np

from ada import Beam, Direction, Node, Part
from ada.api.beams.justification import Justification
from ada.api.containers import Beams
from ada.config import logger
from ada.core.exceptions import VectorNormalizeError
from ada.sections.categories import BaseTypes


def get_beams(xml_root: ET.Element, parent: Part) -> Beams:
    all_beams = xml_root.findall(".//straight_beam") + xml_root.findall(".//curved_beam")
    return Beams(chain.from_iterable([el_to_beam(bm_el, parent) for bm_el in all_beams]), parent)


def el_to_beam(bm_el: ET.Element, parent: Part, edge_curve_resolver=None) -> List[Beam]:
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
        cur_bm = seg_to_beam(name, seg, parent, prev_bm, zv, edge_curve_resolver=edge_curve_resolver)
        if cur_bm is None:
            continue

        prev_bm = cur_bm
        segs += [cur_bm]

    if len(segs) > 0:
        apply_offsets_and_alignments(name, bm_el, segs)
    else:
        logger.warning(f"No segments found for beam {name}")

    return segs


def apply_offsets_and_alignments(name: str, bm_el: ET.Element, segs: list["Beam"]):
    """
    Import rule:
      - aligned_curve_offset: semantic only (justification), no numeric e1/e2
      - constant/linear curve_offset: numeric -> materialize into e1/e2 (eccentricity)
      - curve_end_offset.keep_axial_eccentricity_at_end1/end2:
          controls whether the AXIAL component of the numeric offset is applied at that end.
    """

    def _parse_bool(attr_val: str | None) -> bool:
        if attr_val is None:
            return False
        v = attr_val.strip().lower()
        return v in ("true", "1", "yes")

    def _remove_axial_component(offset: np.ndarray, bm: "Beam", use_local: bool) -> np.ndarray:
        """
        Remove the component along the beam axis (local x).
        - if use_local: just zero the local-x component
        - if global: subtract projection onto bm axis (bm.xvec)
        """
        o = np.asarray(offset, dtype=float).copy()

        if use_local:
            o[0] = 0.0
            return o

        x = np.asarray(bm.xvec, dtype=float)
        n = np.linalg.norm(x)
        if n <= 0.0:
            return o
        xhat = x / n
        return o - np.dot(o, xhat) * xhat

    # ---- aligned (flush) ----
    aligned_el = bm_el.find("curve_offset/aligned_curve_offset")
    if aligned_el is not None:
        alignment_str = aligned_el.attrib.get("alignment")  # flush_top/flush_bottom/no_flush

        for seg in segs:
            # When the section was re-encoded from a Genie inverted T
            # (flange-down → adapy flange-up storage + flipped
            # bm.up), Genie's "flush_top" referred to the section's
            # ORIGINAL top edge — the web tip on an inverted T. In
            # adapy's flange-up storage that edge sits at -h/2, i.e.
            # FLUSH_BOTTOM. Swap the justification so OffsetHelper
            # produces the right eccentricity vector.
            flange_down = bool(seg.section.metadata and seg.section.metadata.get("gxml_flange_down"))
            effective = alignment_str
            if flange_down:
                if alignment_str == "flush_top":
                    effective = "flush_bottom"
                elif alignment_str == "flush_bottom":
                    effective = "flush_top"

            if effective == "flush_top":
                seg.justification = Justification.FLUSH_TOP
            elif effective == "flush_bottom":
                seg.justification = Justification.FLUSH_BOTTOM
            else:
                seg.justification = Justification.NA

            if seg.metadata is None:
                seg.metadata = {}
            # OffsetHelper.curve_offset_local() reads this metadata
            # key and overrides ``seg.justification`` with its
            # interpretation, so the value we store has to be the
            # SWAPPED one — otherwise the swap above gets undone
            # the next time eccentricity is computed.
            seg.metadata["aligned_curve_offset_alignment"] = effective
            if flange_down and effective != alignment_str:
                # Remember what Genie said so a later writer can
                # un-swap on export.
                seg.metadata["gxml_aligned_curve_offset_alignment_original"] = alignment_str

        return

    # ---- explicit numeric curve offsets ----
    o1, o2, use_local, container = get_offsets(bm_el)
    if o1 is None and o2 is None:
        return

    # If one of them is missing, treat as constant
    if o1 is None and o2 is not None:
        o1 = np.asarray(o2, dtype=float)
    if o2 is None and o1 is not None:
        o2 = np.asarray(o1, dtype=float)

    # ---- Axial-component handling ----
    # Two Genie containers wrap numeric curve offsets:
    #   * ``curve_end_offset`` — carries explicit
    #     ``keep_axial_eccentricity_at_end{1,2}`` flags (Bm3/Bm4
    #     fixture). Default flag value is False = strip axial.
    #   * ``reparameterized_beam_curve_offset`` — re-parameterises
    #     the rendered wire; the axial component is NOT supposed to
    #     stretch the beam through e1/e2. Audit #5256 carries 280
    #     stiffener beams (T-sections) with non-zero axial offsets
    #     that previously extended them past their landing wall.
    #     No keep-axial flags here, so always strip.
    #
    # When the offsets land bare under ``<curve_offset>`` (no wrapper),
    # keep the axial component — that's the legacy direct-eccentricity
    # case the original implementation already handled.
    keep1 = True
    keep2 = True
    ceo = bm_el.find("curve_offset/curve_end_offset")
    if ceo is not None:
        keep1 = _parse_bool(ceo.attrib.get("keep_axial_eccentricity_at_end1"))
        keep2 = _parse_bool(ceo.attrib.get("keep_axial_eccentricity_at_end2"))
    elif container == "reparameterized_beam_curve_offset":
        keep1 = False
        keep2 = False

    if o1 is not None and not keep1:
        o1 = _remove_axial_component(o1, segs[0], use_local)
    if o2 is not None and not keep2:
        o2 = _remove_axial_component(o2, segs[-1], use_local)

    # Convert curve_offset -> eccentricity e (inverse of exporter convention)
    e1_global = curve_offset_to_eccentricity_global(o1, segs[0], use_local)
    e2_global = curve_offset_to_eccentricity_global(o2, segs[-1], use_local)

    segs[0].e1 = e1_global
    segs[-1].e2 = e2_global

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
        # Genie-inverted T (flange-down) was re-encoded to adapy's
        # flange-up storage by the section reader; that flips the
        # sign of Cgz_from_mid relative to what Genie's exporter saw
        # when it computed ``offset = Cgz - h/2``. Negating ``cgz``
        # here recovers Genie's original ``add`` term so the eccen-
        # tricity falls out correct — section lands with its web
        # tip on the wall plate instead of half-a-beam-height past
        # it (audit #5256 stiffeners).
        if sec.metadata and sec.metadata.get("gxml_flange_down"):
            cgz = -cgz
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


def get_offsets(
    bm_el: ET.Element,
) -> tuple[np.ndarray | None, np.ndarray | None, bool, str | None]:
    """
    Reads curve offset definitions from Genie XML.

    Returns:
      (end1_o, end2_o, use_local, container)

    end1_o/end2_o are np.ndarray([x,y,z]) or None; use_local indicates
    whether the offsets are expressed in the beam's local coordinate
    system. ``container`` names the immediate child of ``<curve_offset>``
    that wraps the numeric offset nodes (``curve_end_offset``,
    ``reparameterized_beam_curve_offset``) — or None when the numeric
    offsets sit bare under ``<curve_offset>``. Callers use ``container``
    to decide whether to default-strip the axial component before
    converting to eccentricity.
    """

    def _parse_bool(attr_val: str | None) -> bool:
        if attr_val is None:
            return False
        v = attr_val.strip().lower()
        return v in ("true", "1", "yes")

    def _get_xyz(el: ET.Element | None) -> np.ndarray | None:
        if el is None:
            return None
        # expects attributes x,y,z (as Genie exports)
        return np.array(xyz_to_floats(el), dtype=float)

    curve_offset_el = bm_el.find("curve_offset")
    if curve_offset_el is None:
        return None, None, False, None

    # Genie can wrap numeric offsets in:
    # curve_offset/curve_end_offset/curve_offset/<constant_curve_offset|linear_varying_curve_offset>
    # curve_offset/reparameterized_beam_curve_offset/curve_offset/<…>
    offsets_root = curve_offset_el
    container: str | None = None
    for tag in ("curve_end_offset", "reparameterized_beam_curve_offset"):
        wrap = curve_offset_el.find(tag)
        if wrap is not None:
            container = tag
            inner = wrap.find("curve_offset")
            offsets_root = inner if inner is not None else wrap
            break

    # Direct numeric nodes under offsets_root
    constant_offset = offsets_root.find("constant_curve_offset")
    linear_offset = offsets_root.find("linear_varying_curve_offset")

    # Fallback search anywhere below offsets_root (handles older/alternate layouts)
    if constant_offset is None:
        constant_offset = offsets_root.find(".//constant_curve_offset")
    if linear_offset is None:
        linear_offset = offsets_root.find(".//linear_varying_curve_offset")

    end1_o = None
    end2_o = None
    use_local = False

    if constant_offset is not None:
        use_local = _parse_bool(constant_offset.attrib.get("use_local_system"))
        v = _get_xyz(constant_offset.find("constant_offset"))
        end1_o = v
        end2_o = v

    elif linear_offset is not None:
        use_local = _parse_bool(linear_offset.attrib.get("use_local_system"))
        end1_o = _get_xyz(linear_offset.find("offset_end1"))
        end2_o = _get_xyz(linear_offset.find("offset_end2"))

    else:
        # last-resort legacy patterns
        end1 = offsets_root.find(".//offset_end1")
        end2 = offsets_root.find(".//offset_end2")
        end1_o = _get_xyz(end1)
        end2_o = _get_xyz(end2)

        # try to respect any use_local_system we can find
        if offsets_root is not None:
            use_local = _parse_bool(offsets_root.attrib.get("use_local_system"))
        if not use_local and curve_offset_el is not None:
            use_local = _parse_bool(curve_offset_el.attrib.get("use_local_system"))

    return end1_o, end2_o, use_local, container


def convert_offset_to_global_csys(o: np.ndarray, bm: Beam):
    xv = bm.xvec
    yv = bm.yvec
    zv = bm.up
    return xv * o[0] + yv * o[1] + zv * o[2]


def seg_to_beam(name: str, seg: ET.Element, parent: Part, prev_bm: Beam, zv, edge_curve_resolver=None):
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

    # Genie expresses inverted T-sections (flange-down) as
    # ``unsymmetrical_i_section`` with a degenerate top flange. The
    # section reader re-encodes those into adapy's flange-up TPROFILE
    # convention and tags them; here we flip the beam up-vector so
    # the rendered flange ends up at the bottom again, matching the
    # original Genie geometry.
    up = zv
    if sec is not None and sec.metadata and sec.metadata.get("gxml_flange_down"):
        up = tuple(-v for v in zv)

    # A curved segment references the ACIS edge its axis swept. When that edge
    # is a (resolvable) circular arc, keep the curvature as a BeamRevolve rather
    # than collapsing it to the straight chord between the two guide endpoints.
    if seg.tag == "curved_segment" and edge_curve_resolver is not None:
        revolve = _curved_seg_to_beam_revolve(name, seg, sec, mat, parent, metadata, up, pos, edge_curve_resolver)
        if revolve is not None:
            return revolve
        # Not a circular arc — a spline arc. Keep the analytical curve (the exact
        # ACIS intcurve) as a BeamCurved rather than collapsing it to the chord.
        curved = _curved_seg_to_beam_curved(name, seg, sec, mat, parent, metadata, up, pos, edge_curve_resolver)
        if curved is not None:
            return curved

    n1 = parent.nodes.add(Node(pos_to_floats(pos["1"])))
    n2 = parent.nodes.add(Node(pos_to_floats(pos["2"])))

    try:
        bm = Beam(name, n1, n2, sec=sec, mat=mat, parent=parent, metadata=metadata, up=up)
    except VectorNormalizeError:
        logger.warning(f"Beam '{name}' has coincident nodes. Will skip for now")
        return None

    return bm


def _curved_seg_to_beam_curved(name, seg, sec, mat, parent, metadata, up, pos, edge_curve_resolver):
    """Build a :class:`BeamCurved` for a curved segment whose SAT edge is a spline
    arc (an ``intcurve``). Returns ``None`` (caller falls back to a straight chord)
    when there is no ``sat_reference`` edge or the edge resolves to no spline curve.
    """
    from ada.geom import curves as geo_cu

    edge_el = seg.find(".//sat_reference/edge")
    if edge_el is None:
        return None
    edge_ref = edge_el.attrib.get("edge_ref")
    if not edge_ref:
        return None

    curve = edge_curve_resolver(edge_ref)
    if not isinstance(curve, (geo_cu.BSplineCurveWithKnots, geo_cu.RationalBSplineCurveWithKnots)):
        return None

    from ada.api.beams import BeamCurved

    n1 = parent.nodes.add(Node(pos_to_floats(pos["1"])))
    n2 = parent.nodes.add(Node(pos_to_floats(pos["2"])))
    try:
        return BeamCurved(name, n1, n2, curve, sec, up=up, mat=mat, parent=parent, metadata=metadata)
    except Exception as e:  # noqa: BLE001 - never let one bad arc abort the whole import
        logger.warning(f"Curved beam '{name}': failed to build BeamCurved ({e}); using straight chord")
        return None


def _curved_seg_to_beam_revolve(name, seg, sec, mat, parent, metadata, up, pos, edge_curve_resolver):
    """Build a :class:`BeamRevolve` for a curved segment whose SAT edge is an arc.

    Returns ``None`` (so the caller falls back to a straight chord) when there is
    no ``sat_reference`` edge, the edge is unresolved, the curve is not a circle/
    ellipse, or the arc is degenerate.
    """
    from ada.geom import curves as geo_cu

    edge_el = seg.find(".//sat_reference/edge")
    if edge_el is None:
        return None
    edge_ref = edge_el.attrib.get("edge_ref")
    if not edge_ref:
        return None

    curve = edge_curve_resolver(edge_ref)
    if not isinstance(curve, (geo_cu.Circle, geo_cu.Ellipse)):
        return None

    p1 = pos_to_floats(pos["1"])
    p2 = pos_to_floats(pos["2"])
    center = list(curve.position.location)
    axis = list(curve.position.axis)
    radius = float(curve.radius) if isinstance(curve, geo_cu.Circle) else float(curve.semi_axis1)

    rev_curve = _curve_revolve_from_arc(p1, p2, center, axis, radius)
    if rev_curve is None:
        return None

    from ada import BeamRevolve

    try:
        return BeamRevolve(name, rev_curve, sec, up=up, mat=mat, parent=parent, metadata=metadata)
    except Exception as e:  # noqa: BLE001 - never let one bad arc abort the whole import
        logger.warning(f"Curved beam '{name}': failed to build BeamRevolve ({e}); using straight chord")
        return None


def _curve_revolve_from_arc(p1, p2, center, axis, radius):
    """A :class:`~ada.api.curves.CurveRevolve` seated exactly on the SAT arc.

    ``center``/``axis``/``radius`` come straight from the ACIS ellipse-curve, so
    the arc is reproduced rather than re-fitted from three points. The rotation
    axis is oriented so the right-handed sweep from ``p1`` to ``p2`` is positive,
    which is what :meth:`BeamRevolve.solid_geom` revolves.
    """
    import math

    from ada import Direction, Placement, Point
    from ada.api.curves import CurveRevolve

    a = np.asarray(center, dtype=float)
    ax = np.asarray(axis, dtype=float)
    n = float(np.linalg.norm(ax))
    if n < 1e-12:
        return None
    ax = ax / n

    v1 = np.asarray(p1, dtype=float) - a
    v2 = np.asarray(p2, dtype=float) - a
    sin_a = float(np.dot(np.cross(v1, v2), ax))
    cos_a = float(np.dot(v1, v2))
    ang = math.atan2(sin_a, cos_a)  # signed sweep about ax, (-pi, pi]
    if abs(ang) < 1e-9:
        return None
    if ang < 0:  # orient axis so p1 -> p2 is a positive (right-handed) sweep
        ax = -ax
        ang = -ang

    xvec1 = a - np.asarray(p1, dtype=float)
    if float(np.linalg.norm(xvec1)) < 1e-12:
        return None

    curve = CurveRevolve(
        Point(*p1),
        Point(*p2),
        radius=float(radius),
        rot_axis=Direction(*ax),
        rot_origin=Point(*a),
        angle=float(np.rad2deg(ang)),
    )
    # The (rot_origin + angle) form skips the constructor branches that seat the
    # profile frame, so set it here the way the radius branch would.
    place = Placement(Point(*p1), xdir=Direction(*xvec1).get_normalized(), zdir=Direction(*ax))
    curve._profile_normal = place.xdir
    curve._profile_perpendicular = place.ydir
    return curve


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
