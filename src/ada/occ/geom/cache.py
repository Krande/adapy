"""DEPRECATED location. Moved to ``ada.cad.shape_cache``.

The solid cache is keyed by GUID and rebuilds bodies through the ACTIVE backend, so it caches
adacpp shapes just as readily as OCC ones.

Kept as a re-export so out-of-tree consumers that import the old path keep working while
``ada.occ`` is being wound down. New code should import from ``ada.cad.shape_cache``; this shim goes when
the package does.
"""

from __future__ import annotations

from ada.cad.shape_cache import (  # noqa: F401
    cached_solid_by_guid,
    clear_all,
    get_shell_occ,
    get_solid_occ,
    invalidate,
)
