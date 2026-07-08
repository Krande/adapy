"""IfcSweptDiskSolid (rebar/pipe) serializes + tessellates natively via libtess2 — a circular
profile swept along the directrix — instead of falling back to OCC."""

from __future__ import annotations

import numpy as np
import pytest


def test_swept_disk_solid_tube_libtess2():
    pytest.importorskip("adacpp")
    import ada.geom.curves as cu
    import ada.geom.solids as so
    from ada.cad import active_backend
    from ada.cadit.ngeom.serialize import serialize_geometries
    from ada.geom.points import Point

    be = active_backend()
    if not hasattr(be, "tessellate_stream_buffer"):
        pytest.skip("no libtess2 stream")

    # straight rod, radius 0.05, length 10 along +x -> lateral area 2*pi*r*L
    directrix = cu.IndexedPolyCurve(segments=[cu.Edge(Point(0, 0, 0), Point(10, 0, 0))])
    sds = so.SweptDiskSolid(directrix=directrix, radius=0.05)
    blob = serialize_geometries([("rod", sds)])
    assert len(blob) > 32, "SweptDiskSolid serialized empty (would fall back to OCC)"
    m = be.tessellate_stream_buffer(bytes(blob), pipeline="libtess2")
    v = np.asarray(m.positions, float).reshape(-1, 3)
    idx = np.asarray(m.indices, int).reshape(-1, 3)
    assert len(idx) > 0
    tv = v[idx]
    area = 0.5 * np.linalg.norm(np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0]), axis=1).sum()
    lateral = 2 * np.pi * 0.05 * 10.0  # ~3.14 ; caps add ~2*pi*r^2 (~0.016)
    assert lateral * 0.9 < area < lateral * 1.15, area
