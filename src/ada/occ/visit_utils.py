"""DEPRECATED location. Moved to ``ada.visit.mesh_utils``.

A visit-layer helper that happened to live under ``ada.occ``; it builds ObjectMesh from a
tessellation and never touched OCC directly.

Kept as a re-export so out-of-tree consumers that import the old path keep working while
``ada.occ`` is being wound down. New code should import from ``ada.visit.mesh_utils``; this shim goes when
the package does.
"""

from __future__ import annotations

from ada.visit.mesh_utils import occ_geom_to_poly_mesh  # noqa: F401
