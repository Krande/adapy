"""Readers turning a DEXPI file into the neutral :class:`~ada.cadit.dexpi.model.DexpiDocument`.

One reader per serialization, both converging on the same model.
:func:`~ada.cadit.dexpi.store.read_dexpi` picks between them from the root tag.
"""

from __future__ import annotations

from .read_dexpi20 import read_dexpi20
from .read_proteus import read_proteus

__all__ = ["read_dexpi20", "read_proteus"]
