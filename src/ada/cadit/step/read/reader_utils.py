"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.reader_utils` (the pythonocc
CAD backend's home); this shim keeps the historical import path working without
itself importing OCC. See dap plan/v3 Phase 2 (STEP-IO relocation)."""

from ada.occ.step.reader_utils import (
    read_step_file_with_names_colors,
    node_to_step_shape,
    iter_children,
    set_color,
    set_color_adacpp,
)

__all__ = [
    "read_step_file_with_names_colors",
    "node_to_step_shape",
    "iter_children",
    "set_color",
    "set_color_adacpp",
]
