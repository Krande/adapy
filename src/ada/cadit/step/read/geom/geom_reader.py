"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.geom.geom_reader` (the pythonocc
CAD backend's home); this shim keeps the historical import path working without
itself importing OCC. See the internal design notes Phase 2 (STEP-IO relocation)."""

from ada.occ.step.geom.geom_reader import import_geometry_from_step_geom

__all__ = [
    "import_geometry_from_step_geom",
]
