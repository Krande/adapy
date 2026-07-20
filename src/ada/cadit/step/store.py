"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.store` (the pythonocc
CAD backend's home); this shim keeps the historical import path working without
itself importing OCC. See the internal design notes Phase 2 (STEP-IO relocation)."""

from ada.occ.step.store import EntityProps, StepStore

__all__ = [
    "StepStore",
    "EntityProps",
]
