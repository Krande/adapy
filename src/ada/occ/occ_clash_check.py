"""DEPRECATED location. Moved to ``ada.core.clash_distance``.

The distance itself comes from ``active_backend().distance()``; only the module's location
tied it to OCC. It sits beside ada.core.clash_check, which was already its only in-tree caller.

Kept as a re-export so out-of-tree consumers that import the old path keep working while
``ada.occ`` is being wound down. New code should import from ``ada.core.clash_distance``; this shim goes when
the package does.
"""

from __future__ import annotations

from ada.core.clash_distance import plates_min_distance  # noqa: F401
