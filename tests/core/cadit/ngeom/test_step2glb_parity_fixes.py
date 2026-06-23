"""Regression tests for the step2glb-parity fixes in the adacpp libtess2 / NGEOM path.

Guards the fixes made while bringing adapy's libtess2 tessellation to step2glb parity:
- empty-bounds closed quadrics tessellate (sphere 0 -> ~324 tris) instead of bailing,
- meshopt_simplify cleanup is lossless at target_error 0 (the step2glb merge cleanup).

The cone axial-v parameterization and B-spline-edge natural-domain sampling are exercised by the
adacpp tests/ngeom C++ suite; here we cover the Python-reachable surface.
"""

from __future__ import annotations

import numpy as np
import pytest


def test_empty_bounds_sphere_tessellates(tmp_path):
    """A full sphere carries no FACE_BOUND; tessellate_face must not bail on empty bounds — it
    tessellates the closed quadric via tessellate_unbounded. Regression: 0 -> ~324 tris."""
    ada = pytest.importorskip("ada")
    pytest.importorskip("adacpp")
    from ada.cad import AdacppBackend
    from ada.cadit.step.read.stream_reader import stream_read_step

    src = tmp_path / "sphere.step"
    (ada.Assembly("a") / (ada.Part("p") / ada.PrimSphere("s", (0, 0, 0), 100.0))).to_stp(src)

    be = AdacppBackend()
    tris = 0
    for g in stream_read_step(src, local_pool=False, tolerant=True):
        gi = g.geometry.geometry if hasattr(g.geometry, "geometry") else g.geometry
        m = be.tessellate_stream([(str(g.id), gi)], pipeline="libtess2", deflection=2.0)
        tris += len(m.indices) // 3
    assert tris > 200, f"empty-bounds sphere should tessellate (was 0 before the fix), got {tris}"


def test_meshopt_simplify_lossless_drops_degenerate():
    """meshopt_simplify_mesh (border-locked, target_error 0 = lossless) drops degenerate
    triangles, never grows the mesh, and preserves the shape (bbox). This is the step2glb
    merge cleanup applied per unique mesh."""
    cad = pytest.importorskip("adacpp.cad")

    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], np.float32)
    idx = np.array([0, 1, 2, 1, 3, 2, 0, 0, 1], np.uint32)  # 2 real tris + 1 degenerate
    p2, i2 = cad.meshopt_simplify_mesh(pos.reshape(-1), idx, 1.0, 0.0)

    out_tris = np.asarray(i2).reshape(-1, 3)
    assert len(out_tris) <= 3  # never grows
    assert not any(t[0] == t[1] or t[1] == t[2] or t[0] == t[2] for t in out_tris)  # no degenerates
    out = np.asarray(p2, np.float32).reshape(-1, 3)
    assert np.allclose(pos.min(axis=0), out.min(axis=0), atol=1e-5)  # shape (bbox) preserved
    assert np.allclose(pos.max(axis=0), out.max(axis=0), atol=1e-5)
