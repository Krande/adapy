"""Propose the *minimum* set of equipment relocations that make a procedural
model's runs route cleanly.

When a procedural model is compiled (:mod:`ada.topo_model.compile`), some system
runs may fail to route cleanly: the constrained router gives up and the run is
skipped, a bend is left sharp because the corridor was too cramped, or the swept
body detours back over itself. Rather than silently dropping those runs, this
engine searches for a small number of axis-aligned equipment moves that clear the
problems, and returns them as *proposals* for a human to accept — relocations are
NEVER applied automatically.

The search is greedy and minimises the move count: each iteration commits the
single equipment move that resolves the most outstanding problems (without
introducing new ones), so the returned proposal list is minimal by construction
(at most one proposal per moved piece of equipment).

The routing model is built exactly like :func:`ada.topo_model.compile._build_systems`
(same routing grid, equipment occupancy and system wiring), but without the
structural blueprint / cell graph: routing feasibility — can A* find a path, is a
bend too tight, does the body fold over itself — does not depend on the built
walls, so the engine skips the expensive OCC blueprint build and only *routes*.
"""

from __future__ import annotations

from typing import Iterable

import ada
from ada.topology.entities import TopoEquipment, TopoSpace

from .compile import (
    _equipment_to_object,
    _occupy_equipment,
    _routing_grid,
    _wire_systems,
)

__all__ = ["propose_relocations", "run_self_collides"]


# One grid pitch, matching the default spacing of
# :func:`ada.topo_model.compile._routing_grid` — candidate moves are whole
# multiples of it so a shifted piece of equipment still lands on the lattice.
_GRID_PITCH = 0.5
# Axis-aligned shift magnitudes (in grid pitches) tried per equipment, in X and Y.
_SHIFT_STEPS = (1, 2, 3)
# Hard cap on the number of candidate re-routes across the whole search, so a
# pathological model can't blow up. When hit, the search truncates (logged).
_MAX_CANDIDATE_ROUTES = 240
_TOL = 1e-6


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return _dot(a, a) ** 0.5


def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _segment_distance(p1, q1, p2, q2) -> float:
    """Minimum distance between the 3D segments ``[p1, q1]`` and ``[p2, q2]``
    (Ericson, *Real-Time Collision Detection*, closest point between two
    segments). Used to detect a run folding back over itself."""
    d1 = _sub(q1, p1)
    d2 = _sub(q2, p2)
    r = _sub(p1, p2)
    a = _dot(d1, d1)
    e = _dot(d2, d2)
    f = _dot(d2, r)
    eps = 1e-12
    if a <= eps and e <= eps:  # both segments are points
        return _norm(_sub(p1, p2))
    if a <= eps:  # first segment is a point
        s = 0.0
        t = _clamp01(f / e)
    else:
        c = _dot(d1, r)
        if e <= eps:  # second segment is a point
            t = 0.0
            s = _clamp01(-c / a)
        else:
            b = _dot(d1, d2)
            denom = a * e - b * b
            s = _clamp01((b * f - c * e) / denom) if denom > eps else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = _clamp01(-c / a)
            elif t > 1.0:
                t = 1.0
                s = _clamp01((b - c) / a)
    c1 = _add(p1, _scale(d1, s))
    c2 = _add(p2, _scale(d2, t))
    return _norm(_sub(c1, c2))


def run_self_collides(polyline: Iterable, half_extent: float) -> bool:
    """Does a routed run fold back over itself? True when two *non-adjacent*
    centreline segments of ``polyline`` pass within the run's full cross-section
    width (``2 * half_extent``) of each other — i.e. the swept body of one part of
    the run overlaps the body of another part (a duct/tray detour that doubles
    back). Adjacent segments (sharing a vertex) are skipped: they meet by
    construction and are handled by the bend fillet, not a collision."""
    pts = [(float(p[0]), float(p[1]), float(p[2])) for p in polyline]
    threshold = 2.0 * float(half_extent)
    if len(pts) < 4 or threshold <= 0.0:  # need >= 2 non-adjacent segments
        return False
    segs = list(zip(pts, pts[1:]))
    m = len(segs)
    for i in range(m):
        for j in range(i + 2, m):  # j = i+1 shares a vertex -> adjacent, skipped
            if _segment_distance(segs[i][0], segs[i][1], segs[j][0], segs[j][1]) < threshold:
                return True
    return False


