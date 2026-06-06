"""Lazy proxies over :class:`~ada.api.mesh.store.MeshArrays`.

``NodeProxy`` is a :class:`~ada.api.nodes.Node` subclass (so ``isinstance(x, Node)``
and value-equality keep working) that holds only ``(store, row)`` and reads/writes
its coordinates and id straight through to the substrate arrays. No per-node Point or
coordinate copy is stored — ``.p`` mints an interned ``Point`` on access.

Identity: the store hands out the *same* proxy for a given row while it is alive (a
``WeakValueDictionary`` cache), so ``from_id(5) is from_id(5)`` holds within a live
window. Equality/hash/ordering are value-based (``(*p, id)``), matching ``Node``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.api.nodes import Node
from ada.base.units import Units
from ada.config import Config
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada.api.mesh.store import MeshArrays


class NodeProxy(Node):
    """A ``Node`` backed by a row of a :class:`MeshArrays` substrate."""

    def __init__(self, store: "MeshArrays", row: int, parent=None, units=Units.M):
        # Deliberately do NOT call Node.__init__ — there is no per-node coordinate
        # or id data; those live in the substrate arrays.
        self._store = store
        self._row = int(row)
        self._r = None
        self._parent = parent
        self._units = units
        self._refs = []
        self._precision = Config().general_precision

    # ── coordinates / id read+write straight through to the arrays ───────
    @property
    def p(self) -> Point:
        return Point(self._store.coords[self._row])

    @p.setter
    def p(self, value):
        self._store.set_node_coord(self._row, np.asarray(value, dtype=float))

    @property
    def id(self) -> int:
        return int(self._store.node_ids[self._row])

    @id.setter
    def id(self, value: int):
        self._store.set_node_id(self._row, int(value))

    @property
    def row(self) -> int:
        return self._row

    @property
    def store(self) -> "MeshArrays":
        return self._store

    # x/y/z, r, units, parent, refs, has_refs, p_roundoff, comparisons, hash,
    # __len__/__getitem__/__repr__ are all inherited from Node and work unchanged
    # because they go through the .p / .id / ._refs accessors above.

    def __reduce__(self):
        # Pickle as a reference into the owning store rather than duplicating it.
        return (_rebuild_node_proxy, (self._store, self._row))


def _rebuild_node_proxy(store: "MeshArrays", row: int) -> NodeProxy:
    return store.node_proxy(row)
