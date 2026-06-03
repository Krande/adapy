"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.writer` (the pythonocc
CAD backend's home); this shim keeps the historical import path working without
itself importing OCC. See dap plan/v3 Phase 2 (STEP-IO relocation)."""

from ada.occ.step.writer import StepSchema, StepWriter

__all__ = [
    "StepSchema",
    "StepWriter",
]
