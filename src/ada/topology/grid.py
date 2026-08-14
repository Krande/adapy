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

    @classmethod
    def from_bounds(
        cls,
        p_min: tuple[float, float, float],
        p_max: tuple[float, float, float],
        spacing: float,
    ) -> "CellGrid":
        """Uniform lattice between ``p_min`` and ``p_max`` with the given node
        ``spacing`` (the max bound is always included as the last grid line)."""
        if spacing <= 0:
            raise ValueError(f"spacing must be positive, got {spacing}")

        def _axis(lo: float, hi: float) -> list[float]:
            if hi < lo:
                raise ValueError(f"invalid bounds: max {hi} < min {lo}")
            n = int(round((hi - lo) / spacing))
            vals = [lo + i * spacing for i in range(n + 1)]
            if abs(vals[-1] - hi) > spacing * 1e-6:
                vals.append(hi)
            return vals

        return cls(
            x_list=_axis(p_min[0], p_max[0]),
            y_list=_axis(p_min[1], p_max[1]),
            z_list=_axis(p_min[2], p_max[2]),
        )

    def index_of(self, x: float, y: float, z: float, tol: float = 1e-6) -> GridIndex:
        return (
            _find_index(self.x_list, x, tol),
            _find_index(self.y_list, y, tol),
            _find_index(self.z_list, z, tol),
        )

    def coord_from_index(self, idx: GridIndex) -> tuple[float, float, float]:
        ix, iy, iz = idx
        return (self.x_list[ix], self.y_list[iy], self.z_list[iz])
