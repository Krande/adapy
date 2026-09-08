"""DEXPI P&ID exchange: the neutral model, and the readers and writers around it.

DEXPI 2.0.0 replaced the serialization without changing the plant semantics, so adapy reads and
writes both wire formats -- Proteus XML (1.3/1.4) and DEXPI XML (2.0.0) -- behind one in-memory
model, dispatched on the root tag by :func:`~ada.cadit.dexpi.flavour.sniff_flavour`.

Nothing here is imported by ``ada/__init__.py``. ``import ada`` is eager and must stay cheap and
pyodide-friendly, so the vendored class table loads lazily on first lookup and the readers are
imported by the caller that wants them.
"""

from __future__ import annotations

from .canonical import canonicalize, graph_signature
from .flavour import DexpiFlavour, sniff_flavour
from .model import (
    DexpiAssociation,
    DexpiAttribute,
    DexpiConnection,
    DexpiDocument,
    DexpiHeader,
    DexpiItem,
    DexpiNode,
    DexpiPlacement,
    ItemKind,
    classify,
)
from .validate import validate_document

__all__ = [
    "DexpiAssociation",
    "DexpiAttribute",
    "DexpiConnection",
    "DexpiDocument",
    "DexpiFlavour",
    "DexpiHeader",
    "DexpiItem",
    "DexpiNode",
    "DexpiPlacement",
    "ItemKind",
    "canonicalize",
    "classify",
    "graph_signature",
    "sniff_flavour",
    "validate_document",
]
