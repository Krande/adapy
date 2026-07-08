"""The crows-nest distortion metric flags spike triangles (a vertex shot out past the body) but NOT
benign geometry — uniform faceting, deep thin extrusions, coarse curves — that a plain aspect/reach
test over-flags. Mirrors the frontend meshStats spike metric behind the "distorted" gallery walk.
"""

from __future__ import annotations

import numpy as np

from ada.occ.tessellating import accumulate_mesh_distortion, consume_mesh_distortion_stats


def _grid(n: int = 11):
    xs, ys = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    v = np.c_[xs.ravel(), ys.ravel(), np.zeros(n * n)]
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            i = r * n + c
            tris += [[i, i + 1, i + n + 1], [i, i + n + 1, i + n]]
    return v, np.asarray(tris)


def test_clean_grid_has_no_distortion():
    v, tris = _grid()
    accumulate_mesh_distortion(v, tris)
    assert consume_mesh_distortion_stats()["distorted_tris"] == 0


def test_spike_vertex_is_flagged():
    v, tris = _grid()
    v2 = np.vstack([v, [0.5, 0.5, 20.0]])  # a vertex shot 20 units off a unit-size sheet
    sp = len(v2) - 1
    tris2 = np.vstack([tris, [[0, 1, sp]]])  # a thin triangle reaching out to it
    accumulate_mesh_distortion(v2, tris2)
    assert consume_mesh_distortion_stats()["distorted_tris"] == 1


def test_deep_thin_extrusion_not_flagged():
    # A long thin box: side triangles are thin AND reach across the bbox, but no vertex is an
    # outlier (they're all on the body) — must NOT count as crows-nest distortion.
    x, y, z = 0.05, 0.05, 5.0
    corners = np.array(
        [[0, 0, 0], [x, 0, 0], [x, y, 0], [0, y, 0], [0, 0, z], [x, 0, z], [x, y, z], [0, y, z]], float
    )
    faces = [
        [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6],
        [3, 0, 4], [3, 4, 7], [0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7],
    ]
    accumulate_mesh_distortion(corners, np.asarray(faces))
    assert consume_mesh_distortion_stats()["distorted_tris"] == 0
