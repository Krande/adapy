"""DEPRECATED location. Moved to ``ada.cad.exceptions``.

These exception types describe a failure to BUILD geometry, which is a CAD-backend concern
rather than an OCC one -- the adacpp backend raises them too. They carry OCC in their names for
back-compat; renaming is a separate decision.

Kept as a re-export so out-of-tree consumers that import the old path keep working while
``ada.occ`` is being wound down. New code should import from ``ada.cad.exceptions``; this shim will go
when the package does.
"""

from __future__ import annotations

from ada.cad.exceptions import (  # noqa: F401
    UnableToCreateCurveOCCGeom,
    UnableToCreateSolidOCCGeom,
    UnableToCreateSurfaceOCCGeom,
    UnableToCreateTesselationFromSolidOCCGeom,
)
