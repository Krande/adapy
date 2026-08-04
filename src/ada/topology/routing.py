"""Rule-based routing of systems over a :class:`CellGrid`.

Kernel-agnostic (heapq + plain math): 6-connected orthogonal A* over the grid's
node lattice, with pluggable per-move rules (allowed nodes, move costs, bend
penalty). ``route_system`` routes between two equipment ports and
``system_route_to_geometry`` turns the routed polyline into adapy geometry.

``RoutingBlueprintBase`` is the scaffold for blueprints that assign routing
rules and navigate systems through the cell structure.
"""

from __future__ import annotations

import bisect
import heapq
from collections import defaultdict
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
    "RunWarning",
    "RoutingRules",
    "RoutingBlueprintBase",
    "nearest_index",
    "astar_route",
    "astar_route_constrained",
    "swept_bend_params",
    "path_to_polyline",
    "route_system",
    "system_route_to_geometry",
    "occupy_run",
    "occupy_faces",
    "run_half_extent",
]

_NEIGHBOR_STEPS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


class RoutingError(Exception):
    """Raised when no route can be found between two grid nodes."""


@dataclass
class RunWarning:
    """A place where a routed (or authored) run can't be built as a clean fitting
    — a bend left sharp because the route is too cramped, a section too big to
    turn without inverting, etc. The geometry is still emitted best-effort; the
    warning names the spot and a concrete fix so the input can be respaced rather
    than silently deformed."""

    position: tuple[float, float, float]
    message: str
    suggestion: str

    def __str__(self) -> str:
        return f"{self.message}\n      fix: {self.suggestion}"


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


def _nearest_step_index(vec) -> int:
    """Snap a world direction to the index of the closest of the six axis steps in
    ``_NEIGHBOR_STEPS`` (the one whose unit axis has the largest dot product). Used
    to turn a nozzle's outward normal into a forced leaving/arriving heading for the
    turn-constrained planner."""
    v = (float(vec[0]), float(vec[1]), float(vec[2]))
    return max(
        range(len(_NEIGHBOR_STEPS)),
        key=lambda i: _NEIGHBOR_STEPS[i][0] * v[0] + _NEIGHBOR_STEPS[i][1] * v[1] + _NEIGHBOR_STEPS[i][2] * v[2],
    )


# Per-heading turn table: for each axis-step index, the four perpendicular headings
# (every step except itself and its reverse). Steps are paired reverses (d ^ 1).
_PERP_STEPS: tuple[tuple[int, ...], ...] = tuple(
    tuple(j for j in range(len(_NEIGHBOR_STEPS)) if j != d and j != (d ^ 1)) for d in range(len(_NEIGHBOR_STEPS))
)


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


def astar_route_constrained(
    grid: CellGrid,
    start: GridIndex,
    goal: GridIndex,
    *,
    start_dir: int,
    goal_dir: int,
    t1_cells: float,
    t2_cells: float,
    start_run: float = 0.0,
    rules: RoutingRules | None = None,
) -> list[GridIndex]:
    """Direction-augmented (state-lattice) 6-connected A* that is *feasible by
    construction* for a fixed-radius fitting: every corner is guaranteed enough
    straight either side to host the bend, so the route never needs post-smoothing
    and never produces a corner too tight to round.

    ``start_dir``/``goal_dir`` are indices into :data:`_NEIGHBOR_STEPS` — the forced
    heading the run must *leave* ``start`` along (the nozzle's outward normal) and
    the heading of *travel* it must *arrive* at ``goal`` along (``-`` the far
    nozzle's outward normal). The search state is
    ``(node, dir_idx, from_start, bucket)``:

    * ``dir_idx`` — the current heading.
    * ``from_start`` — ``True`` until the first turn (still on the leaving straight).
    * ``bucket`` ∈ {0, 1, 2} — how much straight has accrued since the last turn:
      ``0`` below ``t1`` (one bend's tangent), ``1`` in ``[t1, t2)``, ``2`` at/above
      ``t2`` (an interior leg shared by two bends). ``t1``/``t2`` are the cell counts
      ``t1_cells``/``t2_cells`` scaled by the grid pitch, so the run length is
      measured in true distance (robust to a non-uniform lattice).

    A **turn** onto a perpendicular heading is allowed only from a leaving straight
    that reached ``bucket >= 1`` (``from_start``) or an interior straight that reached
    ``bucket >= 2`` — i.e. both legs of every bend clear the fixed radius. The goal is
    accepted only when reached along ``goal_dir`` with ``bucket >= 1`` (a full arriving
    tangent). Occupied nodes are impassable except ``goal`` itself (so a nozzle inside
    an equipment-clearance halo is still reachable). ``start_run`` seeds the leaving
    straight already accrued *before* ``start`` — the length of the nozzle stub leg
    (``start`` is the stub node just outside the equipment halo) — so a run can turn
    immediately past its nozzle when the stub already supplies a bend's tangent. Dedup
    is on the full 4-tuple via ``best_g``, bounding the state to ``nodes x 6 x 2 x 3``.

    Raises :class:`RoutingError` when no state reaches the goal — the space is too
    tight for the fitting; widen the corridor, use a smaller section, or reduce the
    bend radius."""
    if rules is None:
        rules = RoutingRules()
    dims = (len(grid.x_list), len(grid.y_list), len(grid.z_list))
    pitch = _grid_spacing(grid) or 1.0
    t1 = t1_cells * pitch
    t2 = t2_cells * pitch
    inf = float("inf")
    tol = 1e-9
    bend_penalty = rules.bend_penalty

    def bucket(run_len: float) -> int:
        if run_len >= t2 - tol:
            return 2
        if run_len >= t1 - tol:
            return 1
        return 0

    def in_bounds(n: GridIndex) -> bool:
        return 0 <= n[0] < dims[0] and 0 <= n[1] < dims[1] and 0 <= n[2] < dims[2]

    def passable(n: GridIndex) -> bool:
        return n == goal or not grid.has_geometry(n)

    def seg_len(a: GridIndex, b: GridIndex) -> float:
        xa, ya, za = grid.coord_from_index(a)
        xb, yb, zb = grid.coord_from_index(b)
        return abs(xb - xa) + abs(yb - ya) + abs(zb - za)

    def heuristic(n: GridIndex) -> float:
        xa, ya, za = grid.coord_from_index(n)
        xg, yg, zg = grid.coord_from_index(goal)
        return abs(xg - xa) + abs(yg - ya) + abs(zg - za)

    start_state = (start, start_dir, True, bucket(start_run))
    # heap entry: (f, tie, g, run_len, state) — run_len rides along so the bucket can
    # be recomputed on each straight step without living in the dedup key.
    open_heap: list[tuple[float, int, float, float, tuple]] = [(heuristic(start), 0, 0.0, start_run, start_state)]
    came_from: dict[tuple, tuple] = {}
    best_g: dict[tuple, float] = {start_state: 0.0}
    tie = 0

    while open_heap:
        _, _, g, run_len, state = heapq.heappop(open_heap)
        if g > best_g.get(state, inf):
            continue
        node, d, from_start, buck = state
        if node == goal and d == goal_dir and buck >= 1:
            path = [node]
            s = state
            while s in came_from:
                s = came_from[s]
                path.append(s[0])
            path.reverse()
            return path

        # Straight: continue along d; the accrued run grows so the bucket rises.
        step = _NEIGHBOR_STEPS[d]
        nxt = (node[0] + step[0], node[1] + step[1], node[2] + step[2])
        if in_bounds(nxt) and passable(nxt):
            seg = seg_len(node, nxt)
            nrl = run_len + seg
            ns = (nxt, d, from_start, bucket(nrl))
            ng = g + seg
            if ng < best_g.get(ns, inf):
                best_g[ns] = ng
                came_from[ns] = state
                tie += 1
                heapq.heappush(open_heap, (ng + heuristic(nxt), tie, ng, nrl, ns))

        # Turn: only once the current straight has enough tangent for the bend.
        if (from_start and buck >= 1) or (not from_start and buck >= 2):
            for dp in _PERP_STEPS[d]:
                pstep = _NEIGHBOR_STEPS[dp]
                nxt = (node[0] + pstep[0], node[1] + pstep[1], node[2] + pstep[2])
                if not in_bounds(nxt) or not passable(nxt):
                    continue
                seg = seg_len(node, nxt)
                ns = (nxt, dp, False, bucket(seg))
                ng = g + seg + bend_penalty
                if ng < best_g.get(ns, inf):
                    best_g[ns] = ng
                    came_from[ns] = state
                    tie += 1
                    heapq.heappush(open_heap, (ng + heuristic(nxt), tie, ng, seg, ns))

    raise RoutingError(
        f"no feasible turn-constrained route from {start} to {goal}: the fitting needs "
        f"{t1:.3g} m of straight beside every bend ({t2:.3g} m between two bends) and the "
        "space is too tight — widen the corridor, use a smaller cross-section, or reduce the bend radius"
    )


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


