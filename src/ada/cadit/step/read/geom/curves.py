"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.geom.curves` (the pythonocc CAD
backend's home); this shim keeps the historical import path working.

It re-exports LAZILY: importing this module no longer imports OCC. See
`ada.cadit.step._lazy_reexport` for why that matters and why a plain re-export did not deliver it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.step._lazy_reexport import lazy_reexport

if TYPE_CHECKING:  # tooling still sees the names; nothing is imported at run time
    from ada.occ.step.geom.curves import get_wires_from_face, process_wire  # noqa: F401

__all__ = [
    "get_wires_from_face",
    "process_wire",
]

__getattr__, __dir__ = lazy_reexport(__name__, "ada.occ.step.geom.curves", __all__)