def _aabb_overlap(lo_a, hi_a, lo_b, hi_b) -> bool:
    return all(lo_a[k] <= hi_b[k] + _TOL and lo_b[k] <= hi_a[k] + _TOL for k in range(3))


# --------------------------------------------------------------------------- #
# Model / routing
# --------------------------------------------------------------------------- #
def _origin(eq: TopoEquipment, pos: tuple[float, float]) -> list[float]:
    """World origin (base centre) of an equipment placed with its corner at
    ``pos`` — matching :func:`ada.topo_model.compile._equipment_to_object`."""
    return [pos[0] + eq.LX / 2.0, pos[1] + eq.LY / 2.0, float(eq.Z)]


def _build_equipment_map(
    equipments: list[TopoEquipment], positions: dict[str, tuple[float, float]], resolver
) -> dict[str, ada.Equipment]:
    """Compile each equipment entity (with its corner placed at ``positions``)
    into a placed object, keeping only the :class:`ada.Equipment` instances (the
    ones with ports that a system can connect to) — exactly the set
    :func:`ada.topo_model.compile.compile_procedural_doc` occupies on the grid."""
    equipment_map: dict[str, ada.Equipment] = {}
    for e in equipments:
        pos = positions.get(e.NAME)
        placed = e.model_copy(update={"X": pos[0], "Y": pos[1]}) if pos is not None else e
        obj = _equipment_to_object(placed, resolver)
        if isinstance(obj, ada.Equipment):
            equipment_map[obj.name] = obj
    return equipment_map


def _route_and_collect(
    specs: list[dict], equipment_map: dict[str, ada.Equipment], spaces: list[TopoSpace], design_rules
) -> set[str]:
    """Route every system over the (equipment-occupied) grid and return the set of
    system names that DON'T route cleanly:

    * **infeasible** — the router raised and the system was skipped
      (``run_design(..., skip_failed=True)`` records it in ``result.skipped``),
    * **cramped** — a bend was left sharp (``system.route_warnings`` non-empty),
    * **self-colliding** — the swept body folds back over itself
      (:func:`run_self_collides` on the planned polyline).

    Mirrors the routing half of :func:`ada.topo_model.compile._build_systems`
    (same grid, same clearance-inflated occupancy, same ``run_design`` call) but
    with no cell graph / penetrations — routing feasibility doesn't depend on the
    built walls."""
    from ada.topology import run_design
    from ada.topology.routing import run_half_extent

    systems = _wire_systems(specs, equipment_map)
    if not systems:
        return set()

    grid = _routing_grid(spaces, list(equipment_map.values()))
    clearance = max((run_half_extent(s) for s in systems), default=0.0)
    for eq in equipment_map.values():
        _occupy_equipment(grid, eq, clearance)

    result = run_design(
        systems,
        cell_graph=None,
        grid=grid,
        members=None,
        rules=design_rules,
        skip_failed=True,
        avoid_other_systems=True,
    )

    problems: set[str] = set(result.skipped)
    for system in systems:
        if system.name in problems:
            continue
        if getattr(system, "route_warnings", None):
            problems.add(system.name)
            continue
        plan = result.route_plans.get(system.name)
        if plan is not None and run_self_collides(plan.polyline, run_half_extent(system)):
            problems.add(system.name)
    return problems


def _system_endpoint_names(specs: list[dict], equipment_names: set[str]) -> dict[str, list[str]]:
    """Map each system name to the equipment (by name) it terminates on — only
    equipment that resolved to a movable :class:`ada.Equipment` (in
    ``equipment_names``). Site terminals are fixed and never candidates."""
    out: dict[str, list[str]] = {}
    for spec in specs:
        names: list[str] = []
        for conn in spec.get("CONNECTIONS") or []:
            eqn = conn.get("EQUIPMENT")
            if eqn and eqn in equipment_names and eqn not in names:
                names.append(eqn)
        out[spec.get("NAME")] = names
    return out


