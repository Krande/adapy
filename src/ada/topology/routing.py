"""Rule-based routing of systems over a :class:`CellGrid`.

Kernel-agnostic (heapq + plain math): 6-connected orthogonal A* over the grid's
node lattice, with pluggable per-move rules (allowed nodes, move costs, bend
penalty). ``route_system`` routes between two equipment ports and
``system_route_to_geometry`` turns the routed polyline into adapy geometry.

``RoutingBlueprintBase`` is the scaffold for blueprints that assign routing
rules and navigate systems through the cell structure.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import ada
from ada.topology.blueprint import BlueprintBase
from ada.topology.grid import CellGrid, GridIndex

if TYPE_CHECKING:
    from ada.api.systems.base import System
    from ada.api.systems.ports import Port

__all__ = [
    "RoutingError",
    "RoutingRules",
    "RoutingBlueprintBase",
    "nearest_index",
    "astar_route",
    "path_to_polyline",
    "route_system",
    "system_route_to_geometry",
]

_NEIGHBOR_STEPS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


class RoutingError(Exception):
    """Raised when no route can be found between two grid nodes."""


def _default_is_allowed(idx: GridIndex, grid: CellGrid) -> bool:
    return not grid.has_geometry(idx)


def _default_move_cost(a: GridIndex, b: GridIndex, grid: CellGrid) -> float:
    xa, ya, za = grid.coord_from_index(a)
    xb, yb, zb = grid.coord_from_index(b)
    return abs(xb - xa) + abs(yb - ya) + abs(zb - za)


@dataclass
class RoutingRules:
    """Pluggable routing costs. Defaults: occupied nodes are forbidden, moves
    cost their length, vertical moves cost ``elevation_penalty`` x extra, and
    each direction change adds ``bend_penalty``."""

    is_allowed: Callable[[GridIndex, CellGrid], bool] = field(default=_default_is_allowed)
    move_cost: Callable[[GridIndex, GridIndex, CellGrid], float] | None = None
    elevation_penalty: float = 2.0
    bend_penalty: float = 0.5

    def cost(self, a: GridIndex, b: GridIndex, grid: CellGrid) -> float:
        if self.move_cost is not None:
            return self.move_cost(a, b, grid)
        base = _default_move_cost(a, b, grid)
        if a[2] != b[2]:
            base += self.elevation_penalty * abs(grid.z_list[b[2]] - grid.z_list[a[2]])
        return base


def _nearest_axis_index(vals: list[float], v: float) -> int:
    return min(range(len(vals)), key=lambda i: abs(vals[i] - v))


def nearest_index(grid: CellGrid, x: float, y: float, z: float) -> GridIndex:
    """Snap a world coordinate to the closest grid node (``index_of`` raises for
    anything not exactly on a grid line)."""
    if not (grid.x_list and grid.y_list and grid.z_list):
        raise RoutingError("grid has no coordinates; build it before routing")
    return (
        _nearest_axis_index(grid.x_list, x),
        _nearest_axis_index(grid.y_list, y),
        _nearest_axis_index(grid.z_list, z),
    )


def astar_route(
    grid: CellGrid, start: GridIndex, goal: GridIndex, rules: RoutingRules | None = None
) -> list[GridIndex]:
    """6-connected orthogonal A* from ``start`` to ``goal``; returns the node
    path including both endpoints."""
    if rules is None:
        rules = RoutingRules()
    dims = (len(grid.x_list), len(grid.y_list), len(grid.z_list))

    def heuristic(idx: GridIndex) -> float:
        xa, ya, za = grid.coord_from_index(idx)
        xg, yg, zg = grid.coord_from_index(goal)
        return abs(xg - xa) + abs(yg - ya) + abs(zg - za)

    # (f, tie, g, node, parent-direction); parent map reconstructs the path
    open_heap: list[tuple[float, int, float, GridIndex, tuple[int, int, int] | None]] = []
    heapq.heappush(open_heap, (heuristic(start), 0, 0.0, start, None))
    came_from: dict[GridIndex, GridIndex] = {}
    best_g: dict[GridIndex, float] = {start: 0.0}
    tie = 0

    while open_heap:
        _, _, g, current, prev_dir = heapq.heappop(open_heap)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path
        if g > best_g.get(current, float("inf")):
            continue
        for step in _NEIGHBOR_STEPS:
            nxt = (current[0] + step[0], current[1] + step[1], current[2] + step[2])
            if not all(0 <= nxt[i] < dims[i] for i in range(3)):
                continue
            if nxt != goal and not rules.is_allowed(nxt, grid):
                continue
            ng = g + rules.cost(current, nxt, grid)
            if prev_dir is not None and step != prev_dir:
                ng += rules.bend_penalty
            if ng < best_g.get(nxt, float("inf")):
                best_g[nxt] = ng
                came_from[nxt] = current
                tie += 1
                heapq.heappush(open_heap, (ng + heuristic(nxt), tie, ng, nxt, step))

    raise RoutingError(f"no route found between grid nodes {start} and {goal} — check occupied nodes and routing rules")


def path_to_polyline(grid: CellGrid, path: list[GridIndex]) -> list[ada.Point]:
    """Grid path -> world polyline with collinear points removed (bends only)."""
    pts = [ada.Point(*grid.coord_from_index(idx)) for idx in path]
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for prev, cur, nxt in zip(pts, pts[1:], pts[2:]):
        d1 = tuple((cur - prev).round(9))
        d2 = tuple((nxt - cur).round(9))
        cross = (
            d1[1] * d2[2] - d1[2] * d2[1],
            d1[2] * d2[0] - d1[0] * d2[2],
            d1[0] * d2[1] - d1[1] * d2[0],
        )
        if any(abs(c) > 1e-9 for c in cross):
            out.append(cur)
    out.append(pts[-1])
    return out


def route_system(
    system: System,
    grid: CellGrid,
    rules: RoutingRules | None = None,
    start: Port | None = None,
    end: Port | None = None,
) -> list[ada.Point]:
    """Route ``system`` between two of its connected ports (defaults: first and
    last). Port world positions are snapped to the grid for pathfinding; the
    exact port positions cap the ends of the returned polyline. Sets
    ``system.routed_path``."""
    if start is None or end is None:
        if len(system.ports) < 2:
            raise RoutingError(
                f"system {system.name!r} has {len(system.ports)} connected port(s); need two ends to route "
                "(pass start=/end= or connect more equipment)"
            )
        start = start if start is not None else system.ports[0]
        end = end if end is not None else system.ports[-1]

    p_start = start.get_global_position()
    p_end = end.get_global_position()
    idx_start = nearest_index(grid, *p_start)
    idx_end = nearest_index(grid, *p_end)

    try:
        path = astar_route(grid, idx_start, idx_end, rules)
    except RoutingError as e:
        raise RoutingError(
            f"failed to route system {system.name!r} from port {start.name!r} "
            f"({start.parent.name if start.parent else '?'}) to port {end.name!r} "
            f"({end.parent.name if end.parent else '?'}): {e}"
        ) from None

    polyline = path_to_polyline(grid, path)
    if tuple(polyline[0]) != tuple(p_start):
        polyline.insert(0, p_start)
    if tuple(polyline[-1]) != tuple(p_end):
        polyline.append(p_end)

    system.routed_path = polyline
    return polyline


def system_route_to_geometry(system: System, name: str | None = None) -> list:
    """Turn ``system.routed_path`` into adapy geometry appended to
    ``system.route_geometry``. Piping gets a real pipe; cable/duct runs use a
    small-radius pipe as carrier geometry (their IFC entity class still follows
    the system category via ``segment_ifc_class`` metadata)."""
    from ada.api.systems.base import CableSystem, DuctSystem, PipingSystem

    if system.routed_path is None:
        raise RoutingError(f"system {system.name!r} has no routed path; call route_system first")

    name = name if name is not None else f"{system.name}_route"
    if isinstance(system, PipingSystem):
        radius, wt = system.pipe_radius, system.pipe_wt
        segment_ifc_class = "IfcPipeSegment"
    elif isinstance(system, CableSystem):  # covers ElectricalSystem
        radius, wt = 0.02, 2e-3
        segment_ifc_class = "IfcCableSegment"
    elif isinstance(system, DuctSystem):
        radius, wt = 0.1, 2e-3
        segment_ifc_class = "IfcDuctSegment"
    else:
        radius, wt = 0.02, 2e-3
        segment_ifc_class = "IfcPipeSegment"

    sec = ada.Section(f"{name}_sec", "PIPE", r=radius, wt=wt)
    pipe = ada.Pipe(name, system.routed_path, sec, metadata={"segment_ifc_class": segment_ifc_class})
    system.route_geometry.append(pipe)
    return system.route_geometry


class RoutingBlueprintBase(BlueprintBase):
    """Blueprint scaffold that routes a set of systems through the cell
    structure. Subclasses override ``rules_for`` (per-system rules) and/or
    ``build_routing_grid`` (custom lattice)."""

    def __init__(self, systems: list[System] | None = None):
        super().__init__()
        self.systems: list[System] = systems if systems is not None else []

    def rules_for(self, system: System) -> RoutingRules:
        return RoutingRules()

    def build_routing_grid(self) -> CellGrid:
        """Default lattice: the bounding box of the cell graph's cells at 0.5 m
        spacing. Override for domain-specific grids."""
        cg = self.builder.cell_graph
        pts = [p for cell in cg.cells for p in cell.get_points()]
        if not pts:
            raise RoutingError("cell graph has no cells; cannot derive a routing grid")
        xs, ys, zs = zip(*((float(p[0]), float(p[1]), float(p[2])) for p in pts))
        return CellGrid.from_bounds((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)), spacing=0.5)

    def build(self) -> ada.Part:
        self.output_part = ada.Part("Systems")
        grid = self.build_routing_grid()
        for system in self.systems:
            route_system(system, grid, rules=self.rules_for(system))
            system_route_to_geometry(system)
            self.add_to_area(system.name, ada.Part(f"{system.name}_geom") / system.route_geometry)
        self.load_parts_from_area_map()
        return self.output_part
