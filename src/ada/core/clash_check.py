from __future__ import annotations

import traceback
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Iterable, List

import numpy as np

from ada.api.transforms import EquationOfPlane
from ada.config import logger

from .utils import Counter
from .vector_utils import (
    intersect_calc,
    is_between_endpoints,
    is_parallel,
    vector_length,
)

if TYPE_CHECKING:
    from ada import Assembly, Beam, Part, Pipe, PipeSegStraight, Plate, PrimCyl
    from ada.api.containers import Beams


def basic_intersect(bm: Beam, margins, all_beam_containers: list[Beams]):
    """Beams whose volume could touch ``bm``'s -- the candidate set a cross-check then decides on.

    BOX AGAINST BOX, not box against endpoints. ``Beams.get_beams_within_volume`` indexes beams by
    their END NODES, so a beam counted as a candidate only if one of its ends lands inside ``bm``'s
    box. That silently drops the MID-SPAN CROSSING: two long beams crossing away from either's
    ends have no endpoint in the other's box, are never offered to ``beam_cross_check``, and so
    were never a joint however plainly they met. Any filter here only has to ADMIT every pair that
    could touch -- the decision is made downstream and is exact -- so it costs nothing to be
    generous and it costs a real joint to be clever.
    """
    if bm.section.type == "gensec":
        return bm, []
    try:
        vol = bm.bbox().minmax
    except ValueError as e:
        logger.error(f"Intersect bbox skipped: {e}\n{traceback.format_exc()}")
        return None

    pad = margins or 0.0
    lo = [float(v) - pad for v in vol[0]]
    hi = [float(v) + pad for v in vol[1]]

    def could_touch(other: Beam) -> bool:
        if other is bm or other == bm:
            return False
        try:
            o_lo, o_hi = other.bbox().minmax
        except ValueError:
            return True  # a beam whose box cannot be built is offered rather than dropped
        return all(lo[k] <= float(o_hi[k]) + pad and float(o_lo[k]) - pad <= hi[k] for k in range(3))

    beams = filter(could_touch, chain.from_iterable(all_beam_containers))
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
    from ada import Node

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
            # The MIDPOINT of the two closest points, not the one on bm1's line. `beam_cross_check`
            # returns the point on the FIRST member, so a near miss inside the out-of-plane
            # tolerance yields a different point depending on which member is asked -- up to the
            # tolerance apart, far beyond `point_tol`, so the two never merge and ONE physical
            # contact is registered as TWO joints. Computed here rather than in `beam_cross_check`,
            # whose `s`/`t` callers (the joint detailers) want the point on a specific member.
            cd_ = bm2.n1.p + t * (bm2.n2.p - bm2.n1.p)
            new_node = Node((point + cd_) / 2.0)
            n = nodes.add(new_node, point_tol=point_tol)
            if n not in nmap.keys():
                nmap[n] = [bm1]
            if bm1 not in nmap[n]:
                nmap[n].append(bm1)
            if bm2 not in nmap[n]:
                nmap[n].append(bm2)


def are_plates_touching(pl1: Plate, pl2: Plate, tol=1e-3) -> bool:
    """Check if two plates are within tolerance of each other.

    Delegates to ``plates_min_distance``, which is the same question already
    answered against the active CAD backend -- and cached per plate-GUID pair.
    This function used to duplicate that logic against pythonocc directly, which
    is how it got left behind when the rest of the module moved to the backend:
    it built its own distance from ``solid_occ()``, and ``solid_occ()`` returns
    an active-backend handle (an adacpp ``ShapeHandle`` by default), which the
    pythonocc ``BRepExtrema_DistShapeShape`` loaders reject with
    ``TypeError: ... argument 2 of type 'TopoDS_Shape const &'``. So it raised on
    every call under the default backend. Nothing in the repo calls it, which is
    why nothing noticed.

    Returns a plain bool rather than the old ``BRepExtrema_DistShapeShape``
    object, which exists only on the pythonocc path. Note that it deliberately
    does NOT forward the distance: ``plates_min_distance`` returns ``0.0`` for
    coincident plates, which is falsy, so a caller testing the result for
    truthiness would read the most clearly-touching case as "not touching". A
    bool has no such edge, and a caller reaching for the old ``.Value()`` gets a
    loud ``AttributeError`` rather than a silently wrong number.
    """
    from ada.core.clash_distance import plates_min_distance

    # `is not None`, not truthiness -- see the 0.0 note above.
    return plates_min_distance(pl1, pl2, tol) is not None


