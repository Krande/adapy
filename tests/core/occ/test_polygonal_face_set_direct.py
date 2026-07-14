"""IfcPolygonalFaceSet emits directly as a fan-triangulated 3D mesh (no OCC fallback, no plane
flattening).

Regression for the alignment/signal OCC fallbacks: PolygonalFaceSet had no NGEOM encoder, so it fell
back to OCC. It now fan-triangulates in 3D via _direct_triangulated_meshstore — keeping the original
vertices (a per-face plane inference would flatten non-planar faces).
"""

from __future__ import annotations

import numpy as np

import ada.geom.surfaces as su
from ada.geom import Geometry
from ada.geom.points import Point


def test_polygonal_face_set_fan_triangulates_in_3d():
    from ada.occ.tessellating import BatchTessellator

    # Two faces sharing an edge: a quad (-> 2 tris) and a triangle (-> 1 tri). Non-planar quad to
    # prove the vertices aren't flattened onto a plane.
    coords = [
        Point(0, 0, 0),
        Point(1, 0, 0),
        Point(1, 1, 0.3),
        Point(0, 1, 0),  # quad (non-planar: z=0.3)
        Point(2, 0, 0),  # extra vertex for the triangle
    ]
    pfs = su.PolygonalFaceSet(coordinates=coords, faces=[[1, 2, 3, 4], [2, 5, 3]])
    ms = BatchTessellator()._direct_triangulated_meshstore(Geometry("p", pfs, None), "n")
    assert ms is not None
    idx = np.asarray(ms.indices, int).reshape(-1, 3)
    assert len(idx) == 3  # 2 (quad fan) + 1 (triangle)
    pos = np.asarray(ms.position, float).reshape(-1, 3)
    # Original vertices preserved (not projected onto a plane) — vertex 3 keeps its z=0.3.
    assert np.isclose(pos[2, 2], 0.3), pos[2]