def _grid_spacing(grid: CellGrid) -> float:
    """The lattice pitch. Used as the default nozzle-stub length so a run leaves
    a port one cell along its direction before turning onto the grid.

    Per axis we take the *median* step, not the smallest: ``CellGrid.from_bounds``
    appends the exact max bound, so the final cell is a short remainder step
    (e.g. a 0.1 tail on an otherwise 0.5 lattice). Picking the min there would
    yield a stub far shorter than a cell, leaving a tiny leg that folds into the
    first grid node and breaks pipe-elbow generation."""
    steps = []
    for vals in (grid.x_list, grid.y_list, grid.z_list):
        axis = sorted(abs(b - a) for a, b in zip(vals[:-1], vals[1:]) if abs(b - a) > 1e-9)
        if axis:
            steps.append(axis[len(axis) // 2])
    return min(steps) if steps else 0.0


def _seg_len(a: ada.Point, b: ada.Point) -> float:
    return ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5


def _sanitize_polyline(pts: list[ada.Point], tol: float = 1e-6) -> list[ada.Point]:
    """Drop coincident and collinear/anti-parallel interior vertices so the run
    is a clean sequence of real bends.

    End-capping (nozzle stub + exact port position) can leave a zero-length hop
    or a 180° spike — the stub overshoots the first grid node, so the run steps
    out along the nozzle and immediately reverses. Both are geometrically
    degenerate and crash pipe-elbow generation downstream (no arc fits a 180°
    turn). A zero cross-product catches the anti-parallel spike apex as well as
    ordinary collinear points; a second dedup pass collapses any coincidence the
    apex removal exposes."""

    def _dedup(seq: list[ada.Point]) -> list[ada.Point]:
        out: list[ada.Point] = []
        for p in seq:
            if not out or _seg_len(p, out[-1]) > tol:
                out.append(p)
        return out

    seq = _dedup(pts)
    if len(seq) <= 2:
        return seq
    kept = [seq[0]]
    for cur, nxt in zip(seq[1:-1], seq[2:]):
        prev = kept[-1]
        d1 = (cur[0] - prev[0], cur[1] - prev[1], cur[2] - prev[2])
        d2 = (nxt[0] - cur[0], nxt[1] - cur[1], nxt[2] - cur[2])
        cross = (
            d1[1] * d2[2] - d1[2] * d2[1],
            d1[2] * d2[0] - d1[0] * d2[2],
            d1[0] * d2[1] - d1[1] * d2[0],
        )
        if cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2 > tol * tol:
            kept.append(cur)
    kept.append(seq[-1])
    return _dedup(kept)


def _port_stub(port: Port, stub_len: float) -> ada.Point | None:
    """A point ``stub_len`` along the port's (outward) direction vector from its
    world position, or ``None`` when the port has no usable direction / stub. The
    nozzle physically points out of the equipment body for both inlets and
    outlets, so the stub always follows the outward normal regardless of
    IN/OUT."""
    p = port.get_global_position()
    d = port.direction_vector
    n = float((d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5)
    if n < 1e-9 or stub_len <= 0.0:
        return None
    return ada.Point(p[0] + d[0] / n * stub_len, p[1] + d[1] / n * stub_len, p[2] + d[2] / n * stub_len)


def _cap_end(pts: list[ada.Point], port_pos: ada.Point, stub: ada.Point | None, *, at_start: bool) -> None:
    """Prepend/append the exact port position (and its nozzle stub) so the run
    terminates at the port and leaves it along the nozzle normal. Skips points
    that coincide with what's already there."""
    if at_start:
        # order at the head: port_pos, stub, <path...>
        for p in reversed([q for q in (port_pos, stub) if q is not None]):
            if not pts or tuple(pts[0]) != tuple(p):
                pts.insert(0, p)
    else:
        # order at the tail: <path...>, stub, port_pos
        for p in [q for q in (stub, port_pos) if q is not None]:
            if not pts or tuple(pts[-1]) != tuple(p):
                pts.append(p)


def swept_bend_params(system: System) -> tuple[float, float, float, float]:
    """The bend geometry a swept (duct / cable-tray) run needs, factored out of
    :func:`system_route_to_geometry` so the router and the modeller agree.

    Returns ``(bend_r, floor, lateral_half, up_half)``:

    * ``lateral_half``/``up_half`` — the swept profile's true half-width and
      half-height. An open cable tray rotates the channel a quarter turn, so its
      width comes from the section height and vice-versa; a closed duct keeps them.
    * ``floor`` — the inversion-proof minimum radius (:func:`_inversion_floor`),
      the smallest bend that won't crush the section inner wall.
    * ``bend_r`` — the run's fixed centreline radius: the configured
      ``system.bend_radius`` (or ``~1x`` the section when unset), never below
      ``floor``. This is the tangent every corner must clear on both legs, which the
      turn-constrained planner enforces."""
    from ada.api.systems.base import CableSystem, DuctSystem

    if isinstance(system, CableSystem):  # covers ElectricalSystem — open UNP tray
        # UNP section is authored h=tray_width, w_top=tray_height; open_channel
        # rotation makes lateral = 0.5*h, up = 0.5*w_top (see _rotate_profile_90).
        lateral_half = 0.5 * float(system.tray_width or 0.0)
        up_half = 0.5 * float(system.tray_height or 0.0)
    elif isinstance(system, DuctSystem):  # closed BOX duct
        lateral_half = 0.5 * float(system.duct_width or 0.0)
        up_half = 0.5 * float(system.duct_height or 0.0)
    else:
        raise RoutingError(f"system {getattr(system, 'name', system)!r} is not a swept (duct/cable-tray) run")

    floor = _inversion_floor(lateral_half, up_half, 0.0)
    section_r = 2.0 * max(lateral_half, up_half)
    cfg_r = getattr(system, "bend_radius", None)
    bend_r = max(float(cfg_r) if cfg_r else section_r, floor)
    return bend_r, floor, lateral_half, up_half


def _route_swept(
    system: System,
    grid: CellGrid,
    start: Port,
    end: Port,
    stub_len: float,
) -> list[ada.Point]:
    """Route a swept (duct / cable-tray) system with the turn-constrained planner so
    the run is feasible by construction — every corner has room for the fixed-radius
    bend, no post-smoothing, no cramped fillets.

    Both port world positions (and their nozzle stubs) are augmented onto the grid so
    each end is an exact grid node reached along whole legs. The run is forced to
    leave ``start`` along its outward nozzle normal and to arrive at ``end`` travelling
    into its nozzle (``-`` the far outward normal). The straight-run thresholds are the
    run's bend radius (one tangent) and twice it (an interior leg shared by two bends),
    expressed in grid-pitch units. A :class:`RoutingError` (no feasible route) is left
    to propagate so the design engine can skip and report it."""
    bend_r, _floor, _lat, _up = swept_bend_params(system)

    p_start = start.get_global_position()
    p_end = end.get_global_position()
    stub_start = _port_stub(start, stub_len)
    stub_end = _port_stub(end, stub_len)
    augment_grid_with_points(grid, [p_start, p_end, stub_start, stub_end])

    dims = (len(grid.x_list), len(grid.y_list), len(grid.z_list))

    def _anchor(port_pos: ada.Point, stub: ada.Point | None) -> tuple[GridIndex, ada.Point | None, float]:
        # Anchor the A* at the nozzle stub node (one stub-length out along the port
        # normal, beyond the equipment's own clearance halo), so the search starts in
        # the clear and never has to step through the port's own occupied cells. The
        # stub leg (port -> stub) is kept for the end cap and its length seeds the
        # leaving straight, so a run can turn right past its nozzle. A stub that falls
        # off-grid (a nozzle facing straight into a boundary) drops back to the bare
        # port node with no seeded straight.
        if stub is not None:
            idx = nearest_index(grid, *stub)
            if all(0 <= idx[i] < dims[i] for i in range(3)):
                return idx, stub, _seg_len(port_pos, stub)
        idx = nearest_index(grid, *port_pos)
        return idx, None, 0.0

    idx_start, stub_start, run_seed = _anchor(p_start, stub_start)
    idx_end, stub_end, _ = _anchor(p_end, stub_end)
    if not (all(0 <= idx_start[i] < dims[i] for i in range(3)) and all(0 <= idx_end[i] < dims[i] for i in range(3))):
        raise RoutingError(f"swept system {system.name!r}: a port falls outside the routing grid")

    # Forced leaving/arriving headings from the nozzle normals: leave A along its
    # outward normal, arrive at B travelling into it (-outward_normal_B).
    start_dir = _nearest_step_index(start.direction_vector)
    goal_dir = _nearest_step_index([-float(c) for c in end.direction_vector])

    pitch = _grid_spacing(grid) or 1.0
    # Fractional "cells" of straight a bend needs — bend_r (one tangent) and 2*bend_r
    # (an interior leg). Left un-rounded so the requirement is the true radius, not a
    # whole cell (a run whose nozzle sits one short leg from a wall still routes).
    t1_cells = bend_r / pitch
    t2_cells = 2.0 * bend_r / pitch

    try:
        path = astar_route_constrained(
            grid,
            idx_start,
            idx_end,
            start_dir=start_dir,
            goal_dir=goal_dir,
            t1_cells=t1_cells,
            t2_cells=t2_cells,
            start_run=run_seed,
        )
    except RoutingError as e:
        raise RoutingError(
            f"failed to route swept system {system.name!r} from port {start.name!r} "
            f"({start.parent.name if start.parent else '?'}) to port {end.name!r} "
            f"({end.parent.name if end.parent else '?'}): {e}"
        ) from None

    polyline = path_to_polyline(grid, path)
    # Cap the ends with the exact port positions and their nozzle stubs so each run
    # terminates at the port and leaves/enters it along the nozzle (the stub leg
    # crosses the equipment's own halo — expected for a nozzle exiting its body).
    _cap_end(polyline, p_start, stub_start, at_start=True)
    _cap_end(polyline, p_end, stub_end, at_start=False)
    polyline = _sanitize_polyline(polyline)

    system.routed_path = polyline
    return polyline


def route_system(
    system: System,
    grid: CellGrid,
    rules: RoutingRules | None = None,
    start: Port | None = None,
    end: Port | None = None,
    stub_len: float | None = None,
) -> list[ada.Point]:
    """Route ``system`` between two of its connected ports (defaults: first and
    last). Each run leaves its port along the port's outward direction vector for
    ``stub_len`` (defaults to one grid pitch) before snapping onto the grid for
    A* pathfinding; the exact port positions cap the ends of the returned
    polyline. Sets ``system.routed_path``."""
    if start is None or end is None:
        if len(system.ports) < 2:
            raise RoutingError(
                f"system {system.name!r} has {len(system.ports)} connected port(s); need two ends to route "
                "(pass start=/end= or connect more equipment)"
            )
        start = start if start is not None else system.ports[0]
        end = end if end is not None else system.ports[-1]

    if rules is None:
        rules = RoutingRules()
    if stub_len is None:
        stub_len = _grid_spacing(grid)

    # Graceful swept runs (ducts, cable trays and electrical) come as straight
    # sections plus fixed-radius bends, so they route through the turn-constrained
    # planner — every corner feasible by construction. Pipes bend continuously
    # (revolved elbows), and a *strict* run keeps the free A* path so its geometry
    # phase still raises (naming the points) when the layout can't fit the fixed
    # radius; both keep the free A* path below.
    from ada.api.systems.base import CableSystem, DuctSystem

    if isinstance(system, (CableSystem, DuctSystem)) and not bool(getattr(system, "strict", False)):
        return _route_swept(system, grid, start, end, stub_len)

    dims = (len(grid.x_list), len(grid.y_list), len(grid.z_list))

    p_start = start.get_global_position()
    p_end = end.get_global_position()

    def _anchor(port_pos: ada.Point, stub: ada.Point | None) -> tuple[GridIndex, ada.Point | None]:
        # Pathfind to/from the nozzle stub (one cell along the port normal) so the
        # grid route leaves the port in the direction it faces, and ALWAYS keep the
        # stub for the end cap so the last leg follows the nozzle orientation. We
        # target the stub node even when it sits in an occupied cell — A* exempts
        # its goal node from ``is_allowed`` (line ``nxt != goal``), so the run still
        # approaches the stub from a clear neighbour but terminates along the
        # nozzle. (The old code dropped the stub whenever it fell inside an
        # equipment-clearance halo — e.g. a site terminal on the wall right next to
        # a switchboard — which silently discarded the specified orientation.) Only
        # a genuinely off-grid stub falls back to snapping the bare port position.
        if stub is not None:
            idx = nearest_index(grid, *stub)
            if all(0 <= idx[i] < dims[i] for i in range(3)):
                return idx, stub
        return nearest_index(grid, *port_pos), None

    idx_start, stub_start = _anchor(p_start, _port_stub(start, stub_len))
    idx_end, stub_end = _anchor(p_end, _port_stub(end, stub_len))

    try:
        path = astar_route(grid, idx_start, idx_end, rules)
    except RoutingError as e:
        raise RoutingError(
            f"failed to route system {system.name!r} from port {start.name!r} "
            f"({start.parent.name if start.parent else '?'}) to port {end.name!r} "
            f"({end.parent.name if end.parent else '?'}): {e}"
        ) from None

    polyline = path_to_polyline(grid, path)
    _cap_end(polyline, p_start, stub_start, at_start=True)
    _cap_end(polyline, p_end, stub_end, at_start=False)
    polyline = _sanitize_polyline(polyline)

    system.routed_path = polyline
    return polyline


def _orthogonalize_polyline(pts: list[ada.Point]) -> list[ada.Point]:
    """Replace any diagonal segment with axis-aligned steps (X, then Y, then Z),
    so a box/channel run stays orthogonal. A straight-swept beam segment reads
    wrong on a diagonal (the port-stub cap can leave a short diagonal hop);
    ducts/cable trays are built as orthogonal runs with fittings, so this matches
    reality. Pipes keep their diagonals (their elbows bend continuously)."""
    if len(pts) < 2:
        return list(pts)
    out = [pts[0]]
    for b in pts[1:]:
        cur = list(out[-1])
        for axis in (0, 1, 2):
            if abs(b[axis] - cur[axis]) > 1e-9:
                cur[axis] = b[axis]
                out.append(ada.Point(*cur))
    return _sanitize_polyline(out)


def _collapse_short_legs(pts: list[ada.Point], min_len: float) -> list[ada.Point]:
    """Remove interior legs shorter than ``min_len`` (a swept run's cross-section
    half-extent) so a routed centreline never carries a sub-profile jog.

    Such a jog — a grid-remainder step, or the short cap connecting an off-grid
    nozzle to the lattice — would otherwise fillet into a tiny arc whose radius is
    below the profile half-width, inverting the swept solid's inner wall into a
    self-intersecting crush (thousands of overlapping facets). The two vertices of
    each offending leg are dropped and the neighbours reconnected orthogonally;
    the resulting centreline shift is bounded by ``min_len`` so it stays inside the
    equipment clearance halo (which is sized to the same half-extent). The two
    terminal legs — the port stub/cap that carries nozzle orientation — are never
    collapsed.

    A collapse is only kept when it strictly reduces the vertex count. On a *3D*
    staircase (short x-, y- AND z-steps) removing one step and re-squaring the gap
    simply re-inserts an equivalent step, so the count wouldn't shrink — accepting
    such a no-op would spin forever. Those legs are left in place (the fillet's
    inversion guard renders them as sharp corners instead), which keeps the pass
    bounded to at most one collapse per vertex."""
    out = _orthogonalize_polyline([ada.Point(*p) for p in pts])
    if min_len <= 0.0:
        return out
    guard = len(out) + 1  # backstop: at most one accepted collapse per vertex
    while len(out) > 3 and guard > 0:
        guard -= 1
        collapsed = False
        for k in range(1, len(out) - 2):  # interior legs only (both terminal legs kept)
            if _seg_len(out[k], out[k + 1]) < min_len:
                candidate = _orthogonalize_polyline(out[:k] + out[k + 2 :])
                if len(candidate) < len(out):  # only keep a collapse that actually simplifies
                    out = candidate
                    collapsed = True
                    break
        if not collapsed:
            break
    return out


def _ortho_path_free(grid: CellGrid, a, b) -> bool:
    """True when the orthogonal L-path ``a -> b`` (X, then Y, then Z) crosses no
    occupied grid node — i.e. the shortcut stays in the clear corridor. Used by
    :func:`_space_bends` to validate a candidate simplification against the voxel
    occupancy (equipment and, once sequential routing marks them, other systems)."""
    corners = _orthogonalize_polyline([ada.Point(*a), ada.Point(*b)])
    for p, q in zip(corners, corners[1:]):
        ia = nearest_index(grid, *p)
        ib = nearest_index(grid, *q)
        axis = next((k for k in range(3) if ia[k] != ib[k]), None)
        if axis is None:
            continue
        step = 1 if ib[axis] > ia[axis] else -1
        idx = list(ia)
        while True:
            if grid.has_geometry(tuple(idx)):
                return False
            if idx[axis] == ib[axis]:
                break
            idx[axis] += step
    return True


def _space_bends(pts: list[ada.Point], grid: CellGrid) -> list[ada.Point]:
    """Pull a routed centreline taut against the grid occupancy so its bends end
    up few and well-separated — the "post-smooth in the clear corridor" pass.

    A fine grid finds a collision-free route but leaves it zig-zagging (port-cap
    jogs, one-cell detours), which fillets into cramped, twisting bends. Here we
    greedily replace each run of vertices with the farthest orthogonal shortcut
    that :func:`_ortho_path_free` confirms is unobstructed — removing the
    unnecessary detours while, by construction, never crossing a blocked node
    (equipment / another system). The two terminal legs (the port stub/cap that
    carries nozzle orientation) are preserved. With no grid the path is returned
    orthogonalised but un-pulled."""
    out = _orthogonalize_polyline([ada.Point(*p) for p in pts])
    n = len(out)
    if grid is None or n <= 3:
        return out
    kept = [out[0], out[1]]  # keep the entry nozzle leg
    i = 1
    while i < n - 2:
        best = i + 1
        for j in range(n - 2, i + 1, -1):  # farthest reachable shortcut, keeping the exit nozzle leg
            if _ortho_path_free(grid, tuple(out[i]), tuple(out[j])):
                best = j
                break
        kept.extend(_orthogonalize_polyline([out[i], out[best]])[1:])
        i = best
    if _seg_len(kept[-1], out[n - 2]) > 1e-9:
        kept.append(out[n - 2])
    kept.append(out[n - 1])
    return _sanitize_polyline(kept)


def run_half_extent(system) -> float:
    """A run's cross-section half-extent — pipe radius, or half the widest duct/
    tray dimension — i.e. how far its body reaches from the centreline."""
    r = getattr(system, "pipe_radius", None)
    if r is not None:
        return float(r)
    w = getattr(system, "duct_width", None) or getattr(system, "tray_width", None)
    h = getattr(system, "duct_height", None) or getattr(system, "tray_height", None)
    return 0.5 * max(float(w or 0.0), float(h or 0.0))


def occupy_run(grid: CellGrid, polyline, radius: float, tag: str = "run") -> None:
    """Mark every grid node within ``radius`` of a routed run's centreline as
    occupied, so systems routed afterwards (and the taut-pull) keep clear of this
    run's body — the voxel-occupancy basis for inter-system avoidance. ``radius``
    is the run's own half-extent plus the clearance wanted from other runs."""
    pts = [tuple(float(c) for c in p) for p in polyline]
    if len(pts) < 2 or radius <= 0.0:
        return
    xs, ys, zs = grid.x_list, grid.y_list, grid.z_list
    for a, b in zip(pts, pts[1:]):
        lo = tuple(min(a[k], b[k]) - radius for k in range(3))
        hi = tuple(max(a[k], b[k]) + radius for k in range(3))
        for ix, x in enumerate(xs):
            if not (lo[0] <= x <= hi[0]):
                continue
            for iy, y in enumerate(ys):
                if not (lo[1] <= y <= hi[1]):
                    continue
                for iz, z in enumerate(zs):
                    if lo[2] <= z <= hi[2] and _point_seg_dist((x, y, z), a, b) <= radius + 1e-9:
                        grid.register((ix, iy, iz), tag)


def augment_grid_with_points(grid: CellGrid, points, tol: float = 1e-6) -> None:
    """Insert each point's x/y/z coordinate as a grid line (per axis, kept sorted
    and deduped) so A* can reach that exact coordinate without a sub-grid jog.

    Equipment ports sit at arbitrary world positions — the centre of a small,
    off-lattice box, an off-grid nozzle height — that a uniform lattice can't hit,
    so the route reaches them via a fractional cap staircase whose short legs
    fillet into cramped/sharp bends. Adding the port (and its nozzle-stub)
    coordinates as grid lines lets the route land on the port along whole grid
    legs, so the approach is a clean orthogonal turn rather than a micro-jog.

    Inserting a grid line shifts every higher index on that axis, so any occupancy
    already registered (equipment boxes, earlier systems' runs) is re-keyed onto the
    new indices — the routing lattice can be augmented per-system without corrupting
    the no-go volumes. ``None`` entries (a port with no usable nozzle stub) are
    ignored."""
    old_lists = (list(grid.x_list), list(grid.y_list), list(grid.z_list))
    inserted = False
    for axis, comp in (("x_list", 0), ("y_list", 1), ("z_list", 2)):
        vals = getattr(grid, axis)
        for p in points:
            if p is None:
                continue
            v = float(p[comp])
            i = bisect.bisect_left(vals, v)
            if (i < len(vals) and abs(vals[i] - v) < tol) or (i > 0 and abs(vals[i - 1] - v) < tol):
                continue  # already a grid line
            vals.insert(i, v)
            inserted = True
    if inserted and grid.occupancy:
        # Re-key occupancy: an old node's coordinate still exists in the augmented
        # list, so its new index is where that coordinate now sits.
        ox, oy, oz = old_lists
        remapped: dict[GridIndex, set] = defaultdict(set)
        for (ix, iy, iz), geoms in grid.occupancy.items():
            nix = bisect.bisect_left(grid.x_list, ox[ix])
            niy = bisect.bisect_left(grid.y_list, oy[iy])
            niz = bisect.bisect_left(grid.z_list, oz[iz])
            remapped[(nix, niy, niz)] |= geoms
        grid.occupancy = remapped


def occupy_faces(grid: CellGrid, faces, clearance: float = 0.0, tag: str = "no_go") -> None:
    """Voxelize planar members (walls, floor/roof decks) as no-go obstacles on the
    grid: every node lying within ``clearance`` of a face's plane AND inside the
    (``clearance``-inflated) outline bounding box is marked occupied, so A* routes
    around the member and the taut-pull never shortcuts across it.

    ``clearance`` should be at least the routed run's cross-section half-extent so
    the run's *body* — not merely its centreline — stays clear of the wall. A face
    with no built plate is still a valid obstacle; pass only the members you want
    treated as impenetrable (a system meant to *penetrate* a wall must NOT have
    that wall in ``faces``, or it can no longer route through it)."""
    import numpy as np

    c = float(clearance)
    for face in faces:
        pts = np.asarray([tuple(p) for p in face.get_points()], dtype=float)
        if len(pts) < 3:
            continue
        n = np.asarray(tuple(face.normal), dtype=float)
        nn = float(np.linalg.norm(n))
        if nn < 1e-9:
            continue
        n = n / nn
        p0 = pts[0]
        lo = pts.min(axis=0) - c
        hi = pts.max(axis=0) + c
        for ix, x in enumerate(grid.x_list):
            if not (lo[0] <= x <= hi[0]):
                continue
            for iy, y in enumerate(grid.y_list):
                if not (lo[1] <= y <= hi[1]):
                    continue
                for iz, z in enumerate(grid.z_list):
                    if not (lo[2] <= z <= hi[2]):
                        continue
                    if abs(float(n.dot(np.array([x, y, z]) - p0))) <= c + 1e-9:
                        grid.register((ix, iy, iz), tag)


def _point_seg_dist(p, a, b) -> float:
    ab = _v_sub(b, a)
    denom = _v_dot(ab, ab)
    t = 0.0 if denom < 1e-12 else max(0.0, min(1.0, _v_dot(_v_sub(p, a), ab) / denom))
    proj = _v_add(a, _v_scale(ab, t))
    return _v_norm(_v_sub(p, proj))


def _v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _v_scale(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def _v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _v_norm(v):
    return (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5


def _inversion_floor(lateral_half: float, up_half: float, min_radius: float) -> float:
    """The smallest fillet radius that keeps a bend's inner wall from inverting,
    for ANY bend orientation.

    Inner-wall inversion happens when the fillet radius drops below the profile's
    half-extent measured radially (in the bend plane). That extent varies with the
    bend plane, but the *largest* it can ever be — the worst case a bend of unknown
    orientation might present — is the asymmetric profile's diagonal half-extent
    ``hypot(lateral_half, up_half)``. Using that as the floor is inversion-proof in
    every orientation (a per-plane estimate can under-shoot on an oblique bend and
    crush the section into a self-intersecting mass)."""
    return max(min_radius, (lateral_half * lateral_half + up_half * up_half) ** 0.5)


def _polyline_to_directrix(
    pts: list[ada.Point],
    radius: float,
    min_radius: float = 0.0,
    *,
    lateral_half: float = 0.0,
    up_half: float = 0.0,
    strict: bool = False,
    run_name: str | None = None,
    warnings: list | None = None,
):
    """Build an ``IndexedPolyCurve`` directrix from an (orthogonal) polyline,
    filleting each interior corner into an ``ArcLine`` of ``radius`` so a swept
    duct/tray turns on a real circular bend. Straight spans stay ``Edge``
    segments. Returns ``None`` for a degenerate (< 2 point) path.

    Two modes, matching how real duct/cable-tray products behave — they come as
    straight sections plus standard-radius bends only, so a bend that can't fit is
    an error, not something to deform:

    * **graceful** (default): the radius is clamped to the tangent budget of each
      adjacent segment so nearby bends never eat into each other — half of an
      *interior* leg (shared with the neighbouring bend) but the *whole* of a
      *terminal* leg (the port stub/cap, which has a bend at one end only, so it
      can lend its full length). A corner whose clamped radius would still fall
      below its inversion floor (see :func:`_inversion_floor`, from
      ``lateral_half``/``up_half``; ``min_radius`` is an absolute lower bound) is
      left *sharp* and, when a ``warnings`` list is supplied, recorded as a
      :class:`RunWarning` naming the spot and a respacing fix. Pair with
      :func:`_collapse_short_legs` so such corners are rare.
    * **strict**: every bend uses the full fixed ``radius`` (a catalog value); no
      clamping, no sharp fallback. If two routed points sit too close to fit that
      bend — the fixed tangent would overshoot the previous one — it raises
      :class:`RoutingError` naming ``run_name`` and the exact offending point
      sequence, so the user can respace the route rather than get warped geometry.

    This is the non-circular analogue of how :class:`ada.Pipe` builds its bent
    directrix — the box/channel profile is later swept along this curve
    (:class:`_SweptRun`), the way piping sweeps a disk."""
    from ada.geom.curves import ArcLine, Edge, IndexedPolyCurve

    pts = [ada.Point(*(float(c) for c in p)) for p in pts]
    if len(pts) < 2:
        return None
    tag = f"{run_name!r} " if run_name else ""

    # Inversion-proof floor (diagonal half-extent): a graceful corner rounds only
    # when its radius clears this; strict rejects a fixed radius below it.
    floor = _inversion_floor(lateral_half, up_half, min_radius)
    if strict and radius < floor - 1e-9:
        raise RoutingError(
            f"duct/cable-tray run {tag}has bend radius {radius:.4g} smaller than its profile "
            f"half-width {floor:.4g}; a bend that tight inverts the section — widen the radius"
        )

    segs = []
    cursor = pts[0]  # running start of the next straight edge
    for i in range(1, len(pts) - 1):
        p_prev, p_cur, p_next = tuple(pts[i - 1]), tuple(pts[i]), tuple(pts[i + 1])
        a, b = _v_sub(p_cur, p_prev), _v_sub(p_next, p_cur)
        la, lb = _v_norm(a), _v_norm(b)
        if la < 1e-9 or lb < 1e-9:
            continue
        ua, ub = _v_scale(a, 1.0 / la), _v_scale(b, 1.0 / lb)
        if _v_norm(_v_cross(ua, ub)) < 1e-9:  # collinear — no bend to fillet
            continue
        if strict:
            r = radius
        else:
            # Tangent budget per adjacent leg: half of an interior leg (shared with
            # the neighbouring bend), the WHOLE of a terminal leg (the port stub/cap
            # turns at one end only, so its full length is available). Using half on a
            # terminal leg needlessly starved the last bend and dropped it to a sharp
            # corner (e.g. a tray/duct turning into a short nozzle stub).
            avail_a = la if i == 1 else 0.5 * la
            avail_b = lb if i == len(pts) - 2 else 0.5 * lb
            r = min(radius, avail_a, avail_b)
            if r < max(floor, 1e-9):  # too tight to fillet without inverting — keep sharp + warn
                if warnings is not None:
                    v = tuple(round(float(c), 3) for c in p_cur)
                    warnings.append(
                        RunWarning(
                            v,
                            f"duct/cable-tray run {tag}has a bend at {v} too tight to round "
                            f"(only {min(avail_a, avail_b):.3g} m of straight beside it, needs {floor:.3g} m) "
                            f"— left as a sharp corner",
                            f"space the route so both legs at {v} are at least {2 * floor:.4g} m long, "
                            f"or use a smaller cross-section",
                        )
                    )
                p_curp = ada.Point(*p_cur)
                if _seg_len(cursor, p_curp) > 1e-9:
                    segs.append(Edge(cursor, p_curp))
                cursor = p_curp
                continue
        t1 = _v_sub(p_cur, _v_scale(ua, r))
        t2 = _v_add(p_cur, _v_scale(ub, r))
        t1p = ada.Point(*t1)
        # The bend's entry tangent point t1 must lie ahead of the previous bend's
        # exit (cursor) along this leg. If it doesn't, the two bends' fixed radii
        # overlap — the points are too close for a real fitting.
        overshoot = _v_dot(_v_sub(t1, tuple(cursor)), ua) < -1e-6
        if strict and overshoot:
            raise RoutingError(
                f"duct/cable-tray run {tag}cannot fit a {radius:.4g} m bend at "
                f"{tuple(round(c, 3) for c in p_cur)}: the leg from "
                f"{tuple(round(c, 3) for c in p_prev)} to {tuple(round(c, 3) for c in p_next)} is too "
                f"short (need at least {2 * radius:.4g} m of straight between bends). "
                f"Respace the run or reduce its bend radius."
            )
        perp = _v_sub(ub, _v_scale(ua, _v_dot(ub, ua)))
        pn = _v_norm(perp)
        if pn < 1e-9:
            continue
        centre = _v_add(t1, _v_scale(_v_scale(perp, 1.0 / pn), r))
        v1, v2 = _v_sub(t1, centre), _v_sub(t2, centre)
        bis = _v_add(v1, v2)
        bn = _v_norm(bis)
        if bn < 1e-9:
            continue
        mid = _v_add(centre, _v_scale(_v_scale(bis, 1.0 / bn), r))  # arc apex
        if _seg_len(cursor, t1p) > 1e-9:
            segs.append(Edge(cursor, t1p))
        segs.append(ArcLine(t1p, ada.Point(*mid), ada.Point(*t2)))
        cursor = ada.Point(*t2)
    if _seg_len(cursor, pts[-1]) > 1e-9:
        segs.append(Edge(cursor, pts[-1]))
    if not segs:
        return None
    return IndexedPolyCurve(segments=segs)


def _slerp_lateral(axis, v_from, v_to, f):
    """Rotate the lateral ``v_from`` a fraction ``f`` of the way toward ``v_to``,
    turning ABOUT ``axis`` (the riser direction) rather than linearly blending.

    Linearly interpolating two laterals and renormalising collapses through zero
    when they are (near-)anti-parallel — a riser that reverses the horizontal run
    direction has ``left=(0,1,0)`` / ``right=(0,-1,0)``, so the lerp hits ``0`` at
    the midpoint and the frame FLIPS 180° in one station (the tray visibly twists
    and flips halfway up). Rotating about the riser axis instead sweeps the lateral
    smoothly (through the sideways orientation) so the unavoidable 180° twist is
    distributed evenly up the riser. Anti-parallel endpoints have an ambiguous
    turn direction (their cross product is ~0); we pick the positive sense so the
    twist is deterministic."""
    import numpy as np

    a = np.asarray(axis, dtype=float)
    na = float(np.linalg.norm(a))
    if na < 1e-9:
        return None
    a = a / na
    vf = np.asarray(v_from, dtype=float)
    vt = np.asarray(v_to, dtype=float)
    vf = vf - float(np.dot(vf, a)) * a
    vt = vt - float(np.dot(vt, a)) * a
    nf, nt = float(np.linalg.norm(vf)), float(np.linalg.norm(vt))
    if nf < 1e-9 or nt < 1e-9:
        return None
    vf, vt = vf / nf, vt / nt
    ang = float(np.arccos(float(np.clip(np.dot(vf, vt), -1.0, 1.0))))
    if ang < 1e-6:
        return vf
    sgn = float(np.dot(np.cross(vf, vt), a))
    direction = 1.0 if (sgn >= 0.0 or ang > np.pi - 1e-3) else -1.0
    theta = direction * ang * f
    # Rodrigues rotation of vf about a by theta.
    return vf * np.cos(theta) + np.cross(a, vf) * np.sin(theta) + a * float(np.dot(a, vf)) * (1.0 - np.cos(theta))


def _level_frames(pts, up):
    """Gravity-aligned per-station frames along a 3D polyline. Returns
    ``(dir_x, dir_y)`` as ``(N, 3)`` arrays — ``dir_x`` the profile's local +x
    (lateral, width) and ``dir_y`` its local +y (up, height).

    A duct/cable tray is gravity-oriented: on every *horizontal* run its opening
    must face straight up (+Z), never sideways. So on any clearly non-vertical
    station the lateral is ``dir_x = tangent x up``, which makes
    ``dir_y = dir_x x tangent`` = ``up`` projected perpendicular to the tangent,
    i.e. exactly +Z on a level leg.

    Through a (near-)vertical riser ``tangent x up`` collapses and its direction
    turns to noise, so the level rule can't set the frame there. A riser can also
    join two horizontal runs heading in *perpendicular* directions — the tray
    genuinely has to rotate 90 deg along it (a twist fitting). We therefore
    INTERPOLATE the lateral across each vertical band, from the level frame
    entering it to the level frame leaving it, spreading that rotation smoothly up
    the riser instead of snapping it at one station. A band with no level frame on
    one side (the run starts/ends vertical) carries the other side's frame; a fully
    vertical run seeds an arbitrary perpendicular.

    This replaces a purely rotation-minimising transport, which — though smooth —
    drifts the up axis off +Z after any climb and tilts every following horizontal
    tray onto its side."""
    import numpy as np

    pts = np.asarray(pts, dtype=float)
    n = len(pts)
    up_ref = np.asarray(up, dtype=float)
    up_ref = up_ref / (np.linalg.norm(up_ref) or 1.0)
    t = np.gradient(pts, axis=0)
    tn = np.linalg.norm(t, axis=1, keepdims=True)
    t = t / np.where(tn < 1e-12, 1.0, tn)

    # |t x up| = sin(angle of the tangent from vertical). Above this band the level
    # lateral is well-conditioned; within it the lateral is set by interpolation
    # (below) so a riser's twist is distributed, not snapped.
    VERTICAL_BAND = 0.25  # ~14 deg from vertical
    dir_x: list = [None] * n
    for i in range(n):
        lat = np.cross(t[i], up_ref)
        if np.linalg.norm(lat) >= VERTICAL_BAND:
            dir_x[i] = lat / np.linalg.norm(lat)

    def _ortho(v, i):
        v = v - float(np.dot(v, t[i])) * t[i]
        nrm = np.linalg.norm(v)
        return v / nrm if nrm > 1e-9 else None

    i = 0
    while i < n:
        if dir_x[i] is not None:
            i += 1
            continue
        lo = i
        while i < n and dir_x[i] is None:  # the vertical-band gap [lo, i)
            i += 1
        left = dir_x[lo - 1] if lo > 0 else None
        right = dir_x[i] if i < n else None
        span = i - lo
        # Rotate the lateral about the riser axis (mean band tangent) so an
        # anti-parallel left/right distributes its 180° twist smoothly instead of
        # flipping at the midpoint (see _slerp_lateral).
        band_axis = t[lo:i].mean(axis=0) if span > 0 else t[lo]
        for k, j in enumerate(range(lo, span + lo)):
            if left is not None and right is not None:
                f = (k + 1.0) / (span + 1.0)  # rotate the twist along the riser
                sl = _slerp_lateral(band_axis, left, right, f)
                v = _ortho(sl, j) if sl is not None else None
                if v is None:
                    v = _ortho((1.0 - f) * left + f * right, j) or _ortho(left, j)
            elif left is not None:
                v = _ortho(left, j)
            elif right is not None:
                v = _ortho(right, j)
            else:  # whole run vertical: seed an arbitrary perpendicular
                seed = np.array([1.0, 0.0, 0.0]) if abs(t[j, 0]) < 0.9 else np.array([0.0, 1.0, 0.0])
                v = _ortho(seed, j)
            if v is None:
                v = left if left is not None else np.array([1.0, 0.0, 0.0])
            dir_x[j] = v

    dir_x = np.asarray(dir_x, dtype=float)
    dir_y = np.cross(dir_x, t)  # up = dir_x x tangent (+Z projected on a level leg)
    return dir_x, dir_y


def _run_segment_frames(segments, up=(0.0, 0.0, 1.0)):
    """Per-segment ``(origins, dir_x, dir_y)`` frame slices for the segments of a
    routed run's directrix, framed CONTINUOUSLY across the whole run.

    Each segment (a straight ``Edge`` or an arc-fillet ``ArcLine``) is sampled into
    its own stations, the stations are concatenated into one global polyline
    (sharing the coincident segment joins), framed once with :func:`_level_frames`,
    then split back per segment. Adjacent segments therefore share the join
    station's frame exactly, so their swept solids meet without the 90 deg twist
    that framing each segment in isolation produces at an out-of-plane bend."""
    import numpy as np

    from ada.cadit.ngeom.serialize import _sample_arc
    from ada.geom.curves import ArcLine

    seg_pts = []
    for seg in segments:
        if isinstance(seg, ArcLine):
            seg_pts.append([tuple(float(c) for c in p) for p in _sample_arc(seg.start, seg.midpoint, seg.end)])
        else:  # Edge / straight: densify so a twist crossing it (a riser joining two
            # perpendicular runs) distributes finely and the sweep follows it, rather
            # than jumping across a 2-station span.
            s0 = tuple(float(c) for c in seg.start)
            s1 = tuple(float(c) for c in seg.end)
            steps = max(1, int(_seg_len(ada.Point(*s0), ada.Point(*s1)) / 0.1))
            seg_pts.append([tuple(s0[k] + (s1[k] - s0[k]) * (m / steps) for k in range(3)) for m in range(steps + 1)])

    glob: list[tuple] = []
    ranges: list[tuple[int, int]] = []
    for pts in seg_pts:
        if glob and _seg_len(ada.Point(*glob[-1]), ada.Point(*pts[0])) < 1e-9:
            start = len(glob) - 1  # reuse the shared join station
            glob.extend(pts[1:])
        else:
            start = len(glob)
            glob.extend(pts)
        ranges.append((start, len(glob) - 1))

    dir_x, dir_y = _level_frames(glob, up)
    origins = np.asarray(glob, dtype=float)
    return [(origins[a : b + 1], dir_x[a : b + 1], dir_y[a : b + 1]) for a, b in ranges]


def _rotate_profile_90(profile):
    """A copy of a swept-area profile rotated a quarter turn in its local plane
    ((x, y) -> (-y, x)), so an open UNP channel (which opens along its local +x)
    ends up opening along local +y — the sweep's "up" — i.e. an upward-opening
    cable tray. Only ``IndexedPolyCurve`` outlines are rotated (box/channel
    sections); anything else is returned unchanged."""
    from ada.geom.curves import ArcLine, Edge, IndexedPolyCurve
    from ada.geom.surfaces import ArbitraryProfileDef

    def _rp(p):
        return ada.Point(-float(p[1]), float(p[0]))

    def _rot(curve):
        if not isinstance(curve, IndexedPolyCurve):
            return curve
        new_segs = []
        for s in curve.segments:
            if isinstance(s, Edge):
                new_segs.append(Edge(_rp(s.start), _rp(s.end)))
            elif isinstance(s, ArcLine):
                new_segs.append(ArcLine(_rp(s.start), _rp(s.midpoint), _rp(s.end)))
            else:
                new_segs.append(s)
        return IndexedPolyCurve(segments=new_segs)

    return ArbitraryProfileDef(
        profile_type=profile.profile_type,
        outer_curve=_rot(profile.outer_curve),
        inner_curves=[_rot(c) for c in (profile.inner_curves or [])],
    )


class _SweptRun(ada.BeamCurved):
    """A duct or cable-tray run swept along its routed 3D curve — a fixed-reference
    sweep of the box/channel profile, the non-circular analogue of how a
    :class:`ada.Pipe` sweeps a disk along its directrix. ``open_channel`` rotates
    the profile a quarter turn so an open UNP tray opens upward (+Z) rather than
    sideways.

    Renders through the NGEOM stream (adacpp libtess2), which frames the swept
    profile upright along any directrix; the OCC path frames it too where
    available.

    ``frames`` is an optional precomputed ``(origins, dir_x, dir_y)`` slice — the
    portion of a run's globally parallel-transported frame belonging to this
    segment — so a run split into many ``_SweptRun`` pieces stays frame-continuous
    across its bends (see :func:`_run_segment_frames`)."""

    def __init__(self, name, n1, n2, curve3d, sec, open_channel: bool = False, metadata=None, frames=None):
        super().__init__(name, n1, n2, curve3d, sec, up=(0.0, 0.0, 1.0), metadata=metadata)
        self._open_channel = open_channel
        self._frames = frames

    def solid_geom(self):
        geom = super().solid_geom()
        if self._open_channel:
            geom.geometry.swept_area = _rotate_profile_90(geom.geometry.swept_area)
        if self._frames is not None:
            geom.geometry.precomputed_frames = self._frames
        return geom


def system_route_to_geometry(system: System, name: str | None = None, grid: CellGrid | None = None) -> list:
    """Turn ``system.routed_path`` into realistic adapy geometry appended to
    ``system.route_geometry``. Each service gets a cross-section that reads as
    itself rather than a generic pipe:

    * piping → a round ``PIPE`` tube with elbows (one :class:`ada.Pipe`),
    * ducting → a rectangular ``BOX`` duct swept along its curved run,
    * cable/electrical → an open ``UNP`` cable tray swept along its curved run.

    Every service is a solid swept along its routed 3D curve: piping reuses
    :class:`ada.Pipe` (a disk swept with revolved elbows); ducts and cable trays
    are a :class:`_SweptRun` — a fixed-reference sweep of the box/channel profile
    along an :class:`~ada.geom.curves.IndexedPolyCurve` directrix whose corners
    are filleted into arcs, the non-circular analogue of a pipe. The IFC entity
    class still follows the service via ``segment_ifc_class`` metadata.

    Swept runs are routed feasible-by-construction by the turn-constrained planner
    (:func:`astar_route_constrained`), so their path is already taut with well-spaced
    bends — the ``grid`` taut-pull (:func:`_space_bends`) is skipped for them and the
    orthogonal path goes straight to the arc-filleted directrix."""
    from ada.api.systems.base import CableSystem, DuctSystem, PipingSystem

    if system.routed_path is None:
        raise RoutingError(f"system {system.name!r} has no routed path; call route_system first")

    # Bend-artifact warnings for this run are recomputed from scratch each call.
    system.route_warnings = []
    name = name if name is not None else f"{system.name}_route"
    path = system.routed_path
    # A graceful swept run comes from the turn-constrained planner; a strict one is
    # still routed by free A* and taut-pulled, then its fixed-radius directrix raises
    # if the layout can't host the bend.
    swept_system = isinstance(system, (CableSystem, DuctSystem)) and not bool(getattr(system, "strict", False))
    # Box/channel runs follow an orthogonal path — square off any diagonal hop the
    # routing left so a tray/duct never runs on a slant. A swept run from the
    # turn-constrained planner is already feasible/taut (skip _space_bends); anything
    # else with a grid is pulled taut in the clear corridor for few, well-separated
    # bends before the directrix is filleted.
    ortho = _orthogonalize_polyline(path)
    if grid is not None and not swept_system:
        ortho = _space_bends(ortho, grid)

    def _swept(sec, *, open_channel: bool, seg_class: str):
        # Emit ONE swept solid per directrix segment — a straight ``Edge`` or a
        # curved ``ArcLine`` bend — each named ``<name>_<i>`` so every straight
        # leg and fitting is an individually selectable object in the viewer (the
        # way a pipe's segments are), rather than one monolithic run.
        from ada.geom.curves import IndexedPolyCurve

        # The swept profile's true lateral/up half-extents and the run's fixed bend
        # radius — shared with the turn-constrained router via swept_bend_params so
        # the corner budget the planner guarantees matches the fillet built here.
        bend_r, _floor, lat_half, up_half = swept_bend_params(system)
        strict = bool(getattr(system, "strict", False))
        if strict:
            # Real products only bend on their fixed radius: route as-given, keep
            # every fitting regular, and raise (naming the points) if it can't fit.
            directrix = _polyline_to_directrix(
                ortho, bend_r, lateral_half=lat_half, up_half=up_half, strict=True, run_name=name
            )
        else:
            # Drop sub-profile micro-jogs (grid-remainder / off-grid nozzle caps) so
            # no corner fillets into a self-intersecting tiny arc, then fillet the
            # clean run (sharp fallback only where a corner is still too tight).
            clean = _collapse_short_legs(ortho, max(lat_half, up_half))
            directrix = _polyline_to_directrix(
                clean, bend_r, lateral_half=lat_half, up_half=up_half, run_name=name, warnings=system.route_warnings
            )
        if directrix is None:
            return
        # Frame the whole run once (parallel transport), then hand each segment its
        # slice so the individually-swept pieces meet without a twist at the bends.
        frames = _run_segment_frames(directrix.segments, up=(0.0, 0.0, 1.0))
        for i, (seg, seg_frames) in enumerate(zip(directrix.segments, frames)):
            system.route_geometry.append(
                _SweptRun(
                    f"{name}_{i}",
                    ada.Point(*seg.start),
                    ada.Point(*seg.end),
                    IndexedPolyCurve(segments=[seg]),
                    sec,
                    open_channel=open_channel,
                    metadata={"segment_ifc_class": seg_class},
                    frames=seg_frames,
                )
            )

    if isinstance(system, DuctSystem):
        w, h, t = system.duct_width, system.duct_height, system.wall
        sec = ada.Section(f"{name}_sec", "BOX", h=h, w_top=w, w_btn=w, t_w=t, t_ftop=t, t_fbtn=t)
        _swept(sec, open_channel=False, seg_class="IfcDuctSegment")
    elif isinstance(system, CableSystem):  # covers ElectricalSystem
        w, h, t = system.tray_width, system.tray_height, system.wall
        # Channel web = the tray bottom (its width), flanges = the shallow sides
        # (its height); _SweptRun(open_channel=True) rotates the profile so the
        # tray opens upward.
        sec = ada.Section(f"{name}_sec", "UNP", h=w, w_top=h, w_btn=h, t_w=t, t_ftop=t, t_fbtn=t)
        _swept(sec, open_channel=True, seg_class="IfcCableSegment")
    elif isinstance(system, PipingSystem):
        sec = ada.Section(f"{name}_sec", "PIPE", r=system.pipe_radius, wt=system.pipe_wt)
        system.route_geometry.append(ada.Pipe(name, path, sec, metadata={"segment_ifc_class": "IfcPipeSegment"}))
    else:
        sec = ada.Section(f"{name}_sec", "PIPE", r=0.02, wt=2e-3)
        system.route_geometry.append(ada.Pipe(name, path, sec, metadata={"segment_ifc_class": "IfcPipeSegment"}))
    return system.route_geometry


class RoutingBlueprintBase(BlueprintBase):
    """Blueprint scaffold that routes a set of systems through the cell
    structure. Subclasses override ``rules_for`` (per-system rules) and/or
    ``build_routing_grid`` (custom lattice). Pass a ``DesignRules`` to fully
    override the routing/modelling callables; otherwise the per-system
    ``rules_for`` feeds the default planner. This scaffold is route-only (no
    penetration modelling) — use ``ada.topology.run_design`` directly for that."""

    def __init__(self, systems: list[System] | None = None, design_rules: object | None = None):
        super().__init__()
        self.systems: list[System] = systems if systems is not None else []
        self.design_rules = design_rules

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
        from ada.topology.design_rules import DesignRules, run_design

        self.output_part = ada.Part("Systems")
        grid = self.build_routing_grid()
        rules = self.design_rules if self.design_rules is not None else DesignRules(rules_for=self.rules_for)
        result = run_design(self.systems, cell_graph=self.builder.cell_graph, grid=grid, members=[], rules=rules)
        for system in self.systems:
            geoms = result.route_geometry.get(system.name, [])
            self.add_to_area(system.name, ada.Part(f"{system.name}_geom") / geoms)
        self.load_parts_from_area_map()
        return self.output_part