def _candidate_positions(
    name: str,
    positions: dict[str, tuple[float, float]],
    eq_by_name: dict[str, TopoEquipment],
    spaces: list[TopoSpace],
    clearance: float,
) -> list[tuple[tuple[float, float], float]]:
    """Axis-aligned shifts of the named equipment by ``±1..±3`` grid pitches in X
    and Y that (a) keep its footprint inside some space cell at its current Z and
    (b) don't overlap another equipment's clearance-inflated box. Returns a list
    of ``(new_corner_xy, displacement)`` sorted by displacement (nearest first)."""
    eq = eq_by_name[name]
    x0, y0 = positions[name]
    z, lx, ly, lz = float(eq.Z), float(eq.LX), float(eq.LY), float(eq.LZ)
    out: list[tuple[tuple[float, float], float]] = []
    for axis in (0, 1):
        for step in _SHIFT_STEPS:
            for sign in (1, -1):
                delta = sign * step * _GRID_PITCH
                nx = x0 + (delta if axis == 0 else 0.0)
                ny = y0 + (delta if axis == 1 else 0.0)
                box_lo = (nx, ny, z)
                box_hi = (nx + lx, ny + ly, z + lz)
                if not _inside_a_space(box_lo, box_hi, spaces):
                    continue
                if _overlaps_other(name, box_lo, box_hi, positions, eq_by_name, clearance):
                    continue
                out.append(((nx, ny), abs(delta)))
    out.sort(key=lambda t: t[1])
    return out


def _inside_a_space(box_lo, box_hi, spaces: list[TopoSpace]) -> bool:
    """Is the equipment's plan footprint fully inside some space cell that spans
    its base Z? (A move must keep it in a room, not push it into the void.)"""
    z = box_lo[2]
    for s in spaces:
        if (
            s.X - _TOL <= box_lo[0]
            and box_hi[0] <= s.X + s.DX + _TOL
            and s.Y - _TOL <= box_lo[1]
            and box_hi[1] <= s.Y + s.DY + _TOL
            and s.Z - _TOL <= z <= s.Z + s.DZ + _TOL
        ):
            return True
    return False


