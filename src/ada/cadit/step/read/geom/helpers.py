"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.geom.helpers` (the pythonocc CAD
backend's home); this shim keeps the historical import path working.

It re-exports LAZILY: importing this module no longer imports OCC. See
`ada.cadit.step._lazy_reexport` for why that matters and why a plain re-export did not deliver it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.step._lazy_reexport import lazy_reexport

if TYPE_CHECKING:  # tooling still sees the names; nothing is imported at run time
    from ada.occ.step.geom.helpers import (  # noqa: F401
        array1_to_int_list,
        array1_to_list,
        array1_to_point_list,
        array2_to_point_list,
    )

__all__ = [
    "array1_to_list",
    "array1_to_int_list",
    "array2_to_point_list",
    "array1_to_point_list",
]

__getattr__, __dir__ = lazy_reexport(__name__, "ada.occ.step.geom.helpers", __all__)
