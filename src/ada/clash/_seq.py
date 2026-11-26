"""Reading point lists that may be numpy arrays, lists, tuples or None. Stdlib only.

``value or ()`` is the natural way to read an optional list, and it raises on a numpy array of more
than one element ("the truth value of an array is ambiguous"). The engine (``adacpp.clash``) returns
its patch points and near points as arrays, a run's JSON as lists; the clash path reads both.
"""

from __future__ import annotations

from typing import Any

__all__ = ["as_items"]


def as_items(value: Any) -> Any:
    """``value`` as something to iterate: ``None`` is empty, an array (anything with ``tolist``) its
    nested lists, anything else itself."""
    if value is None:
        return ()
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return tolist()
    return value
