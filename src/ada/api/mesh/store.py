"""``MeshArrays`` — the packed-numpy substrate that backs the FEM mesh.

A single per-FEM object owns the raw numeric data:

* ``coords``   ``float64 (n, 3)``  — the single source of truth for ``Node.p``
* ``node_ids`` ``int64   (n,)``    — source-file node labels
* ``blocks``   ``{ElemTypeKey -> ElemArrayBlock}`` where each block holds an
  ``int32 (m, k)`` connectivity array of **row indices into ``coords``** (not node
  IDs) plus an ``int64 (m,)`` element-id array.

Storing connectivity as row indices (not IDs) is the linchpin: renumbering nodes is
then O(1) on ``node_ids`` alone (``conn`` is untouched), and the results-side
``FemNodes``/``ElementBlock`` become zero-copy views.

Derived structures (id->index map, voxel grid, bbox, node proxies) are lazy and
invalidated on the relevant edit.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Iterable

import numpy as np

if TYPE_CHECKING:
    from ada.api.mesh.proxies import NodeProxy
    from ada.api.nodes import Node
    from ada.fem.results.common import FemNodes


class ElemArrayBlock:
    """One element type's connectivity, packed.

    ``conn`` rows are **node row-indices** into the owning store's ``coords``.
    """

    __slots__ = ("ctype", "conn", "el_ids", "fem_secs", "elsets", "ecc", "hinge", "_eid2row")

    def __init__(self, ctype, conn: np.ndarray, el_ids: np.ndarray, fem_secs=None, elsets=None):
        self.ctype = ctype
        self.conn = np.ascontiguousarray(conn, dtype=np.int32)
        self.el_ids = np.ascontiguousarray(el_ids, dtype=np.int64)
        if self.conn.ndim != 2 or self.conn.shape[0] != self.el_ids.shape[0]:
            raise ValueError("conn must be (m,k) and match el_ids length")
        # Per-element attributes. Shared/heavy ones (FemSection, FemSet) are stored
        # as parallel reference lists (a handful of distinct objects shared across
        # rows); rare ones (eccentricity, hinge) as sparse row-keyed dicts.
        self.fem_secs: list | None = fem_secs
        self.elsets: list | None = elsets
        self.ecc: dict[int, object] = {}
        self.hinge: dict[int, object] = {}
        self._eid2row: dict[int, int] | None = None

    @property
    def nodes_per_elem(self) -> int:
        return self.conn.shape[1]

    @property
    def eid2row(self) -> dict[int, int]:
        if self._eid2row is None:
            self._eid2row = {int(e): i for i, e in enumerate(self.el_ids)}
        return self._eid2row

    def __len__(self) -> int:
        return self.conn.shape[0]


class MeshArrays:
    def __init__(self, coords: np.ndarray, node_ids: np.ndarray, blocks: dict | None = None):
        self.coords = np.ascontiguousarray(coords, dtype=np.float64)
        self.node_ids = np.ascontiguousarray(node_ids, dtype=np.int64)
        if self.coords.ndim != 2 or self.coords.shape[1] != 3:
            raise ValueError("coords must be (n, 3)")
        if self.node_ids.shape[0] != self.coords.shape[0]:
            raise ValueError("node_ids length must match coords")
        self.blocks: dict = dict(blocks) if blocks else {}

        self._id2idx: dict[int, int] | None = None
        self._bbox = None
        # Identity: from_id(x) returns the same proxy while it's alive (held by an
        # element list or a transient caller). Weak so untouched proxies are freed.
        self._proxy_cache: "weakref.WeakValueDictionary[int, NodeProxy]" = weakref.WeakValueDictionary()
        self._elem_proxy_cache: "weakref.WeakValueDictionary[tuple, object]" = weakref.WeakValueDictionary()
        # Node->element adjacency (lazy; rebuilt when connectivity changes).
        self._adjacency = None
        self._adj_epoch = 0
        # Non-element refs (Beam/Csys/FemSet) that aren't derivable from
        # connectivity, keyed by node row. Element refs come from the CSR.
        self._extra_refs: dict[int, list] = {}
        # Refs that point AT an element (FemSet/Beam/Plate...), keyed by
        # (ctype, row). Connectivity edits don't move element rows, so these stay
        # valid across node remove / renumber.
        self._elem_refs: dict[tuple, list] = {}

    # ── node id <-> row index ────────────────────────────────────────────
    @property
    def n_nodes(self) -> int:
        return self.coords.shape[0]

    @property
    def id2idx(self) -> dict[int, int]:
        if self._id2idx is None:
            self._id2idx = {int(nid): i for i, nid in enumerate(self.node_ids)}
        return self._id2idx

    def node_index(self, nid: int) -> int:
        try:
            return self.id2idx[int(nid)]
        except KeyError:
            raise ValueError(f'The node id "{nid}" is not found')

    def node_id(self, row: int) -> int:
        return int(self.node_ids[row])

    def has_node(self, nid: int) -> bool:
        return int(nid) in self.id2idx

    # ── proxy minting (identity-stable while alive) ──────────────────────
    def node_proxy(self, row: int) -> "NodeProxy":
        row = int(row)
        cached = self._proxy_cache.get(row)
        if cached is not None:
            return cached
        from ada.api.mesh.proxies import NodeProxy

        proxy = NodeProxy(self, row)
        self._proxy_cache[row] = proxy
        return proxy

    def node_proxy_by_id(self, nid: int) -> "NodeProxy":
        return self.node_proxy(self.node_index(nid))

    def iter_node_proxies(self) -> Iterable["NodeProxy"]:
        for row in range(self.n_nodes):
            yield self.node_proxy(row)

    # ── single-element writes (write-through) ────────────────────────────
    def set_node_coord(self, row: int, p) -> None:
        self.coords[row] = np.asarray(p, dtype=np.float64)[:3]
        self._bbox = None

    def set_node_id(self, row: int, new_id: int) -> None:
        self.node_ids[row] = int(new_id)
        self._id2idx = None
        # the cached proxy (if any) keeps the same row, so identity is preserved

    # ── bulk vectorized ops ──────────────────────────────────────────────
    def translate(self, vec) -> None:
        self.coords += np.asarray(vec, dtype=np.float64)
        self._bbox = None

    def rotate(self, rot_mat: np.ndarray, origin) -> None:
        o = np.asarray(origin, dtype=np.float64)
        self.coords[:] = (self.coords - o) @ np.asarray(rot_mat, dtype=np.float64).T + o
        self._bbox = None

    def scale(self, factor: float) -> None:
        self.coords *= float(factor)
        self._bbox = None

    def renumber_nodes(self, start_id: int = 1, renumber_map: dict[int, int] | None = None) -> None:
        """Renumber node IDs. Connectivity is index-based, so it is untouched —
        this is O(n) on ``node_ids`` alone (vs the per-object id-setter loop)."""
        if renumber_map is not None:
            self.node_ids = np.array([int(renumber_map[int(x)]) for x in self.node_ids], dtype=np.int64)
        else:
            # smallest old id -> start_id, in old-id order (matches the object path)
            order = np.argsort(self.node_ids, kind="stable")
            new = np.empty_like(self.node_ids)
            new[order] = np.arange(start_id, start_id + self.node_ids.shape[0], dtype=np.int64)
            self.node_ids = new
        self._id2idx = None

    def renumber_elems(self, start_id: int = 1, renumber_map: dict[int, int] | None = None) -> None:
        """Renumber element IDs across all blocks (connectivity untouched)."""
        if renumber_map is not None:
            for blk in self.blocks.values():
                blk.el_ids = np.array([int(renumber_map[int(x)]) for x in blk.el_ids], dtype=np.int64)
                blk._eid2row = None
        else:
            nxt = start_id
            # number in (block-insertion, id) order to be deterministic
            for blk in self.blocks.values():
                order = np.argsort(blk.el_ids, kind="stable")
                new = np.empty_like(blk.el_ids)
                new[order] = np.arange(nxt, nxt + blk.el_ids.shape[0], dtype=np.int64)
                blk.el_ids = new
                blk._eid2row = None
                nxt += blk.el_ids.shape[0]

    # ── queries ──────────────────────────────────────────────────────────
    def bbox(self):
        if self._bbox is None:
            if self.n_nodes == 0:
                raise ValueError("No Nodes are found")
            mn = self.coords.min(axis=0)
            mx = self.coords.max(axis=0)
            self._bbox = ((mn[0], mx[0]), (mn[1], mx[1]), (mn[2], mx[2]))
        return self._bbox

    def rows_in_box(self, vol_min, vol_max) -> np.ndarray:
        """Row indices whose coords lie within the [vol_min, vol_max] box (vectorized)."""
        lo = np.asarray(vol_min, dtype=np.float64)
        hi = np.asarray(vol_max, dtype=np.float64)
        mask = np.all((self.coords >= lo) & (self.coords <= hi), axis=1)
        return np.nonzero(mask)[0]

    # ── construction ─────────────────────────────────────────────────────
    @classmethod
    def from_node_rows(cls, node_rows: np.ndarray, blocks: dict | None = None) -> "MeshArrays":
        """``node_rows`` is ``(n, 4)`` of ``[id, x, y, z]``."""
        arr = np.asarray(node_rows, dtype=np.float64)
        return cls(arr[:, 1:4], arr[:, 0].astype(np.int64), blocks)

    @classmethod
    def from_nodes(cls, nodes: Iterable["Node"]) -> "MeshArrays":
        nodes = list(nodes)
        coords = np.array([n.p for n in nodes], dtype=np.float64) if nodes else np.zeros((0, 3))
        node_ids = np.array([n.id for n in nodes], dtype=np.int64) if nodes else np.zeros((0,), dtype=np.int64)
        return cls(coords, node_ids)

    def add_elem_block_from_id_conn(self, ctype, el_ids, id_conn: np.ndarray) -> ElemArrayBlock:
        """Add a block whose connectivity is given as node *IDs*; converts to row indices
        in bulk via ``searchsorted`` (vectorized — the reader hot path)."""
        id_conn = np.asarray(id_conn)
        flat = id_conn.reshape(-1)
        order = np.argsort(self.node_ids, kind="stable")
        sorted_ids = self.node_ids[order]
        pos = np.searchsorted(sorted_ids, flat)
        # guard: every referenced id must exist
        if np.any(pos >= sorted_ids.size) or np.any(sorted_ids[np.clip(pos, 0, sorted_ids.size - 1)] != flat):
            missing = flat[(pos >= sorted_ids.size) | (sorted_ids[np.clip(pos, 0, sorted_ids.size - 1)] != flat)]
            raise ValueError(f"element references unknown node id(s): {missing[:5].tolist()}")
        conn = order[pos].astype(np.int32).reshape(id_conn.shape)
        blk = ElemArrayBlock(ctype, conn, np.asarray(el_ids, dtype=np.int64))
        self.blocks[ctype] = blk
        return blk

    @classmethod
    def from_fem(cls, fem) -> "MeshArrays":
        """Build a substrate from an object-model ``FEM`` (the bridge a migrated
        reader would produce directly). Element groups with a uniform node count
        per type become blocks; ragged/non-structural groups are skipped."""
        store = cls.from_nodes(list(fem.nodes))
        for el_type, group in fem.elements.group_by_type():
            elems = list(group)
            try:
                id_conn = np.array([[n.id for n in e.nodes] for e in elems], dtype=np.int64)
            except ValueError:
                continue  # mixed node counts within the group — skip for now
            if id_conn.ndim != 2:
                continue
            el_ids = np.array([e.id for e in elems], dtype=np.int64)
            blk = store.add_elem_block_from_id_conn(el_type, el_ids, id_conn)
            # capture per-element attributes (shared ones as parallel lists,
            # rare ones as sparse row dicts)
            if any(getattr(e, "fem_sec", None) is not None for e in elems):
                blk.fem_secs = [getattr(e, "fem_sec", None) for e in elems]
            if any(getattr(e, "elset", None) is not None for e in elems):
                blk.elsets = [getattr(e, "elset", None) for e in elems]
            for i, e in enumerate(elems):
                if getattr(e, "eccentricity", None) is not None:
                    blk.ecc[i] = e.eccentricity
                if getattr(e, "hinge_prop", None) is not None:
                    blk.hinge[i] = e.hinge_prop
        return store

    # ── structural edits (atomic across nodes + connectivity) ────────────
    def add_node(self, p, nid: int | None = None) -> int:
        """Append a node; returns its row. Connectivity (row-indexed) is unaffected."""
        p = np.asarray(p, dtype=np.float64).reshape(3)
        self.coords = np.vstack([self.coords, p]) if self.n_nodes else p.reshape(1, 3).copy()
        if nid is None:
            nid = int(self.node_ids.max()) + 1 if self.node_ids.size else 1
        self.node_ids = np.append(self.node_ids, np.int64(nid))
        self._id2idx = None
        self._bbox = None
        return self.coords.shape[0] - 1

    def remove_nodes(self, rows) -> None:
        """Remove node rows and remap every block's connectivity in one shot.

        Removed rows must be unreferenced by any element (the caller — e.g.
        ``merge_coincident`` after ``replace_node`` — guarantees this); otherwise
        the dangling reference becomes ``-1``.
        """
        rows = np.atleast_1d(np.asarray(list(rows), dtype=np.int64))
        if rows.size == 0:
            return
        keep = np.ones(self.n_nodes, dtype=bool)
        keep[rows] = False
        old2new = np.full(self.n_nodes, -1, dtype=np.int32)
        old2new[keep] = np.arange(int(keep.sum()), dtype=np.int32)
        self.coords = self.coords[keep]
        self.node_ids = self.node_ids[keep]
        for blk in self.blocks.values():
            blk.conn = old2new[blk.conn].astype(np.int32)
            blk._eid2row = None
        self._id2idx = None
        self._bbox = None
        self._adjacency = None
        self._adj_epoch += 1
        # rows shifted -> any cached proxies now point at the wrong row.
        self._proxy_cache.clear()
        self._extra_refs = {}

    def conn_changed(self) -> None:
        """Signal that a block's connectivity was edited (invalidates adjacency)."""
        self._adjacency = None
        self._adj_epoch += 1

    # ── element access ───────────────────────────────────────────────────
    def elem_loc(self, eid: int):
        """Return ``(ctype, row)`` for an element id, or raise ValueError."""
        for ctype, blk in self.blocks.items():
            row = blk.eid2row.get(int(eid))
            if row is not None:
                return ctype, row
        raise ValueError(f'The elem id "{eid}" is not found')

    def n_elems(self) -> int:
        return sum(len(b) for b in self.blocks.values())

    def __repr__(self) -> str:
        by_type = ", ".join(f"{ctype}: {len(blk)}" for ctype, blk in self.blocks.items())
        return f"MeshArrays(nodes: {self.n_nodes}, elems: {self.n_elems()}{', ' + by_type if by_type else ''})"

    def elem_proxy(self, ctype, row: int):
        key = (ctype, int(row))
        cached = self._elem_proxy_cache.get(key)
        if cached is not None:
            return cached
        from ada.api.mesh.proxies import ElemProxy

        proxy = ElemProxy(self, ctype, int(row))
        self._elem_proxy_cache[key] = proxy
        return proxy

    def elem_proxy_by_id(self, eid: int):
        ctype, row = self.elem_loc(eid)
        return self.elem_proxy(ctype, row)

    def iter_elem_proxies(self):
        for ctype, blk in self.blocks.items():
            for row in range(len(blk)):
                yield self.elem_proxy(ctype, row)

    # ── node->element adjacency + refs side-table ────────────────────────
    def node_to_elem(self):
        from ada.api.mesh.adjacency import CSRAdjacency

        if self._adjacency is None:
            self._adjacency = CSRAdjacency.build(self)
        return self._adjacency

    def add_extra_ref(self, row: int, item) -> None:
        lst = self._extra_refs.setdefault(int(row), [])
        if item not in lst:
            lst.append(item)

    def remove_extra_ref(self, row: int, item) -> None:
        lst = self._extra_refs.get(int(row))
        if lst and item in lst:
            lst.remove(item)

    def extra_refs(self, row: int) -> list:
        return self._extra_refs.get(int(row), [])

    # element-level refs (things that reference an element, e.g. FemSet)
    def add_elem_ref(self, ctype, row: int, item) -> None:
        lst = self._elem_refs.setdefault((ctype, int(row)), [])
        if item not in lst:
            lst.append(item)

    def remove_elem_ref(self, ctype, row: int, item) -> None:
        lst = self._elem_refs.get((ctype, int(row)))
        if lst and item in lst:
            lst.remove(item)

    def elem_refs(self, ctype, row: int) -> list:
        return self._elem_refs.get((ctype, int(row)), [])

    # ── results-side bridge (zero-copy views) ────────────────────────────
    def to_fem_nodes(self) -> "FemNodes":
        from ada.fem.results.common import FemNodes

        return FemNodes(self.coords, self.node_ids)
