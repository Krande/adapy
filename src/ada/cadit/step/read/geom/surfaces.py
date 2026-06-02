"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.geom.surfaces` (the pythonocc
CAD backend's home); this shim keeps the historical import path working without
itself importing OCC. See dap plan/v3 Phase 2 (STEP-IO relocation)."""

from ada.occ.step.geom.surfaces import (
    occ_face_to_ada_face,
    occ_shell_to_ada_faces,
    iter_faces,
    get_bsplinesurface_with_knots,
)

__all__ = [
    "occ_face_to_ada_face",
    "occ_shell_to_ada_faces",
    "iter_faces",
    "get_bsplinesurface_with_knots",
]
