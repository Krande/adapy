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
        # Pathfind from the nozzle stub (one cell along the port normal) so the
        # grid route leaves the port in the direction it faces — but only when
        # that node is on-grid and the rules allow it; otherwise fall back to
        # snapping the port itself (keeps rule-forbidden levels off-limits).
        if stub is not None:
            idx = nearest_index(grid, *stub)
            if all(0 <= idx[i] < dims[i] for i in range(3)) and rules.is_allowed(idx, grid):
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


def _polyline_to_directrix(pts: list[ada.Point], radius: float):
    """Build an ``IndexedPolyCurve`` directrix from an (orthogonal) polyline,
    filleting each interior corner into an ``ArcLine`` of ``radius`` so a swept
    duct/tray turns on a real circular bend. The radius is clamped to half of
    each adjacent segment so two nearby bends never eat into each other; straight
    spans stay ``Edge`` segments. Returns ``None`` for a degenerate (< 2 point)
    path.

    This is the non-circular analogue of how :class:`ada.Pipe` builds its bent
    directrix — the box/channel profile is later swept along this curve
    (:class:`_SweptRun`), the way piping sweeps a disk."""
    from ada.geom.curves import ArcLine, Edge, IndexedPolyCurve

    pts = [ada.Point(*(float(c) for c in p)) for p in pts]
    if len(pts) < 2:
        return None

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
        r = min(radius, 0.5 * la, 0.5 * lb)
        if r < 1e-9:
            continue
        t1 = _v_sub(p_cur, _v_scale(ua, r))
        t2 = _v_add(p_cur, _v_scale(ub, r))
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
        t1p = ada.Point(*t1)
        if _seg_len(cursor, t1p) > 1e-9:
            segs.append(Edge(cursor, t1p))
        segs.append(ArcLine(t1p, ada.Point(*mid), ada.Point(*t2)))
        cursor = ada.Point(*t2)
    if _seg_len(cursor, pts[-1]) > 1e-9:
        segs.append(Edge(cursor, pts[-1]))
    if not segs:
        return None
    return IndexedPolyCurve(segments=segs)


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
    available."""

    def __init__(self, name, n1, n2, curve3d, sec, open_channel: bool = False, metadata=None):
        super().__init__(name, n1, n2, curve3d, sec, up=(0.0, 0.0, 1.0), metadata=metadata)
        self._open_channel = open_channel

    def solid_geom(self):
        geom = super().solid_geom()
        if self._open_channel:
            geom.geometry.swept_area = _rotate_profile_90(geom.geometry.swept_area)
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
        bend_r = max(float(sec.w_top or 0.0), float(sec.h or 0.0))
        directrix = _polyline_to_directrix(ortho, bend_r)
        if directrix is None:
            return
        system.route_geometry.append(
            _SweptRun(
                name,
                ada.Point(*ortho[0]),
                ada.Point(*ortho[-1]),
                directrix,
                sec,
                open_channel=open_channel,
                metadata={"segment_ifc_class": seg_class},
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
