"""AdacppBackend.tessellate_stream — the NGEOM stream path (serialize ada.geom -> adacpp).

Skips unless an adacpp build with the tessellate_stream verb is importable (the verb lives on
the feat/libtess2-tessellator adacpp branch; published builds won't have it until released).
When present, validates the three pipelines (libtess2 / occ / cgal) on a closed unit cube.
"""

from __future__ import annotations

import math

import pytest

import ada.geom.curves as cu
import ada.geom.surfaces as su
from ada.geom.placement import Axis2Placement3D, Direction, Point


def _backend_or_skip():
    pytest.importorskip("adacpp")
    from ada.cad import AdacppBackend

    b = AdacppBackend()
    if getattr(b._cad, "tessellate_stream", None) is None:
        pytest.skip("adacpp build has no tessellate_stream verb")
    return b


def _line_oe(s, t):
    ec = cu.EdgeCurve(start=s, end=t, edge_geometry=cu.Line(s, [b - a for a, b in zip(s, t)]), same_sense=True)
    return cu.OrientedEdge(start=s, end=t, edge_element=ec, orientation=True)


def _quad(plane, vs):
    loop = cu.EdgeLoop(edge_list=[_line_oe(vs[i], vs[(i + 1) % 4]) for i in range(4)])
    return su.FaceSurface(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=plane, same_sense=True)


def _plane(o, z, x):
    return su.Plane(position=Axis2Placement3D(Point(*o), Direction(*z), Direction(*x)))


def _unit_cube():
    v = lambda x, y, z: (x, y, z)  # noqa: E731
    faces = [
        _quad(_plane((0, 0, 0), (0, 0, -1), (1, 0, 0)), [v(0, 0, 0), v(0, 1, 0), v(1, 1, 0), v(1, 0, 0)]),
        _quad(_plane((0, 0, 1), (0, 0, 1), (1, 0, 0)), [v(0, 0, 1), v(1, 0, 1), v(1, 1, 1), v(0, 1, 1)]),
        _quad(_plane((0, 0, 0), (-1, 0, 0), (0, 1, 0)), [v(0, 0, 0), v(0, 0, 1), v(0, 1, 1), v(0, 1, 0)]),
        _quad(_plane((1, 0, 0), (1, 0, 0), (0, 1, 0)), [v(1, 0, 0), v(1, 1, 0), v(1, 1, 1), v(1, 0, 1)]),
        _quad(_plane((0, 0, 0), (0, -1, 0), (1, 0, 0)), [v(0, 0, 0), v(1, 0, 0), v(1, 0, 1), v(0, 0, 1)]),
        _quad(_plane((0, 1, 0), (0, 1, 0), (1, 0, 0)), [v(0, 1, 0), v(0, 1, 1), v(1, 1, 1), v(1, 1, 0)]),
    ]
    return su.ConnectedFaceSet(cfs_faces=faces)


def _area(bm):
    import numpy as np

    pos = bm.positions.reshape(-1, 3)
    idx = bm.indices
    return float(
        sum(
            0.5 * np.linalg.norm(np.cross(pos[idx[i + 1]] - pos[idx[i]], pos[idx[i + 2]] - pos[idx[i]]))
            for i in range(0, len(idx), 3)
        )
    )


@pytest.mark.parametrize("pipeline", ["libtess2", "occ", "cgal"])
def test_stream_unit_cube(pipeline):
    backend = _backend_or_skip()
    bm = backend.tessellate_stream([("cube", _unit_cube())], pipeline=pipeline, deflection=0.05)
    assert len(bm.indices) // 3 == 12  # 6 quads -> 12 triangles
    assert math.isclose(_area(bm), 6.0, abs_tol=1e-3)  # unit cube surface area
    assert len(bm.groups) == 1 and bm.groups[0].node_id == 0
