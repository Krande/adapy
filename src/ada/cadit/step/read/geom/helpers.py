"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.geom.helpers` (the pythonocc
CAD backend's home); this shim keeps the historical import path working without
itself importing OCC. See dap plan/v3 Phase 2 (STEP-IO relocation)."""

from ada.occ.step.geom.helpers import (
    array1_to_list,
    array1_to_int_list,
    array2_to_point_list,
    array1_to_point_list,
)

__all__ = [
    "array1_to_list",
    "array1_to_int_list",
    "array2_to_point_list",
    "array1_to_point_list",
]
