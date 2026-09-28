from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ada.cad import active_backend
from ada.cad.shape_cache import cached_solid_by_guid, get_solid_occ

if TYPE_CHECKING:
    from ada import Plate

# A module-level cache of solids, keyed by plate GUID


def plates_min_distance(pl1: Plate, pl2: Plate, tol: float = 1e-3) -> float | None:
    """
    Public API: ensure both solids are cached, then dispatch to the GUID-based
    LRU cache. Returns the minimal distance (m) when the plates are within
    ``tol``, else ``None``.

    The cached solids are in the local system of each plate. A distance is unchanged by a rigid motion, so plates
    that share their world transform (e.g. plates of one part) are measured locally. Otherwise their world bodies
    are measured.
    """
    import numpy as np

    m1, m2 = pl1.world_matrix(), pl2.world_matrix()
    if not (m1 is None and m2 is None) and not (m1 is not None and m2 is not None and np.array_equal(m1, m2)):
        dist = active_backend().distance(pl1.shape_global(), pl2.shape_global())
        return dist if dist <= tol else None

    # 1) build/cache solids
    get_solid_occ(pl1)
    get_solid_occ(pl2)

    # 2) canonical key order
    g1, g2 = sorted((pl1.guid, pl2.guid))
    return _plates_min_distance_by_guid(g1, g2, tol)


@lru_cache(maxsize=None)
def _plates_min_distance_by_guid(guid1: str, guid2: str, tol: float) -> float | None:
    """
    LRU-cached on (guid1, guid2, tol). Fetches solids from the cache and
    computes the minimal distance once via the active CAD backend.
    """
    s1 = cached_solid_by_guid(guid1)
    s2 = cached_solid_by_guid(guid2)
    dist = active_backend().distance(s1, s2)
    return dist if dist <= tol else None
