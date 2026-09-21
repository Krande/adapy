"""DEPRECATED location. Moved to ``ada.visit.tessellate``.

Despite the old name, nothing here required OCC: BatchTessellator routes every call through
``ada.cad.active_backend()``, so it already ran on whichever kernel was active.

Kept as a re-export so out-of-tree consumers that import the old path keep working while
``ada.occ`` is being wound down. New code should import from ``ada.visit.tessellate``; this shim goes when
the package does.
"""

from __future__ import annotations

from ada.visit.tessellate import (  # noqa: F401
    BatchTessellator,
    LineMesh,
    TessellationFallbackError,
    TriangleMesh,
    accumulate_mesh_distortion,
    consume_mesh_distortion_stats,
    consume_tess_fallback_stats,
    shape_to_tri_mesh,
    tessellate_advanced_face,
    tessellate_edges,
    tessellate_shape,
)
