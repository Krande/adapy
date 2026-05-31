"""A sparse 3D occupancy grid keyed on sorted coordinate lists.

Pure Python (bisect over coordinate lists) — no CAD kernel involved. Used to
register geometry against the cell graph's structural grid lines.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Hashable, Tuple

GridIndex = Tuple[int, int, int]
GeomRef = Hashable  # int, str, ("beam", id), …


class GridIndexError(Exception):
    """Raised when a coordinate does not fall on a registered grid line."""


def _find_index(vals: list[float], v: float, tol: float) -> int:
    i = bisect.bisect_left(vals, v)
    if i < len(vals) and abs(vals[i] - v) <= tol:
        return i
    if i > 0 and abs(vals[i - 1] - v) <= tol:
        return i - 1
    raise GridIndexError(f"value {v} not on grid")


@dataclass
class CellGrid:
    x_list: list[float] = field(default_factory=list)
    y_list: list[float] = field(default_factory=list)
    z_list: list[float] = field(default_factory=list)

    # grid index -> set of geometry references occupying that node
    occupancy: dict[GridIndex, set[GeomRef]] = field(default_factory=lambda: defaultdict(set))

    def register(self, idx: GridIndex, geom: GeomRef) -> None:
        self.occupancy[idx].add(geom)

    def has_geometry(self, idx: GridIndex) -> bool:
        return bool(self.occupancy.get(idx))

    def iter_occupied(self):
        return ((idx, geoms) for idx, geoms in self.occupancy.items() if geoms)

    def index_of(self, x: float, y: float, z: float, tol: float = 1e-6) -> GridIndex:
        return (
            _find_index(self.x_list, x, tol),
            _find_index(self.y_list, y, tol),
            _find_index(self.z_list, z, tol),
        )

    def coord_from_index(self, idx: GridIndex) -> tuple[float, float, float]:
        ix, iy, iz = idx
        return (self.x_list[ix], self.y_list[iy], self.z_list[iz])
