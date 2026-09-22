"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.geom.surfaces` (the pythonocc CAD
backend's home); this shim keeps the historical import path working.

It re-exports LAZILY: importing this module no longer imports OCC. See
`ada.cadit.step._lazy_reexport` for why that matters and why a plain re-export did not deliver it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.step._lazy_reexport import lazy_reexport

if TYPE_CHECKING:  # tooling still sees the names; nothing is imported at run time
    from ada.occ.step.geom.surfaces import (  # noqa: F401
        get_bsplinesurface_with_knots,
        iter_faces,
        occ_face_to_ada_face,
        occ_shell_to_ada_faces,
    )

__all__ = [
    "occ_face_to_ada_face",
    "occ_shell_to_ada_faces",
    "iter_faces",
    "get_bsplinesurface_with_knots",
]

__getattr__, __dir__ = lazy_reexport(__name__, "ada.occ.step.geom.surfaces", __all__)
