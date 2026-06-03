"""Extract polyline boundaries of cut surfaces created by negative-volume booleans on a beam.

Backend-agnostic: the uncut solid and the cutters are built through the active
CAD backend, and the cut + face/edge extraction is delegated to the backend's
``cut_surfaces`` verb (OccBackend uses pythonocc-core, adacpp uses its native
OCCT — neither relies on the other). This module only maps the backend's plain
data into ``CutSurface`` / ``CutEdge`` dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.config import logger
from ada.geom.direction import Direction
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada.api.beams import Beam


@dataclass
class CutEdge:
    """A single edge of a cut surface's boundary, classified by curve type.

    For straight edges (``edge_type == "Line"``), ``points`` has exactly two
    entries (start and end). For curved edges, ``points`` is a polyline
    discretization following the underlying curve.
    """

    edge_type: str
    points: list[Point]


@dataclass
class CutSurface:
    """A face on the cut beam whose surface originates from a negative-volume cutter.

    ``outer_edges`` lists the boundary edges in traversal order, each labelled
    with its curve type so callers can distinguish straight runs from arcs.
    ``outer_polyline`` is the same boundary flattened into a single list of
    points (consecutive duplicates removed).
    """

    surface_type: str
    outer_edges: list[CutEdge]
    outer_polyline: list[Point]
    inner_polylines: list[list[Point]]
    sample_normal: Direction


def _cutter_handle(boolean, backend):
    """Build a cutter as an active-backend shape handle."""
    from ada.api.primitives.bool_half_space import BoolHalfSpace

    prim = boolean.primitive if hasattr(boolean, "primitive") else boolean
    if isinstance(prim, BoolHalfSpace):
        return backend.make_halfspace(prim.poly.origin, prim.poly.normal, bool(prim.flip))
    if hasattr(prim, "solid_occ"):
        try:
            return prim.solid_occ()
        except Exception as ex:
            logger.warning(f"Failed to build cutter {prim}: {ex}")
            return None
    return None


def extract_cut_surfaces(
    beam: Beam,
    deflection: float = 1e-3,
    tol: float = 1e-4,
) -> list[CutSurface]:
    """Return the cut-surface polylines on the beam after applying its negative-volume booleans.

    For each face on the cut solid that originated from a cutter (not from the
    original un-cut beam solid), returns one CutSurface with its outer polyline
    in world coordinates. Curved boundary edges are discretized using
    `deflection` (max sagitta error). Coincident polyline points within `tol`
    are de-duplicated.
    """
    from ada.api.beams import geom_beams as geo_conv
    from ada.cad import active_backend
    from ada.geom.booleans import BoolOpEnum

    if not beam.booleans:
        return []

    backend = active_backend()

    cutters = []
    for b in beam.booleans:
        bool_op = getattr(b, "bool_op", None)
        if bool_op is not None and bool_op != BoolOpEnum.DIFFERENCE:
            continue
        handle = _cutter_handle(b, backend)
        if handle is not None:
            cutters.append(handle)

    if not cutters:
        return []

    geom = geo_conv.straight_beam_to_geom(beam)
    geom.bool_operations = []
    solid = backend.build(geom)

    try:
        raw = backend.cut_surfaces(solid, cutters, deflection, tol)
    except Exception as ex:
        logger.warning(f"cut_surfaces failed for beam {beam.name}: {ex}")
        return []

    surfaces: list[CutSurface] = []
    for surface_type, sample_normal, outer_edges, outer, inners in raw:
        surfaces.append(
            CutSurface(
                surface_type=surface_type,
                outer_edges=[CutEdge(edge_type=et, points=[Point(*p) for p in pts]) for et, pts in outer_edges],
                outer_polyline=[Point(*p) for p in outer],
                inner_polylines=[[Point(*p) for p in poly] for poly in inners],
                sample_normal=Direction(*sample_normal),
            )
        )
    return surfaces
