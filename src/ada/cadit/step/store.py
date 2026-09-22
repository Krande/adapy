"""Back-compat re-export. The OCC implementation moved to `ada.occ.step.store` (the pythonocc CAD
backend's home); this shim keeps the historical import path working.

It re-exports LAZILY: importing this module no longer imports OCC. See
`ada.cadit.step._lazy_reexport` for why that matters and why a plain re-export did not deliver it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.step._lazy_reexport import lazy_reexport

if TYPE_CHECKING:  # tooling still sees the names; nothing is imported at run time
    from ada.occ.step.store import EntityProps, StepStore  # noqa: F401

__all__ = [
    "StepStore",
    "EntityProps",
]

__getattr__, __dir__ = lazy_reexport(__name__, "ada.occ.step.store", __all__)
