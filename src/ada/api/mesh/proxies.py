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
from ada.fem.elements import Elem
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada.api.mesh.store import ElemArrayBlock, MeshArrays


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

    @property
    def refs(self) -> "RefsView":
        # Element refs come from the connectivity (CSR); Beam/Csys/FemSet from the
        # store's side-table. Replaces the per-node Python list.
        return RefsView(self._store, self._row)

    # x/y/z, r, units, parent, has_refs, p_roundoff, comparisons, hash,
    # __len__/__getitem__/__repr__ are all inherited from Node and work unchanged
    # because they go through the .p / .id / .refs accessors above.

    def __reduce__(self):
        # Pickle as a reference into the owning store rather than duplicating it.
        return (_rebuild_node_proxy, (self._store, self._row))


def _rebuild_node_proxy(store: "MeshArrays", row: int) -> NodeProxy:
    return store.node_proxy(row)


class RefsView:
    """``Node.refs`` backed by the substrate: CSR-derived element proxies chained
    with the side-table of non-element refs (Beam/Csys/FemSet)."""

    def __init__(self, store: "MeshArrays", row: int):
        self._store = store
        self._row = row

    def _elems(self):
        adj = self._store.node_to_elem()
        for ctype, erow in adj.incident(self._row):
            yield self._store.elem_proxy(ctype, erow)

    def __iter__(self):
        yield from self._elems()
        yield from self._store.extra_refs(self._row)

    def __len__(self) -> int:
        return self._store.node_to_elem().degree(self._row) + len(self._store.extra_refs(self._row))

    def __contains__(self, item) -> bool:
        return any(item == x for x in self)

    def __getitem__(self, i):
        return list(self)[i]

    def append(self, item) -> None:
        # Element membership is implied by connectivity -> no-op; only non-element
        # refs go to the side-table.
        if not isinstance(item, Elem):
            self._store.add_extra_ref(self._row, item)

    def remove(self, item) -> None:
        if not isinstance(item, Elem):
            self._store.remove_extra_ref(self._row, item)

    def copy(self) -> list:
        return list(self)


class NodeListView:
    """``Elem.nodes`` backed by a block connectivity row; mints node proxies lazily
    and writes through on item assignment."""

    def __init__(self, store: "MeshArrays", block: "ElemArrayBlock", row: int):
        self._store = store
        self._block = block
        self._row = row

    def __len__(self) -> int:
        return int(self._block.conn.shape[1])

    def __getitem__(self, i):
        conn_row = self._block.conn[self._row]
        if isinstance(i, slice):
            return [self._store.node_proxy(int(r)) for r in conn_row[i]]
        return self._store.node_proxy(int(conn_row[i]))

    def __setitem__(self, i, node) -> None:
        self._block.conn[self._row, i] = self._store.node_index(node.id)
        self._block._eid2row = self._block._eid2row  # unaffected
        self._store.conn_changed()

    def __iter__(self):
        for r in self._block.conn[self._row]:
            yield self._store.node_proxy(int(r))

    def index(self, node) -> int:
        target = self._store.node_index(node.id)
        row = self._block.conn[self._row]
        hits = np.nonzero(row == target)[0]
        if hits.size == 0:
            raise ValueError(f"{node!r} not in element")
        return int(hits[0])

    def __eq__(self, other) -> bool:
        return list(self) == list(other)


class ElemProxy(Elem):
    """An ``Elem`` backed by a row of an :class:`ElemArrayBlock`."""

    def __init__(self, store: "MeshArrays", ctype, row: int, parent=None):
        self._store = store
        self._ctype = ctype
        self._row = int(row)
        self._parent = parent
        self._shape = None
        self._refs = []
        self._mass_props = None
        self._formulation_override = None

    @property
    def _block(self) -> "ElemArrayBlock":
        return self._store.blocks[self._ctype]

    @property
    def id(self) -> int:
        return int(self._block.el_ids[self._row])

    @id.setter
    def id(self, value):
        self._block.el_ids[self._row] = int(value)
        self._block._eid2row = None

    @property
    def type(self):
        return self._ctype

    @property
    def nodes(self) -> NodeListView:
        return NodeListView(self._store, self._block, self._row)

    @property
    def fem_sec(self):
        secs = self._block.fem_secs
        return secs[self._row] if secs is not None else None

    @fem_sec.setter
    def fem_sec(self, value):
        if self._block.fem_secs is None:
            self._block.fem_secs = [None] * len(self._block)
        self._block.fem_secs[self._row] = value

    @property
    def elset(self):
        els = self._block.elsets
        return els[self._row] if els is not None else None

    @elset.setter
    def elset(self, value):
        if self._block.elsets is None:
            self._block.elsets = [None] * len(self._block)
        self._block.elsets[self._row] = value

    @property
    def eccentricity(self):
        return self._block.ecc.get(self._row)

    @eccentricity.setter
    def eccentricity(self, value):
        if value is None:
            self._block.ecc.pop(self._row, None)
        else:
            self._block.ecc[self._row] = value

    @property
    def hinge_prop(self):
        return self._block.hinge.get(self._row)

    @hinge_prop.setter
    def hinge_prop(self, value):
        if value is None:
            self._block.hinge.pop(self._row, None)
        else:
            self._block.hinge[self._row] = value

    @property
    def shape(self):
        from ada.fem.shapes import ElemShape

        if self._shape is None:
            self._shape = ElemShape(self.type, list(self.nodes))
        return self._shape

    def updating_nodes(self, old_node: Node, new_node: Node) -> None:
        view = self.nodes
        view[view.index(old_node)] = new_node
        self._shape = None

    def replace_node_with_other_node(self, old_node: Node, new_node: Node):
        view = self.nodes
        view[view.index(old_node)] = new_node
        self._shape = None

    @property
    def refs(self) -> "ElemRefsView":
        return ElemRefsView(self._store, self._ctype, self._row)

    def add_obj_to_refs(self, item) -> None:
        self._store.add_elem_ref(self._ctype, self._row, item)

    def remove_obj_from_refs(self, item) -> None:
        self._store.remove_elem_ref(self._ctype, self._row, item)

    def __repr__(self):
        return f'ElemProxy(ID: {self.id}, Type: {self.type}, NodeIds: "{[n.id for n in self.nodes]}")'

    def __reduce__(self):
        return (_rebuild_elem_proxy, (self._store, self._ctype, self._row))


class ElemRefsView:
    """``Elem.refs`` backed by the store's element-refs side-table (FemSet/Beam/...)."""

    def __init__(self, store: "MeshArrays", ctype, row: int):
        self._store = store
        self._ctype = ctype
        self._row = row

    def __iter__(self):
        return iter(self._store.elem_refs(self._ctype, self._row))

    def __len__(self) -> int:
        return len(self._store.elem_refs(self._ctype, self._row))

    def __contains__(self, item) -> bool:
        return item in self._store.elem_refs(self._ctype, self._row)

    def __getitem__(self, i):
        return self._store.elem_refs(self._ctype, self._row)[i]

    def append(self, item) -> None:
        self._store.add_elem_ref(self._ctype, self._row, item)

    def remove(self, item) -> None:
        self._store.remove_elem_ref(self._ctype, self._row, item)

    def copy(self) -> list:
        return list(self)


def _rebuild_elem_proxy(store: "MeshArrays", ctype, row: int) -> ElemProxy:
    return store.elem_proxy(ctype, row)
