"""Lazy node->element adjacency (CSR) built from a substrate's connectivity.

Replaces the per-node ``Node._refs`` Python lists (the dominant memory cost after
coordinates) for the *element* subset of refs. Non-element refs (Beam/Csys/FemSet)
are not derivable from connectivity and are kept in a small side-table on the store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ada.api.mesh.store import MeshArrays


class CSRAdjacency:
    """Compressed sparse-row node->element incidence.

    ``incident(node_row)`` yields ``(ctype, elem_row)`` pairs. Built vectorized from
    every element block's connectivity in one pass.
    """

    def __init__(self, indptr: np.ndarray, block_ids: np.ndarray, elem_rows: np.ndarray, ctypes: list):
        self._indptr = indptr  # (n_nodes + 1,)
        self._block_ids = block_ids  # (nnz,) -> index into ctypes
        self._elem_rows = elem_rows  # (nnz,) -> row within that block
        self._ctypes = ctypes  # list[ElemTypeKey] parallel to block order

    @classmethod
    def build(cls, store: "MeshArrays") -> "CSRAdjacency":
        n = store.n_nodes
        ctypes = list(store.blocks.keys())
        if not ctypes:
            empty = np.zeros((0,), dtype=np.int64)
            return cls(np.zeros(n + 1, dtype=np.int64), empty, empty, [])

        node_rows = []  # which node each incidence belongs to
        blk_ids = []
        el_rows = []
        for bidx, ctype in enumerate(ctypes):
            blk = store.blocks[ctype]
            conn = blk.conn  # (m, k) node row indices
            m, k = conn.shape
            node_rows.append(conn.reshape(-1))
            blk_ids.append(np.full(m * k, bidx, dtype=np.int64))
            el_rows.append(np.repeat(np.arange(m, dtype=np.int64), k))

        node_rows = np.concatenate(node_rows)
        blk_ids = np.concatenate(blk_ids)
        el_rows = np.concatenate(el_rows)

        order = np.argsort(node_rows, kind="stable")
        node_rows = node_rows[order]
        blk_ids = blk_ids[order]
        el_rows = el_rows[order]

        counts = np.bincount(node_rows, minlength=n)
        indptr = np.zeros(n + 1, dtype=np.int64)
        np.cumsum(counts, out=indptr[1:])
        return cls(indptr, blk_ids, el_rows, ctypes)

    def degree(self, node_row: int) -> int:
        return int(self._indptr[node_row + 1] - self._indptr[node_row])

    def incident(self, node_row: int):
        lo, hi = int(self._indptr[node_row]), int(self._indptr[node_row + 1])
        for i in range(lo, hi):
            yield self._ctypes[int(self._block_ids[i])], int(self._elem_rows[i])