def _overlaps_other(
    name: str,
    box_lo,
    box_hi,
    positions: dict[str, tuple[float, float]],
    eq_by_name: dict[str, TopoEquipment],
    clearance: float,
) -> bool:
    c = float(clearance)
    for oname, opos in positions.items():
        if oname == name:
            continue
        oe = eq_by_name.get(oname)
        if oe is None:
            continue
        olo = (opos[0] - c, opos[1] - c, float(oe.Z) - c)
        ohi = (opos[0] + oe.LX + c, opos[1] + oe.LY + c, float(oe.Z) + oe.LZ + c)
        if _aabb_overlap(box_lo, box_hi, olo, ohi):
            return True
    return False


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def propose_relocations(
    doc: dict,
    *,
    equipment_resolver=None,
    design_rules=None,
    max_moves: int = 4,
) -> dict:
    """Propose the minimum set of equipment relocations that make ``doc``'s runs
    route cleanly.

    ``doc`` is the procedural cell-model document (see
    :mod:`ada.topo_model.compile`). ``equipment_resolver`` resolves a placed
    equipment's catalog slug to its definition (as in the compiler); when omitted,
    only the built-in archetypes are available. ``design_rules`` overrides the
    routing/penetration ruleset; when omitted the document's ``design_rules`` slug
    is resolved (falling back to the standard ruleset), matching the compiler.
    ``max_moves`` caps the number of relocations proposed.

    Returns::

        {
            "proposals": [
                {"equipment": name, "from": [x, y, z], "to": [x, y, z],
                 "reason": str, "fixes": [system names]},
                ...
            ],
            "unresolved": [system names still not routing cleanly],
            "baseline_problems": int,   # problem count before any move
        }

    Relocations are proposals only — the caller decides whether to apply them.
    """
    from ada.config import logger

    if design_rules is None:
        from .design_rulesets import resolve_design_rules

        design_rules = resolve_design_rules(doc.get("design_rules"))

    spaces = [TopoSpace(**s) for s in doc.get("spaces", [])]
    equipments = [TopoEquipment(**e) for e in doc.get("equipments", [])]
    specs = doc.get("systems") or []

    empty = {"proposals": [], "unresolved": [], "baseline_problems": 0}
    if not spaces or not equipments or not specs:
        return empty

    eq_by_name = {e.NAME: e for e in equipments}
    positions = {e.NAME: (float(e.X), float(e.Y)) for e in equipments}
    original_positions = dict(positions)

    # Baseline route: which systems don't route cleanly today? Each equipment map
    # is a fresh set of Equipment/Port objects and may be wired exactly once (the
    # first wiring consumes each port's ``connected_system`` slot), so the
    # clearance probe and the route each get their own map.
    from ada.topology.routing import run_half_extent

    if not _build_equipment_map(equipments, positions, equipment_resolver):
        return empty
    clearance = max(
        (
            run_half_extent(s)
            for s in _wire_systems(specs, _build_equipment_map(equipments, positions, equipment_resolver))
        ),
        default=0.0,
    )

    equipment_map = _build_equipment_map(equipments, positions, equipment_resolver)
    current_problems = _route_and_collect(specs, equipment_map, spaces, design_rules)
    baseline_problems = len(current_problems)
    if not current_problems:
        return {"proposals": [], "unresolved": [], "baseline_problems": 0}

    movable = set(equipment_map)  # only Equipment (ported) pieces can be endpoints
    endpoints = _system_endpoint_names(specs, movable)

    proposals: list[dict] = []
    moved: set[str] = set()
    budget = _MAX_CANDIDATE_ROUTES
    truncated = False

    for _ in range(max_moves):
        if not current_problems:
            break
        # Only equipment that is an endpoint of a still-problematic system, and
        # not already moved, is worth trying.
        candidate_eq = sorted(
            {name for pname in current_problems for name in endpoints.get(pname, []) if name not in moved}
        )
        if not candidate_eq:
            break

        best: dict | None = None
        for eqname in candidate_eq:
            if budget <= 0:
                truncated = True
                break
            for new_pos, disp in _candidate_positions(eqname, positions, eq_by_name, spaces, clearance):
                if budget <= 0:
                    truncated = True
                    break
                budget -= 1
                trial = dict(positions)
                trial[eqname] = new_pos
                trial_map = _build_equipment_map(equipments, trial, equipment_resolver)
                new_problems = _route_and_collect(specs, trial_map, spaces, design_rules)
                removed = current_problems - new_problems
                introduced = new_problems - current_problems
                net = len(removed) - len(introduced)
                if net <= 0:  # a move that fixes nothing (or regresses) is not proposed
                    continue
                # Best = most net problems fixed, tie-break smallest displacement.
                key = (net, -disp)
                if best is None or key > best["key"]:
                    best = {
                        "key": key,
                        "eq": eqname,
                        "pos": new_pos,
                        "disp": disp,
                        "fixes": sorted(removed),
                        "new_problems": new_problems,
                    }
            if truncated:
                break

        if best is None:  # no single move improves anything — stop
            break

        eqname = best["eq"]
        eq = eq_by_name[eqname]
        from_origin = _origin(eq, original_positions[eqname])
        to_origin = _origin(eq, best["pos"])
        dxy = _sub((to_origin[0], to_origin[1], 0.0), (from_origin[0], from_origin[1], 0.0))
        axis = "X" if abs(dxy[0]) >= abs(dxy[1]) else "Y"
        shift = dxy[0] if axis == "X" else dxy[1]
        proposals.append(
            {
                "equipment": eqname,
                "from": [round(float(v), 4) for v in from_origin],
                "to": [round(float(v), 4) for v in to_origin],
                "reason": (f"Shift {eqname} {shift:+.2f} m along {axis} to clear " f"{', '.join(best['fixes'])}."),
                "fixes": best["fixes"],
            }
        )
        positions[eqname] = best["pos"]
        moved.add(eqname)
        current_problems = best["new_problems"]

    if truncated:
        logger.warning(
            "relocate: candidate search truncated at %d routes; %d problem(s) may remain unexplored",
            _MAX_CANDIDATE_ROUTES,
            len(current_problems),
        )
    if len(proposals) >= max_moves and current_problems:
        logger.info("relocate: reached max_moves=%d with %d problem(s) still open", max_moves, len(current_problems))

    return {
        "proposals": proposals,
        "unresolved": sorted(current_problems),
        "baseline_problems": baseline_problems,
    }
