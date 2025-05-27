from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape

from ada.occ.utils import compute_minimal_distance_between_shapes

from .geom.cache import get_solid_occ, occ_solid_cache

if TYPE_CHECKING:
    from ada import Plate

# A module-level cache of solids, keyed by plate GUID


def plates_min_distance(pl1: Plate, pl2: Plate, tol: float = 1e-3) -> BRepExtrema_DistShapeShape | None:
    """
    Public API: ensure both solids are cached, then dispatch
    to the GUID‐based LRU cache.
    """
    # 1) build/cache solids
    get_solid_occ(pl1)
    get_solid_occ(pl2)

    # 2) canonical key order
    g1, g2 = sorted((pl1.guid, pl2.guid))
    return _plates_min_distance_by_guid(g1, g2, tol)


@lru_cache(maxsize=None)
def _plates_min_distance_by_guid(guid1: str, guid2: str, tol: float) -> BRepExtrema_DistShapeShape | None:
    """
    LRU‐cached on (guid1, guid2, tol). Fetches
    solids from _solid_cache and computes distance once.
    """
    s1 = occ_solid_cache[guid1]
    s2 = occ_solid_cache[guid2]
    dss = compute_minimal_distance_between_shapes(s1, s2)
    return dss if dss.Value() <= tol else None
