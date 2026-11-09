"""A uniform grid over axis-aligned boxes: which boxes could overlap a given box.

The partitioning steps of the mesher ask "which other objects could touch this one" for every object. Asking it of
every other object makes the question quadratic in the size of the model -- a deck of a few thousand plates and
beams spent most of its meshing time there. The grid answers it from the cells a box covers, so each query looks at
its neighbours only. It is a pre-filter: it returns every box that could overlap, a caller still makes its own
exact test.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

#: A box covering more cells than this is kept out of the grid and offered to every query.
_MAX_CELLS_PER_BOX = 512


class AabbGrid:
    """Boxes ``lo[i]..hi[i]`` (``(n, 3)`` arrays) binned in a uniform grid.

    ``candidates(qlo, qhi)`` gives the indices (ascending) of every box that may overlap the box ``qlo..qhi``. A box
    with a non-finite bound is offered to every query.
    """

    def __init__(self, lo, hi, cell_size: float | None = None):
        lo = np.asarray(lo, dtype=float).reshape(-1, 3)
        hi = np.asarray(hi, dtype=float).reshape(-1, 3)
        self.n = len(lo)
        finite = np.all(np.isfinite(lo), axis=1) & np.all(np.isfinite(hi), axis=1)
        if cell_size is None:
            cell_size = self._default_cell_size(lo[finite], hi[finite])
        self.cell = float(cell_size)
        self._cells: defaultdict[tuple[int, int, int], list[int]] = defaultdict(list)
        always: list[int] = [int(i) for i in np.flatnonzero(~finite)]
        for i in np.flatnonzero(finite):
            rng = self._cell_range(lo[i], hi[i])
            if rng is None:
                always.append(int(i))
                continue
            (x0, x1), (y0, y1), (z0, z1) = rng
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    for z in range(z0, z1 + 1):
                        self._cells[(x, y, z)].append(int(i))
        self._always = np.array(sorted(always), dtype=np.int64)

    @staticmethod
    def _default_cell_size(lo: np.ndarray, hi: np.ndarray) -> float:
        if len(lo) == 0:
            return 1.0
        extent = np.max(hi - lo, axis=1)
        size = float(np.median(extent))
        if not math.isfinite(size) or size <= 0.0:
            span = float(np.max(np.max(hi, axis=0) - np.min(lo, axis=0)))
            size = span / max(1.0, len(lo) ** (1 / 3)) if span > 0 else 1.0
        return size

    def _cell_range(self, lo, hi):
        a = np.floor(np.asarray(lo, dtype=float) / self.cell).astype(np.int64)
        b = np.floor(np.asarray(hi, dtype=float) / self.cell).astype(np.int64)
        if int(np.prod(b - a + 1)) > _MAX_CELLS_PER_BOX:
            return None
        return tuple((int(a[k]), int(b[k])) for k in range(3))

    def candidates(self, qlo, qhi) -> np.ndarray:
        qlo = np.asarray(qlo, dtype=float)
        qhi = np.asarray(qhi, dtype=float)
        if not (np.all(np.isfinite(qlo)) and np.all(np.isfinite(qhi))):
            return np.arange(self.n, dtype=np.int64)
        a = np.floor(qlo / self.cell).astype(np.int64)
        b = np.floor(qhi / self.cell).astype(np.int64)
        if int(np.prod(b - a + 1)) > len(self._cells):
            # A query wider than the occupied grid: walk the occupied cells instead of the query's.
            found = [idx for key, idx in self._cells.items() if all(a[k] <= key[k] <= b[k] for k in range(3))]
        else:
            found = []
            cells = self._cells
            for x in range(int(a[0]), int(b[0]) + 1):
                for y in range(int(a[1]), int(b[1]) + 1):
                    for z in range(int(a[2]), int(b[2]) + 1):
                        idx = cells.get((x, y, z))
                        if idx is not None:
                            found.append(idx)
        parts = [np.asarray(f, dtype=np.int64) for f in found]
        if len(self._always):
            parts.append(self._always)
        if not parts:
            return np.empty(0, dtype=np.int64)
        return np.unique(np.concatenate(parts))
