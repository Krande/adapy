"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.geom.curves` (the pythonocc
CAD backend's home); this shim keeps the historical import path working without
itself importing OCC. See the internal design notes Phase 2 (STEP-IO relocation)."""

from ada.occ.step.geom.curves import get_wires_from_face, process_wire

__all__ = [
    "get_wires_from_face",
    "process_wire",
]
