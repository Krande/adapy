from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ada import Point
from ada.cadit.sat.read.bsplinesurface import create_bsplinesurface_from_sat
from ada.cadit.sat.read.curve import create_bspline_curve_from_sat
from ada.cadit.sat.read.face import CurvedPlateFactory
from ada.geom.curves import OrientedEdge, Edge, EdgeLoop
from ada.geom.surfaces import AdvancedFace, FaceBound, BSplineSurfaceWithKnots

if TYPE_CHECKING:
    from ada.cadit.sat.store import SatStore


def iter_loop_coedges(loop: list[str], sat_store: SatStore) -> Iterable[OrientedEdge]:
    """Iterates over the edges of the face."""
    # Coedge indices
    coedge_ref = 7
    edge_idx = 9
    coedge_sense_idx = 10

    # Edge indices
    start_idx = 6
    stop_idx = 8
    point_idx = 7

    coedge_start_id = loop[coedge_ref]
    coedge_first = sat_store.get(coedge_start_id)

    coedge_first_direction = str(coedge_first[-4])

    # Coedge row
    next_coedge_idx = 6 if coedge_first_direction == "forward" else 7

    next_coedge = True
    coedge_next_id = coedge_first[next_coedge_idx]
    edge = sat_store.get(coedge_first[edge_idx])
    if 'forward' in coedge_first[coedge_sense_idx]:
        ori = True
    else:
        ori = False
    v1 = sat_store.get(edge[start_idx])
    v2 = sat_store.get(edge[stop_idx])
    p1 = Point(*[float(x) for x in sat_store.get(v1[point_idx])[6:9]])
    p2 = Point(*[float(x) for x in sat_store.get(v2[point_idx])[6:9]])

    yield OrientedEdge(p1, p2, Edge(p1, p2), ori)

    max_iter = 500
    i = 0
    while next_coedge is True:
        coedge = sat_store.get(coedge_next_id)
        edge = sat_store.get(coedge[edge_idx])
        v1 = sat_store.get(edge[start_idx])
        v2 = sat_store.get(edge[stop_idx])
        p1 = Point(*[float(x) for x in sat_store.get(v1[point_idx])[6:9]])
        p2 = Point(*[float(x) for x in sat_store.get(v2[point_idx])[6:9]])
        if 'forward' in coedge[coedge_sense_idx]:
            ori = True
        else:
            ori = False
        yield OrientedEdge(p1, p2, Edge(p1, p2), ori)

        coedge_next_id = coedge[next_coedge_idx]
        if coedge_next_id == coedge_start_id:
            next_coedge = False

        i += 1
        if i > max_iter:
            raise ValueError(f"Found {i} points which is over max={max_iter}")


def get_face_bound(face_data_list: list[str], sat_store: SatStore) -> list[FaceBound]:
    """Gets the edge loop from the SAT object data."""
    loop = sat_store.get(face_data_list[7])
    edges = []

    for edge in iter_loop_coedges(loop, sat_store):
        edges.append(edge)

    return [FaceBound(bound=EdgeLoop(edges), orientation=True)]

def get_face_surface(face_data_list: list[str], sat_store: SatStore) -> BSplineSurfaceWithKnots:
    face_spline_str = sat_store.get(face_data_list[10], return_str=True)
    face_surface = create_bsplinesurface_from_sat(face_spline_str)
    if face_surface is None:
        raise NotImplementedError("Only BSplineSurfaces are supported.")
    return face_surface

def create_advanced_face_from_sat(face_sat_id: id, sat_store: SatStore) -> AdvancedFace:
    """Creates an AdvancedFace from the SAT object data."""
    same_sense = True
    face_data = sat_store.get(face_sat_id)
    bounds = get_face_bound(face_data, sat_store)

    face_surface = get_face_surface(face_data, sat_store)

    if len(bounds) < 1:
        raise NotImplementedError("No bounds found.")

    return AdvancedFace(
        bounds=bounds,
        face_surface=face_surface,
        same_sense=same_sense,
    )
