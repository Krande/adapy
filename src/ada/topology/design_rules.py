"""Pluggable design rules for the two-phase procedural engine.

A *design rule* is a plain callable you hand to the engine — no subclassing. The
engine runs in two phases, and every rule is a function that **fully encompasses
its stage**:

* **Plan** (geometry-free, runs first over the whole cell complex): a
  ``plan_route`` callable turns ``(system, cell complex, grid)`` into a
  :class:`RoutePlan`, and a ``plan_penetration`` callable turns
  ``(system, routed path, penetrated members)`` into a list of
  :class:`Penetration` crossings. Planners see the ``CellGraph`` (the cell
  complex — cells + classified faces) and the routing ``CellGrid`` lattice, plus
  the penetrated members for penetration rules. They emit *data*, not geometry.
* **Model** (plan -> geometry): a ``model_route`` callable turns a
  :class:`RoutePlan` into adapy geometry, and a ``model_penetration`` callable
  turns a :class:`Penetration` into a detail part.

:class:`DesignRules` bundles the four callables; :func:`run_design` drives the
two phases in order (plan everything, then model everything) and returns a
:class:`DesignResult`. Defaults reproduce the built-in routing behaviour; the
built-in *penetration detail* standard lives in ``ada.topo_model`` (see
``standard_design_rules``) since it is an opinionated detail choice, so the core
default emits crossings but no detail geometry (``model_penetration=None``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import numpy as np

import ada
from ada.topology.graph import CellGraph, GraphFace
from ada.topology.grid import CellGrid
from ada.topology.routing import (
    RoutingError,
    RoutingRules,
    route_system,
    system_route_to_geometry,
)

if TYPE_CHECKING:
    from ada.api.systems.base import System

__all__ = [
    "Penetration",
    "find_face_crossings",
    "RoutePlanContext",
    "RoutePlan",
    "PenetrationPlanContext",
    "RoutePlanner",
    "RouteModeller",
    "PenetrationPlanner",
    "PenetrationModeller",
    "DesignRules",
    "DesignResult",
    "default_route_planner",
    "default_route_modeller",
    "default_penetration_planner",
    "run_design",
]


# --------------------------------------------------------------------------- #
# Penetration crossing (generic geometry — where a routed run crosses a face)
# --------------------------------------------------------------------------- #
@dataclass
class Penetration:
    """A point where a system's routed polyline crosses a (planar) member."""

    system: System
    point: ada.Point
    normal: ada.Direction
    face: GraphFace


def find_face_crossings(system: System, faces: list[GraphFace], tol: float = 1e-6) -> list[Penetration]:
    """Where does the system's routed polyline cross each (planar, axis-aligned
    bounded) face? Segment-plane intersection, kept when the hit lies within the
    face outline's bounding box."""
    out: list[Penetration] = []
    if not system.routed_path:
        return out
    for face in faces:
        pts = np.asarray([tuple(p) for p in face.get_points()], dtype=float)
        n = np.asarray(tuple(face.normal), dtype=float)
        p0 = pts[0]
        lo, hi = pts.min(axis=0) - tol, pts.max(axis=0) + tol
        for a, b in zip(system.routed_path[:-1], system.routed_path[1:]):
            a_ = np.asarray(tuple(a), dtype=float)
            b_ = np.asarray(tuple(b), dtype=float)
            denom = float(n.dot(b_ - a_))
            if abs(denom) < tol:
                continue  # segment parallel to the face plane
            t = float(n.dot(p0 - a_)) / denom
            if not (0.0 <= t <= 1.0):
                continue
            x = a_ + t * (b_ - a_)
            if np.all(x >= lo) and np.all(x <= hi):
                out.append(Penetration(system, ada.Point(*x), ada.Direction(*tuple(face.normal)), face))
    return out


