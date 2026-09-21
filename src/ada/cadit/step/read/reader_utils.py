"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.reader_utils` (the pythonocc CAD
backend's home); this shim keeps the historical import path working.

It re-exports LAZILY: importing this module no longer imports OCC. See
`ada.cadit.step._lazy_reexport` for why that matters and why a plain re-export did not deliver it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.step._lazy_reexport import lazy_reexport

if TYPE_CHECKING:  # tooling still sees the names; nothing is imported at run time
    from ada.occ.step.reader_utils import (  # noqa: F401
        iter_children,
        node_to_step_shape,
        read_step_file_with_names_colors,
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

__getattr__, __dir__ = lazy_reexport(__name__, "ada.occ.step.reader_utils", __all__)