def filter_away_beams_along_plate_edges(pl: Plate, beams: Iterable[Beam]) -> List[Beam]:
    corners = [tuple(n) for n in pl.poly.points3d]
    edge_vectors = [seg.direction for seg in pl.poly.segments3d]
    # filter away all beams with both ends on any of corner points of the plate
    beams_not_along_plate_edge = []

    # todo: check if beam aligned to the plate edge but exceed the plate edge and will not have a point inside edge
    for bm in beams:
        t1 = tuple(bm.n1.p)
        t2 = tuple(bm.n2.p)
        is_aligned_to_one_of_edges = False
        for edge_vec in edge_vectors:
            if edge_vec.is_equal(bm.xvec):
                is_aligned_to_one_of_edges = True
                break
        is_along_edge = False
        if is_aligned_to_one_of_edges:
            for n in pl.nodes:
                if is_between_endpoints(n.p, bm.n1.p, bm.n2.p, incl_endpoints=True):
                    is_along_edge = True
                    break

        if is_along_edge:
            continue

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


def _beam_reach(bm: Beam) -> float:
    """How far a beam's BODY can sit from its axis -- half its largest section dimension.

    A beam attached to a plate is not required to have its axis in the plate: a girder carrying an
    eccentricity of half its depth has an axis half a section below the plate it holds up, and the
    same girder read back from IFC has that offset baked into the axis instead. Both are the same
    steel touching the same plate, so the test has to be "does this beam's body reach the plate",
    not "is this beam's axis inside it".
    """
    sec = getattr(bm, "section", None)
    if sec is None:
        return 0.0
    dims = [getattr(sec, attr, None) for attr in ("h", "w_top", "w_btn", "r")]
    sizes = [abs(float(d)) for d in dims if d is not None]
    if not sizes:
        return 0.0
    return 0.5 * max(sizes)


def find_beams_connected_to_plate(pl: Plate, beams: list[Beam], tol: float | None = None) -> list[Beam]:
    """Beams whose mid-span reaches the plate: inside its bounding box, grown by the beam's own
    half-section (see :func:`_beam_reach`) plus ``tol``, which defaults to the plate's thickness.

    WHY IT IS NOT A PLAIN "is the midpoint inside the box" TEST. A plate's bounding box is a slab a
    few millimetres thick, and the beams this is meant to find are precisely the ones lying ON one
    of its faces -- so an untolerated test decides membership on the last bits of a float, and the
    same structure answers differently depending on how its beam axes happen to be expressed.
    Measured on one demo frame: adapy's own model found 16 beams per plate, the same model read
    back from IFC (where a beam's eccentricity is baked into its axis rather than carried beside
    it) found 0, and a clash check lost two thirds of its plate joints on a round trip.
    """
    from ada import Node
    from ada.api.containers import Nodes

    nid = Counter(1)
    mids = {}
    for bm in beams:
        mid = np.asarray(bm.placement.get_absolute_placement().origin + (bm.n2.p + bm.n1.p) / 2, dtype=float)
        mids[id(bm)] = mid
    nodes = Nodes([Node(mids[id(bm)], next(nid), refs=[bm]) for bm in beams])

    if tol is None:
        tol = abs(float(pl.t))
    reaches = {id(bm): _beam_reach(bm) for bm in beams}
    widest = max(reaches.values(), default=0.0)

    bbox = pl.bbox()
    pmin = np.asarray(bbox.p1, dtype=float)
    pmax = np.asarray(bbox.p2, dtype=float)
    # One coarse query with the WIDEST pad, then each candidate is re-tested against its own --
    # a container query takes a single box, and padding every beam by the widest section in the
    # model would pull in beams that only a deeper neighbour could have reached.
    coarse = np.array([tol + widest] * 3, dtype=float)
    res = nodes.get_by_volume(pmin - coarse, pmax + coarse)

    all_beams_within = []
    for node in res:
        for bm in node.refs:
            pad = tol + reaches.get(id(bm), 0.0)
            mid = mids.get(id(bm))
            if mid is None:
                continue
            if np.all(mid >= pmin - pad) and np.all(mid <= pmax + pad):
                all_beams_within.append(bm)
    return all_beams_within


