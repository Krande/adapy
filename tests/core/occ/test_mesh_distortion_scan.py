"""The per-conversion distortion tally flags degenerate/sliver triangles (the audit "distorted
tris" flag) and the fallback counter tallies libtess2->OCC fallbacks."""

from __future__ import annotations

import numpy as np


def test_distortion_counts_slivers_only():
    from ada.occ.tessellating import (
        accumulate_mesh_distortion,
        consume_mesh_distortion_stats,
    )

    consume_mesh_distortion_stats()  # reset
    pos = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],  # healthy right triangle
            [0, 0, 0],
            [10, 0, 0],
            [5, 1e-4, 0],  # extreme sliver
            [0, 0, 0],
            [1, 0, 0],
            [1, 0, 0],  # degenerate (repeated vertex)
        ],
        float,
    )
    idx = np.array([[0, 1, 2], [3, 4, 5], [6, 7, 8]], int)
    accumulate_mesh_distortion(pos, idx)
    s = consume_mesh_distortion_stats()
    assert s["n_tris"] == 3
    # The flag targets visible crows-nest spikes: a distorted triangle must touch a spatial outlier
    # vertex (here the sliver reaches out to the far (10,0,0) corner). The in-place degenerate
    # (repeated-vertex, zero-area) triangle is invisible and — like the clean/thin cases in
    # test_mesh_distortion.py — is deliberately NOT flagged, so it would only pollute the metric.
    assert s["distorted_tris"] == 1  # the sliver only
    assert consume_mesh_distortion_stats()["n_tris"] == 0  # reset on read


def test_fallback_counter_reset_on_consume():
    from ada.occ.tessellating import _record_tess_fallback, consume_tess_fallback_stats

    consume_tess_fallback_stats()  # reset
    _record_tess_fallback("empty mesh (geom type not NGEOM-serializable)", "FacetedBrep")
    _record_tess_fallback("active backend has no tessellate_stream", "Box")
    s = consume_tess_fallback_stats()
    assert s["count"] == 2
    assert s["geoms"]["FacetedBrep"] == 1
    assert consume_tess_fallback_stats()["count"] == 0