# --------------------------------------------------------------------------- #
# Plan-phase contexts (what a planning rule sees — before any geometry exists)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RoutePlanContext:
    """Everything a routing planner sees: the system to route, the cell complex
    (``cell_graph`` — may be ``None`` for a raw-box model), the routing lattice,
    the per-move ``RoutingRules`` the default planner reads, and optional
    explicit endpoints (defaults: the system's first/last connected ports)."""

    system: System
    grid: CellGrid
    cell_graph: CellGraph | None = None
    rules: RoutingRules = field(default_factory=RoutingRules)
    start: object | None = None  # Port | None
    end: object | None = None  # Port | None


@dataclass
class RoutePlan:
    """The geometry-free result of the routing plan: the world polyline the run
    follows (and, when the planner records it, the node path through the grid)."""

    system: System
    polyline: list[ada.Point]
    node_path: list[tuple[int, int, int]] | None = None


@dataclass(frozen=True)
class PenetrationPlanContext:
    """Everything a penetration planner sees: the system, its routed polyline,
    the members it may penetrate (walls/floors from the cell complex) and the
    complex/grid for context."""

    system: System
    routed_path: list[ada.Point]
    members: list[GraphFace]
    cell_graph: CellGraph | None = None
    grid: CellGrid | None = None


# --------------------------------------------------------------------------- #
# Callable protocols (each fully encompasses its stage)
# --------------------------------------------------------------------------- #
RoutePlanner = Callable[[RoutePlanContext], "RoutePlan"]
RouteModeller = Callable[["RoutePlan", RoutePlanContext], list]
PenetrationPlanner = Callable[[PenetrationPlanContext], "list[Penetration]"]
PenetrationModeller = Callable[["Penetration", str], "ada.Part | None"]


# --------------------------------------------------------------------------- #
# Default rules (reproduce the built-in routing behaviour)
# --------------------------------------------------------------------------- #
def default_route_planner(ctx: RoutePlanContext) -> RoutePlan:
    """Default routing plan: 6-connected A* over the grid via ``route_system``
    (which also stashes ``system.routed_path`` for the penetration planner)."""
    polyline = route_system(ctx.system, ctx.grid, rules=ctx.rules, start=ctx.start, end=ctx.end)
    return RoutePlan(system=ctx.system, polyline=polyline)


def default_route_modeller(plan: RoutePlan, ctx: RoutePlanContext) -> list:
    """Default route geometry: piping -> ``ada.Pipe``; cable/duct -> a carrier
    pipe tagged with the system's IFC segment class (see
    ``system_route_to_geometry``)."""
    return system_route_to_geometry(plan.system)


def default_penetration_planner(ctx: PenetrationPlanContext) -> list[Penetration]:
    """Default crossing detection: segment-plane intersection against every
    member's outline (see :func:`find_face_crossings`)."""
    return find_face_crossings(ctx.system, ctx.members)


def _default_rules_for(system: System) -> RoutingRules:
    return RoutingRules()


# --------------------------------------------------------------------------- #
# The bundle handed to the engine + the driver
# --------------------------------------------------------------------------- #
@dataclass
class DesignRules:
    """Fully-pluggable rules for the two engine phases. Each callable *fully
    encompasses* its stage; the defaults reproduce the built-in routing
    behaviour. ``model_penetration`` defaults to ``None`` (crossings are found
    but no detail geometry is emitted) — supply one, e.g. via
    ``ada.topo_model.standard_design_rules``, for sleeves/holes."""

    plan_route: RoutePlanner = default_route_planner
    model_route: RouteModeller = default_route_modeller
    plan_penetration: PenetrationPlanner = default_penetration_planner
    model_penetration: PenetrationModeller | None = None
    #: Per-system routing knobs the default route planner reads.
    rules_for: Callable[[System], RoutingRules] = _default_rules_for


@dataclass
class DesignResult:
    """What :func:`run_design` produced across both phases."""

    route_plans: dict[str, RoutePlan]
    route_geometry: dict[str, list]
    penetrations: list[Penetration]
    penetration_parts: list[ada.Part]
    #: Systems whose route plan failed and were skipped (``skip_failed=True``).
    skipped: list[str] = field(default_factory=list)


