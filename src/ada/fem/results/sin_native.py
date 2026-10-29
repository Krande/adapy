"""adacpp.s SIN reader loops (``adacpp.fea.sin``), when the installed adacpp provides them.

The reader's per-record loops -- pointer-table validation, the per-case gathers, the ragged split, the
complex-case presentation and the record decode -- and the comparison's per-field statistics each ask
:func:`kernels` first and keep their numpy path when it returns None: an adacpp without ``fea.sin``, a
byte source without an in-memory buffer (the range-streamed backends), or ``ADA_SIN_NATIVE=0``. Both
paths return the same arrays, bit for bit.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

_UNSET = object()
_module: Any = _UNSET


def _load():
    global _module
    if _module is _UNSET:
        try:
            from adacpp import fea

            _module = getattr(fea, "sin", None)
        except ImportError:
            _module = None
    return _module


def kernels():
    """``adacpp.fea.sin``, or None when it is unavailable or ``ADA_SIN_NATIVE=0``."""
    if os.environ.get("ADA_SIN_NATIVE", "1") == "0":
        return None
    return _load()


def available() -> bool:
    return kernels() is not None


def buffer(source) -> np.ndarray | None:
    """``source``'s bytes as a uint8 view for the kernels, or None (no kernels, or no buffer to view)."""
    if kernels() is None:
        return None
    view = getattr(source, "u8_view", None)
    return view() if view is not None else None


def int64s(a) -> np.ndarray:
    return np.ascontiguousarray(a, dtype=np.int64)
