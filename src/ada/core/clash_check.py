import logging
import traceback
from itertools import chain
from typing import Iterable, List

import numpy as np

from ada.concepts.levels import Part
from ada.concepts.piping import PipeSegStraight
from ada.concepts.points import Node
from ada.concepts.primitives import PrimCyl
from ada.concepts.structural import Beam, Plate

from .utils import Counter, intersect_calc, is_parallel, vector_length


def basic_intersect(bm: Beam, margins, all_parts: [Part]):
    if bm.section.type == "gensec":
        return bm, []
    try:
        vol = bm.bbox
    except ValueError as e:
        logging.error(f"Intersect bbox skipped: {e}\n{traceback.format_exc()}")
        return None
    vol_in = [x for x in zip(vol[0], vol[1])]
    beams = filter(
        lambda x: x != bm,
        chain.from_iterable([p.beams.get_beams_within_volume(vol_in, margins=margins) for p in all_parts]),
    )
    return bm, beams


def beam_cross_check(bm1: Beam, bm2: Beam, outofplane_tol=0.1):
    """Calculate intersection of beams and return point, s, t"""
    p_check = is_parallel
    i_check = intersect_calc
    v_len = vector_length
    a = bm1.n1.p
    b = bm1.n2.p
    c = bm2.n1.p
    d = bm2.n2.p

    ab = b - a
    cd = d - c

    s, t = i_check(a, c, ab, cd)

    ab_ = a + s * ab
    cd_ = c + t * cd

    if p_check(ab, cd):
        logging.debug(f"beams {bm1} {bm2} are parallel")
        return None

    if v_len(ab_ - cd_) > outofplane_tol:
        logging.debug("The two lines do not intersect within given tolerances")
        return None

    return ab_, s, t


def are_beams_connected(bm1: Beam, beams: List[Beam], out_of_plane_tol, point_tol, nodes, nmap) -> None:
    for bm2 in beams:
        if bm1 == bm2:
            continue
        res = beam_cross_check(bm1, bm2, out_of_plane_tol)
        if res is None:
            continue
        point, s, t = res
        t_len = (abs(t) - 1) * bm2.length
        s_len = (abs(s) - 1) * bm1.length
        if t_len > bm2.length / 2 or s_len > bm1.length / 2:
            continue
        if point is not None:
            new_node = Node(point)
            n = nodes.add(new_node, point_tol=point_tol)
            if n not in nmap.keys():
                nmap[n] = [bm1]
            if bm1 not in nmap[n]:
                nmap[n].append(bm1)
            if bm2 not in nmap[n]:
                nmap[n].append(bm2)


def are_plates_touching(pl1: Plate, pl2: Plate, tol=1e-3):
    """Check if two plates are within tolerance of each other"""
    from ..occ.utils import compute_minimal_distance_between_shapes

    dss = compute_minimal_distance_between_shapes(pl1.solid, pl2.solid)
    if dss.Value() <= tol:
        return dss
    else:
        return None


def filter_away_beams_along_plate_edges(pl: Plate, beams: Iterable[Beam]) -> List[Beam]:
    corners = [n for n in pl.poly.points3d]

    # filter away all beams with both ends on any of corner points of the plate
    beams_not_along_plate_edge = []

    for bm in beams:
        t1 = tuple(bm.n1.p)
        t2 = tuple(bm.n2.p)

        if t1 in corners:
            cindex = corners.index(t1)

            nextp = corners[0] if cindex == len(corners) - 1 else corners[cindex + 1]
            prevp = corners[cindex - 1]

            if t2 == nextp or t2 == prevp:
                continue
        beams_not_along_plate_edge.append(bm)

    return beams_not_along_plate_edge


def filter_beams_along_plate_edges(pl: Plate, beams: Iterable[Beam]):
    from .utils import is_clockwise, is_on_line

    corners = [n for n in pl.poly.points3d]
    corners += [corners[0]]
    if is_clockwise(corners):
        corners.reverse()

    # Evalute Corner Points
    crossing_beams = []
    for s, e in zip(corners[:-1], corners[1:]):
        li = (s, e)
        res = [x for x in map(is_on_line, [(li, bm) for bm in beams]) if x is not None]
        crossing_beams += filter(lambda x: x not in crossing_beams, [r[1] for r in res])

    return crossing_beams


def find_beams_connected_to_plate(pl: Plate, beams: List[Beam]) -> List[Beam]:
    """Return all beams with their midpoints inside a specified plate for a given list of beams"""
    from ada.concepts.containers import Nodes

    bbox = list(zip(*pl.bbox))
    nid = Counter(1)
    nodes = Nodes([Node((bm.n2.p + bm.n1.p) / 2, next(nid), refs=[bm]) for bm in beams])
    res = nodes.get_by_volume(bbox[0], bbox[1])

    all_beams_within = list(chain.from_iterable([r.refs for r in res]))
    return all_beams_within


def penetration_check(part: Part):
    a = part.get_assembly()
    cog = part.nodes.vol_cog
    normal = part.placement.zdir
    for p in a.get_all_subparts():
        for pipe in p.pipes:
            for segment in pipe.segments:
                if type(segment) is PipeSegStraight:
                    assert isinstance(segment, PipeSegStraight)
                    p1, p2 = segment.p1, segment.p2
                    v1 = (p1.p - cog) * normal
                    v2 = (p2.p - cog) * normal
                    if np.dot(v1, v2) < 0:
                        part.add_penetration(
                            PrimCyl(f"{p.name}_{pipe.name}_{segment.name}_pen", p1.p, p2.p, pipe.section.r + 0.1)
                        )
