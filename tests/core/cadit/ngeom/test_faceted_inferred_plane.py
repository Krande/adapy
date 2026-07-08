"""Faceted-brep (plain planar Face) shells serialize + tessellate natively via libtess2 — with
inner-bound holes cut and NO vertex displacement.

Regression for basin-faceted-brep.ifc:
  * FacetedBrep / plain Face had no NGEOM dispatch, so the shell fell back to OCC (which capped the
    rim's opening).
  * Once face_surface inferred a plane per plain Face, the inferred planes were transient and the
    id()-keyed surface() memo collided (a GC'd plane's id reused by the next), so faces referenced
    the wrong plane record and tessellated to displaced positions — worse the more faces, silently
    dropping ~20% of surface area on a real shell.
"""

from __future__ import annotations

import gc

import numpy as np
import pytest


def _square_face(dx: float, dy: float, dz: float):
    import ada.geom.curves as cu
    import ada.geom.surfaces as su
    from ada.geom.points import Point

    s = 0.05
    loop = cu.PolyLoop(
        polygon=[Point(dx, dy, dz), Point(dx + s, dy, dz), Point(dx + s, dy + s, dz), Point(dx, dy + s, dz)]
    )
    return su.Face(bounds=[su.FaceBound(bound=loop, orientation=True)])  # plain Face -> inferred plane


def test_faceted_shell_libtess2_no_displacement():
    pytest.importorskip("adacpp")
    import ada.geom.surfaces as su
    from ada.cad import active_backend
    from ada.cadit.ngeom.serialize import serialize_geometries

    be = active_backend()
    if not hasattr(be, "tessellate_stream_buffer"):
        pytest.skip("active backend has no libtess2 stream")

    # Many small planar faces at distinct locations; GC between builds so a naive id()-memo would
    # collide on reused plane ids.
    faces = []
    for i in range(120):
        faces.append(_square_face(0.3 + 0.1 * (i % 12), 0.25 + 0.1 * (i // 12), 0.001 * (i % 5)))
        gc.collect()
    cfs = su.ConnectedFaceSet(faces)

    def area(blob):
        m = be.tessellate_stream_buffer(bytes(blob), pipeline="libtess2")
        v = np.asarray(m.positions, float).reshape(-1, 3)
        idx = np.asarray(m.indices, int).reshape(-1, 3)
        tv = v[idx]
        return 0.5 * np.linalg.norm(np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0]), axis=1).sum()

    expected = 120 * 0.05 * 0.05  # every face is a 5cm square
    got = area(serialize_geometries([("shell", cfs)]))
    # Without the pin fix this drops well below expected (displaced faces lose area); allow only
    # tessellation-level slack.
    assert got == pytest.approx(expected, rel=1e-3), f"{got} vs {expected}"
