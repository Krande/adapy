from __future__ import annotations

import traceback
from dataclasses import dataclass
from itertools import chain
from typing import Iterable, List

import numpy as np

from ada import Assembly, Beam, Node, Part, Pipe, PipeSegStraight, Plate, PrimCyl
from ada.config import get_logger

from .utils import Counter
from .vector_utils import EquationOfPlane, intersect_calc, is_parallel, vector_length

logger = get_logger()


def basic_intersect(bm: Beam, margins, all_parts: [Part]):
    if bm.section.type == "gensec":
        return bm, []
    try:
        vol = bm.bbox().minmax
    except ValueError as e:
        logger.error(f"Intersect bbox skipped: {e}\n{traceback.format_exc()}")
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
        logger.debug(f"beams {bm1} {bm2} are parallel")
        return None

    if v_len(ab_ - cd_) > outofplane_tol:
        logger.debug("The two lines do not intersect within given tolerances")
        return None

    return ab_, s, t


def are_beams_connected(bm1: Beam, beams: List[Beam], out_of_plane_tol, point_tol, nodes, nmap) -> None:
    # TODO: Function should be renamed, or return boolean. Unclear what the function does at the moment
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

    dss = compute_minimal_distance_between_shapes(pl1.solid(), pl2.solid())
    if dss.Value() <= tol:
        return dss

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
    from .vector_utils import is_clockwise, is_on_line

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

    nid = Counter(1)
    nodes = Nodes([Node((bm.n2.p + bm.n1.p) / 2, next(nid), refs=[bm]) for bm in beams])
    res = nodes.get_by_volume(pl.bbox().p1, pl.bbox().p2)

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


@dataclass
class PipeClash:
    seg: PipeSegStraight
    plate: Plate

    @staticmethod
    def pipe_penetration_check(a: Assembly) -> list[PipeClash]:
        plates = list(a.get_all_physical_objects(by_type=Plate))
        pipes = list(a.get_all_physical_objects(by_type=Pipe))
        pipe_segments = []
        for pipe in pipes:
            pipe_segments += list(filter(lambda x: isinstance(x, PipeSegStraight), pipe.segments))

        clashes = []

        for seg in pipe_segments:
            p1 = seg.p1.p
            p2 = seg.p2.p
            for plate in plates:
                origin = plate.placement.origin
                normal = plate.placement.zdir

                v1 = (p1 - origin) * normal
                v2 = (p2 - origin) * normal
                is_clashing = np.dot(v1, v2) < 0
                if is_clashing:
                    print(f"{seg.name=} {is_clashing=} with {plate.name=}")
                    clashes.append(PipeClash(seg, plate))
        return clashes

    def reinforce_plate_pipe_pen(self, add_to_layer: str = None):
        seg = self.seg
        plate = self.plate

        p1 = seg.p1.p
        p2 = seg.p2.p

        pipe = seg.parent
        part = plate.parent

        # Cut away in plate and stringers here
        name = f"{plate.name}_{pipe.name}_{seg.name}_pen"
        part.add_penetration(PrimCyl(name, p1, p2, seg.section.r + 0.1), add_to_layer=add_to_layer)

        # specify reinforcement here
        reinforce_name = Counter(prefix=f"{plate.name}_{pipe.name}_{seg.name}_reinf_")

        eop = EquationOfPlane(plate.placement.origin, plate.placement.zdir, plate.placement.ydir)
        xdir, ydir, zdir = eop.get_lcsys()

        pp = eop.project_point_onto_plane(p1) + plate.t * plate.placement.zdir

        dist = 3 * seg.section.r

        bm_p1 = pp - dist * xdir - dist * ydir
        bm_p2 = pp + dist * xdir - dist * ydir
        bm_p3 = pp + dist * xdir + dist * ydir
        bm_p4 = pp - dist * xdir + dist * ydir

        part.add_beam(Beam(next(reinforce_name), bm_p1, bm_p2, "HP140x8"), add_to_layer=add_to_layer)
        part.add_beam(Beam(next(reinforce_name), bm_p2, bm_p3, "HP140x8"), add_to_layer=add_to_layer)
        part.add_beam(Beam(next(reinforce_name), bm_p3, bm_p4, "HP140x8"), add_to_layer=add_to_layer)
        part.add_beam(Beam(next(reinforce_name), bm_p4, bm_p1, "HP140x8"), add_to_layer=add_to_layer)
