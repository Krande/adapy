"""Routing-grid construction and equipment occupancy for the procedural compile.

Factored out of :mod:`ada.topo_model.compile` so the compile narrative reads as
orchestration: build a lattice over the spaces (:func:`routing_grid`), stamp the
equipment bodies onto it as no-go volumes (:func:`occupy_equipment`), and land
system ports on it (:func:`augment_grid_with_ports`). The rotated-equipment-box
maths that occupancy and clash-flagging both need lives in one place —
:class:`OrientedBox`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import ada
from ada.topology import CellGrid
from ada.topology.entities import TopoSpace

__all__ = [
    "OrientedBox",
    "augment_grid_with_ports",
    "occupy_equipment",
    "routing_grid",
]


# --------------------------------------------------------------------------- #
# Oriented equipment body — shared by occupancy and clash-flagging
# --------------------------------------------------------------------------- #
@dataclass
class OrientedBox:
    """An equipment's (optionally clearance-inflated) body as an oriented box: a
    rotation matrix ``R`` (``None`` when axis-aligned), the pivot (footprint centre
    = origin) and local min/max half-extents. A world point ``q`` is inside when
    ``lo <= Rᵀ·(q - pivot) <= hi``. The pivot and local extents match how
    ``equipment._oriented_box`` builds the rotated body, so occupancy/clash tests
    and the real geometry agree even when the equipment is spun."""

    R: np.ndarray | None
    pivot: np.ndarray
    lo: np.ndarray
    hi: np.ndarray

    @classmethod
    def around_equipment(cls, eq: ada.Equipment, clearance: float = 0.0) -> "OrientedBox":
        """The equipment body inflated by ``clearance`` (a run's cross-section
        half-extent, so a run's body — not just its centreline — is kept clear).
        Honours the placement rotation stashed on ``_topo_rotation_deg``."""
        from .equipment import rotation_matrix

        c = float(clearance)
        pivot = np.array([float(v) for v in eq.origin])  # (X+LX/2, Y+LY/2, Z) — footprint centre
        lo = np.array([-eq.lx / 2 - c, -eq.ly / 2 - c, -c])
        hi = np.array([eq.lx / 2 + c, eq.ly / 2 + c, eq.lz + c])
        R = rotation_matrix(*getattr(eq, "_topo_rotation_deg", (0.0, 0.0, 0.0)))
        return cls(R, pivot, lo, hi)

    def contains(self, q, tol: float = 1e-9) -> bool:
        """Whether world point ``q`` lies inside this oriented box."""
        local = (
            (self.R.T @ (np.asarray(q, float) - self.pivot))
            if self.R is not None
            else (np.asarray(q, float) - self.pivot)
        )
        return bool(np.all(local >= self.lo - tol) and np.all(local <= self.hi + tol))

    def world_aabb(self) -> tuple[np.ndarray, np.ndarray]:
        """The body's world-space axis-aligned bounds, used to prune the grid scan.
        Axis-aligned bodies use the extents directly; a rotated body takes the min/
        max over its eight rotated corners."""
        if self.R is None:
            return self.pivot + self.lo, self.pivot + self.hi
        lo, hi = self.lo, self.hi
        corners = np.array(
            [
                [lo[0], lo[1], lo[2]],
                [hi[0], lo[1], lo[2]],
                [lo[0], hi[1], lo[2]],
                [hi[0], hi[1], lo[2]],
                [lo[0], lo[1], hi[2]],
                [hi[0], lo[1], hi[2]],
                [lo[0], hi[1], hi[2]],
                [hi[0], hi[1], hi[2]],
            ]
        )
        world = (self.R @ corners.T).T + self.pivot
        return world.min(axis=0), world.max(axis=0)


# --------------------------------------------------------------------------- #
# Routing lattice
# --------------------------------------------------------------------------- #
def routing_grid(spaces: list[TopoSpace], equipments: list, spacing: float = 0.5) -> CellGrid:
    """A uniform lattice spanning the union of the space boxes plus a headroom
    level above the top deck (so runs can climb over equipment).

    A run must never lie *in* a wall or floor plate (only cross one perpendicular,
    where a penetration detail is modelled). Every interior lattice line that
    lands exactly on a plate plane is therefore dropped — for decks (Z boundaries),
    interior walls (shared X/Y boundaries between cells) alike: a perpendicular
    crossing still spans the plane on the edge between the surrounding lines, but
    no node sits inside the plate so nothing routes along it. The first/last line
    of each axis (the outer envelope walls) is kept for bounds — built external
    walls are instead blocked as no-go obstacles in ``_build_systems``. A
    ``spacing`` sub-floor band below the lowest deck gives low outlets (e.g. a tank
    drain) somewhere to route beneath the plate."""
    xs, ys, zs = [], [], []
    x_planes, y_planes, deck_planes = set(), set(), set()
    for s in spaces:
        xs += [s.X, s.X + s.DX]
        ys += [s.Y, s.Y + s.DY]
        zs += [s.Z, s.Z + s.DZ]
        x_planes.update((round(float(s.X), 4), round(float(s.X + s.DX), 4)))
        y_planes.update((round(float(s.Y), 4), round(float(s.Y + s.DY), 4)))
        deck_planes.update((round(float(s.Z), 4), round(float(s.Z + s.DZ), 4)))
    for eq in equipments:
        if isinstance(eq, ada.Equipment):
            zs.append(float(eq.origin[2]) + eq.lz)
    headroom = spacing * 3
    grid = CellGrid.from_bounds(
        (min(xs), min(ys), min(zs) - spacing),  # a sub-floor band below the lowest deck
        (max(xs), max(ys), max(zs) + headroom),
        spacing=spacing,
    )

    # Drop interior levels coincident with a plate plane (keep the first/last so
    # the lattice never loses its bounds); margin < spacing so only exact hits go.
    # With the sub-floor band, the lowest deck is now interior and gets dropped too.
    margin = spacing * 0.25

    def _drop(vals: list[float], planes: set[float]) -> list[float]:
        return [
            v for i, v in enumerate(vals) if i == 0 or i == len(vals) - 1 or all(abs(v - p) > margin for p in planes)
        ]

    grid.x_list = _drop(grid.x_list, x_planes)
    grid.y_list = _drop(grid.y_list, y_planes)
    grid.z_list = _drop(grid.z_list, deck_planes)
    return grid


# --------------------------------------------------------------------------- #
# Equipment occupancy + port landing
# --------------------------------------------------------------------------- #
def occupy_equipment(grid: CellGrid, eq: ada.Equipment, clearance: float = 0.0) -> None:
    """Mark grid nodes inside the equipment body — inflated by ``clearance`` — as
    occupied so A* routes around it. The clearance (a run's cross-section
    half-extent) keeps the run's body, not merely its centreline, from clipping
    the equipment. Honours the equipment's placement rotation: a spun box occupies
    its ROTATED footprint (a switchboard turned 90° pokes out where its unrotated
    AABB never reached), so a run no longer grazes the real body."""
    box = OrientedBox.around_equipment(eq, clearance)
    tol = 1e-9
    (x0, y0, z0), (x1, y1, z1) = box.world_aabb()
    for ix, x in enumerate(grid.x_list):
        if not (x0 - tol <= x <= x1 + tol):
            continue
        for iy, y in enumerate(grid.y_list):
            if not (y0 - tol <= y <= y1 + tol):
                continue
            for iz, z in enumerate(grid.z_list):
                if not (z0 - tol <= z <= z1 + tol):
                    continue
                if box.R is None or box.contains((x, y, z)):
                    grid.register((ix, iy, iz), eq.name)


def augment_grid_with_ports(grid: CellGrid, built_systems: list) -> None:
    """Insert every system's port (and nozzle-stub) coordinates as grid lines
    BEFORE equipment occupancy is stamped.

    A run leaves each port along the port's world position and a one-cell stub;
    the router inserts those exact coordinates as grid lines so it can land on the
    port cleanly. If that happens per-system DURING routing (as the swept runs do)
    the new line is added AFTER ``occupy_equipment`` already ran — so its nodes
    that fall inside an equipment box carry no occupancy, and a later run threads
    straight through that box along the fresh, un-blocked line (the drain routed
    through the pump). Front-loading every port/stub line here means occupancy is
    stamped onto ALL the lines the router will use, closing that corridor; the
    per-system augmentation then finds the line already present and is a no-op."""
    from ada.topology.routing import _grid_spacing, _port_stub, augment_grid_with_points

    stub_len = _grid_spacing(grid)
    pts = []
    for system in built_systems:
        for port in getattr(system, "ports", []):
            pts.append(port.get_global_position())
            stub = _port_stub(port, stub_len)
            if stub is not None:
                pts.append(stub)
    if pts:
        augment_grid_with_points(grid, pts)