def _grid_from_cell_graph(cell_graph: CellGraph, spacing: float) -> CellGrid:
    pts = [p for cell in cell_graph.cells for p in cell.get_points()]
    if not pts:
        raise RoutingError("cell graph has no cells; cannot derive a routing grid")
    xs, ys, zs = zip(*((float(p[0]), float(p[1]), float(p[2])) for p in pts))
    return CellGrid.from_bounds((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)), spacing=spacing)


def run_design(
    systems: list[System],
    *,
    cell_graph: CellGraph | None = None,
    grid: CellGrid | None = None,
    members: list[GraphFace] | None = None,
    rules: DesignRules | None = None,
    skip_failed: bool = False,
    spacing: float = 0.5,
) -> DesignResult:
    """Drive both engine phases with ``rules``: plan every system's route, plan
    the penetrations, then model the routes and the penetration details.

    ``cell_graph`` is the cell complex (its faces feed penetration planning, and
    its bounds derive a ``grid`` when none is given). ``members`` defaults to the
    complex's internal walls. With ``skip_failed`` a system whose route can't be
    planned is dropped (and named in ``result.skipped``) instead of raising —
    used by the viewer compile so one bad run doesn't sink the model."""
    from ada.config import logger

    rules = rules or DesignRules()
    if grid is None:
        if cell_graph is None:
            raise RoutingError("run_design needs a grid or a cell_graph to derive one from")
        grid = _grid_from_cell_graph(cell_graph, spacing)
    if members is None:
        members = cell_graph.get_internal_walls() if cell_graph is not None else []

    # --- Plan phase (no geometry) ------------------------------------------ #
    route_plans: dict[str, RoutePlan] = {}
    skipped: list[str] = []
    for system in systems:
        ctx = RoutePlanContext(system=system, grid=grid, cell_graph=cell_graph, rules=rules.rules_for(system))
        try:
            route_plans[system.name] = rules.plan_route(ctx)
        except (RoutingError, ValueError, KeyError) as exc:
            if not skip_failed:
                raise
            logger.warning("design: skipping system %r: %s", system.name, exc)
            skipped.append(system.name)

    planned = [s for s in systems if s.name in route_plans]

    penetrations: list[Penetration] = []
    if members:
        for system in planned:
            pctx = PenetrationPlanContext(
                system=system,
                routed_path=route_plans[system.name].polyline,
                members=members,
                cell_graph=cell_graph,
                grid=grid,
            )
            penetrations.extend(rules.plan_penetration(pctx))

    # --- Model phase (plan -> geometry) ------------------------------------ #
    # Geometry can also fail — e.g. a *strict* duct/cable-tray run whose routed
    # points sit too close to fit a real (fixed-radius) bend raises here, naming
    # the offending point sequence. Honour ``skip_failed`` the same way as the plan
    # phase so one unbuildable run is reported and dropped, not fatal to the model.
    route_geometry: dict[str, list] = {}
    for system in planned:
        ctx = RoutePlanContext(system=system, grid=grid, cell_graph=cell_graph, rules=rules.rules_for(system))
        try:
            route_geometry[system.name] = rules.model_route(route_plans[system.name], ctx)
        except (RoutingError, ValueError) as exc:
            if not skip_failed:
                raise
            logger.warning("design: skipping geometry for system %r: %s", system.name, exc)
            skipped.append(system.name)

    penetration_parts: list[ada.Part] = []
    if rules.model_penetration is not None:
        counts: dict[str, int] = {}
        for pen in penetrations:
            i = counts.get(pen.system.name, 0)
            counts[pen.system.name] = i + 1
            part = rules.model_penetration(pen, f"{pen.system.name}_pen_{i:02d}")
            if part is not None:
                penetration_parts.append(part)

    return DesignResult(
        route_plans=route_plans,
        route_geometry=route_geometry,
        penetrations=penetrations,
        penetration_parts=penetration_parts,
        skipped=skipped,
    )