def penetration_check(part: Part):
    a = part.get_assembly()
    cog = part.nodes.vol_cog()
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
                        part.add_boolean(
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
                    logger.debug(f"{seg.name=} {is_clashing=} with {plate.name=}")
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
        part.add_boolean(PrimCyl(name, p1, p2, seg.section.r + 0.1), add_to_layer=add_to_layer)

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


@dataclass
class PlateConnections:
    mid_span_connected: dict[Plate, list[Plate]]
    edge_connected: dict[Plate, list[Plate]]


def _classify_connection(
    source: Plate,
    target: Plate,
    hits: list[np.ndarray],
    clears: list[np.ndarray],
    parallel: bool,
    edge_conn: dict[Plate, list[Plate]],
    mid_conn: dict[Plate, list[Plate]],
) -> None:
    """
    Decide whether `target` is an edge‐ or mid‐span connection of `source`,
    and record it in the appropriate dict. Kept in sync with the inlined
    classifier in `find_edge_connected_perpendicular_plates` (which is what the
    meshing pipeline actually calls); see the rationale there.
    """
    if not parallel and len(clears) == 2:
        mid_conn.setdefault(source, []).append(target)
    elif (
        (parallel and len(clears) == 2)
        or (len(hits) == 2 and len(clears) == 0)
        or (not parallel and len(hits) >= 2 and len(clears) == 1)
    ):
        edge_conn.setdefault(source, []).append(target)


def find_edge_connected_perpendicular_plates(plates: list[Plate]) -> PlateConnections:
    """Find all plates that are connected at an edge and are perpendicular to that edge."""
    # OCC-backend solid build/distance — imported lazily so this module stays
    # importable under a non-OCC CAD backend (e.g. adacpp). See the internal design notes.
    from ada.cad.shape_cache import get_solid_occ
    from ada.core.clash_distance import plates_min_distance

    # 1) Precompute every per‐plate bit once
    pdata: dict[str, dict] = {}
    for pl in plates:
        # absolute placement → 3D points
        place = pl.placement.get_absolute_placement()
        pts3d = np.asarray(place.origin + pl.poly.points3d, dtype=float)

        # plane equation (unit‐normal) + store
        eq = EquationOfPlane(pl.poly.origin, pl.poly.normal, pl.poly.ydir)

        pdata[pl.guid] = {
            "plate": pl,
            "normal": pl.poly.normal,
            "eq": eq,
            "pts": pts3d,
        }

        # build & cache its solid once
        get_solid_occ(pl)

    edge_connected: dict[Plate, list[Plate]] = {}
    mid_span_connected: dict[Plate, list[Plate]] = {}

    # 2) *Exact* same nested‐loop + classification as your old code
    for guid1, d1 in pdata.items():
        pl1 = d1["plate"]
        eq1 = d1["eq"]
        n1 = d1["normal"]
        pts1 = d1["pts"]

        for guid2, d2 in pdata.items():
            if guid1 == guid2:
                continue

            pl2 = d2["plate"]
            pts2 = d2["pts"]
            n2 = d2["normal"]

            # a) must be perpendicular
            parallel = n1.is_equal(n2)

            # b) find intersection‐points of pl2’s corners in pl1’s plane
            hits = eq1.return_points_in_plane(pts2)
            if hits.size == 0:
                continue

            # c) must actually touch
            if plates_min_distance(pl1, pl2) is None:
                continue

            # d) of those hits, which lie *strictly inside* pl1?
            #    (we compare against its corner‐points, with a tiny tol)
            tol = 1e-6
            clears = []
            for pt in hits:
                if not any(np.allclose(pt, corner, atol=tol) for corner in pts1):
                    clears.append(pt)

            # Classify the (touching) contact so it gets imprinted. ``hits`` are pl2
            # corners lying in pl1's plane; ``clears`` are those NOT at a pl1 corner. A
            # shared edge of nonzero length needs >= 2 in-plane corners whose endpoints
            # are at pl1 corners (clears==0), strictly interior (clears==2), or a mix
            # (clears==1, a T-junction). This is a strict superset of the original two
            # tests plus the previously-dropped T-junction case, which fell through
            # both branches and left coincident-but-distinct nodes along the edge. Both
            # buckets imprint via occ.fragment; the split only orders the passes.
            if not parallel and len(clears) == 2:
                mid_span_connected.setdefault(pl1, []).append(pl2)
            elif (
                (parallel and len(clears) == 2)
                or (len(hits) == 2 and len(clears) == 0)
                or (not parallel and len(hits) >= 2 and len(clears) == 1)
            ):
                edge_connected.setdefault(pl1, []).append(pl2)

    return PlateConnections(mid_span_connected, edge_connected)


def find_plates_that_share_only_1_edge(plates) -> dict[Plate, list[Plate]]:
    """Find all plates that are connected to a plate edge and are perpendicular to that edge"""
    plates = list(plates)
    edge_connected = dict()

    for pl1 in plates:
        place1 = pl1.placement.get_absolute_placement()
        eop = EquationOfPlane(pl1.poly.origin, pl1.poly.normal, pl1.poly.ydir)
        p13d = place1.origin + pl1.poly.points3d
        for pl2 in plates:
            if pl1 == pl2:
                continue
            place2 = pl2.placement.get_absolute_placement()
            p23d = place2.origin + pl2.poly.points3d
            res = eop.return_points_in_plane(np.asarray(p23d))
            # pop out the elements in the numpy array res that are rows in p13d
            res_clear = [r for r in res if not any(np.all(r == p) for p in p13d)]
            if len(res_clear) == 2:
                if pl1 not in edge_connected:
                    edge_connected[pl1] = []
                edge_connected[pl1].append(pl2)

    return edge_connected
