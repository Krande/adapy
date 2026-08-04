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
):
    """Build an ``IndexedPolyCurve`` directrix from an (orthogonal) polyline,
    filleting each interior corner into an ``ArcLine`` of ``radius`` so a swept
    duct/tray turns on a real circular bend. Straight spans stay ``Edge``
    segments. Returns ``None`` for a degenerate (< 2 point) path.

    Two modes, matching how real duct/cable-tray products behave — they come as
    straight sections plus standard-radius bends only, so a bend that can't fit is
    an error, not something to deform:

    * **graceful** (default): the radius is clamped to half of each adjacent
      segment so nearby bends never eat into each other; a corner whose clamped
      radius would fall below its inversion floor (see :func:`_inversion_floor`,
      from ``lateral_half``/``up_half``; ``min_radius`` is an absolute lower bound)
      is left *sharp*. Pair with
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
            r = min(radius, 0.5 * la, 0.5 * lb)
            if r < max(floor, 1e-9):  # too tight to fillet without inverting — keep sharp
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


def _level_frames(pts, up):
    """Gravity-aligned per-station frames along a 3D polyline. Returns
    ``(dir_x, dir_y)`` as ``(N, 3)`` arrays — ``dir_x`` the profile's local +x
    (lateral, width) and ``dir_y`` its local +y (up, height).

    A duct/cable tray is gravity-oriented: on every *horizontal* run its opening
    must face straight up (+Z), never sideways. So the up axis is kept as close to
    world ``up`` (+Z) as the tangent allows — ``dir_x = tangent x up`` gives
    ``dir_y = dir_x x tangent`` = ``up`` projected perpendicular to the tangent,
    i.e. exactly +Z on a level leg. Through a (near-)vertical section, where
    ``tangent x up`` vanishes, the lateral is carried (parallel-transported,
    re-orthogonalised) from the previous station so the frame stays continuous
    across the riser rather than snapping.

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

    dir_x = np.zeros((n, 3))
    prev = None
    for i in range(n):
        lat = np.cross(t[i], up_ref)  # level lateral; = 0 only when the tangent is vertical
        if np.linalg.norm(lat) < 1e-6:  # (near-)vertical: carry the frame through the riser
            if prev is not None:
                lat = prev - float(np.dot(prev, t[i])) * t[i]  # re-orthogonalise the carried lateral
            if np.linalg.norm(lat) < 1e-9:  # run *starts* vertical: seed an arbitrary perpendicular
                a = np.array([1.0, 0.0, 0.0]) if abs(t[i, 0]) < 0.9 else np.array([0.0, 1.0, 0.0])
                lat = np.cross(t[i], a)
        dir_x[i] = lat / (np.linalg.norm(lat) or 1.0)
        prev = dir_x[i]
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
        else:  # Edge / straight
            seg_pts.append([tuple(float(c) for c in seg.start), tuple(float(c) for c in seg.end)])

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


def system_route_to_geometry(system: System, name: str | None = None) -> list:
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
    class still follows the service via ``segment_ifc_class`` metadata."""
    from ada.api.systems.base import CableSystem, DuctSystem, PipingSystem

    if system.routed_path is None:
        raise RoutingError(f"system {system.name!r} has no routed path; call route_system first")

    name = name if name is not None else f"{system.name}_route"
    path = system.routed_path
    # Box/channel runs follow an orthogonal path — square off any diagonal hop the
    # routing left so a tray/duct never runs on a slant — then a directrix with
    # arc-filleted corners is swept, giving real curved fittings at the bends.
    ortho = _orthogonalize_polyline(path)

    def _swept(sec, *, open_channel: bool, seg_class: str):
        # Emit ONE swept solid per directrix segment — a straight ``Edge`` or a
        # curved ``ArcLine`` bend — each named ``<name>_<i>`` so every straight
        # leg and fitting is an individually selectable object in the viewer (the
        # way a pipe's segments are), rather than one monolithic run.
        from ada.geom.curves import IndexedPolyCurve

        # The swept profile's true lateral (width) and up (height) half-extents.
        # An open cable tray rotates the profile a quarter turn (see
        # _rotate_profile_90), so its width comes from the section's h and its
        # height from w_top; a closed duct keeps them as authored.
        if open_channel:
            lat_half, up_half = 0.5 * float(sec.h or 0.0), 0.5 * float(sec.w_top or 0.0)
        else:
            lat_half, up_half = 0.5 * float(sec.w_top or 0.0), 0.5 * float(sec.h or 0.0)
        section_r = 2.0 * max(lat_half, up_half)  # ~1x the widest dimension
        strict = bool(getattr(system, "strict", False))
        # A catalog bend radius if the system carries one, else ~1x the section.
        cfg_r = getattr(system, "bend_radius", None)
        bend_r = float(cfg_r) if cfg_r else section_r
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
            directrix = _polyline_to_directrix(clean, bend_r, lateral_half=lat_half, up_half=up_half)
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
