"""Pluggable two-phase design rules: custom planners/modellers fully override
the routing and penetration stages, and ``run_design`` drives both phases."""

from __future__ import annotations

import pytest

import ada
from ada.topology import (
    CellGrid,
    DesignRules,
    Penetration,
    RoutePlan,
    RoutingError,
    RoutingRules,
    TopologyBuilder,
    run_design,
)
from ada.topo_model import SteelStru, create_pump, create_tank, standard_design_rules


def _two_connected_equipment(p_a=(0, 0, 0), p_b=(4, 4, 0)):
    eq1 = ada.Equipment("E1", 1.0, (0, 0, 0), p_a, 0.1, 0.1, 0.1)
    eq2 = ada.Equipment("E2", 1.0, (0, 0, 0), p_b, 0.1, 0.1, 0.1)
    eq1.add_port(ada.Port("out", (0, 0, 0.1), (0, 0, 1), ada.PortDirection.OUT))
    eq2.add_port(ada.Port("in", (0, 0, 0.1), (0, 0, 1), ada.PortDirection.IN))
    return ada.PipingSystem("CW", medium="water").connect(eq1, "out").connect(eq2, "in")


@pytest.fixture
def grid() -> CellGrid:
    return CellGrid.from_bounds((0, 0, 0), (4, 4, 2), spacing=1.0)


@pytest.fixture
def walled_model():
    """Two 5x5x3 cells sharing a reinforced internal wall at x=5, with a service
    run wired pump (Cell1) -> tank (Cell2) so it must cross the wall."""
    boxes = [ada.PrimBox("Cell1", (0, 0, 0), (5, 5, 3)), ada.PrimBox("Cell2", (5, 0, 0), (10, 5, 3))]
    builder = TopologyBuilder.from_prim_boxes(boxes, blueprint=SteelStru(reinforce_internal_walls=True))
    builder.build()
    pump = create_pump("Pump", origin=(2.5, 2.5, 0.0))
    tank = create_tank("Tank", origin=(7.5, 2.5, 0.0))
    system = ada.PipingSystem("Service", medium="water").connect(pump, "discharge").connect(tank, "inlet")
    grid = CellGrid.from_bounds((0, 0, 0), (10, 5, 3.0), spacing=0.5)
    return builder.cell_graph, grid, system


# --------------------------------------------------------------------------- #
# defaults
# --------------------------------------------------------------------------- #
def test_defaults_route_and_find_crossings_but_model_no_detail(walled_model):
    cell_graph, grid, system = walled_model
    result = run_design([system], cell_graph=cell_graph, grid=grid)
    # route modelled
    pipes = [g for g in result.route_geometry[system.name] if isinstance(g, ada.Pipe)]
    assert len(pipes) == 1 and pipes[0].name == "Service_route"
    # crossing planned (route crosses the internal wall)
    assert len(result.penetrations) >= 1
    assert all(isinstance(p, Penetration) for p in result.penetrations)
    # but the core default emits no detail geometry
    assert result.penetration_parts == []


def test_standard_rules_model_the_sleeve_detail(walled_model):
    cell_graph, grid, system = walled_model
    result = run_design([system], cell_graph=cell_graph, grid=grid, rules=standard_design_rules())
    assert result.penetration_parts, "standard rules should emit a detail part per crossing"
    names = [o.name for part in result.penetration_parts for o in part.get_all_physical_objects()]
    assert any(n.endswith("_sleeve") for n in names)


# --------------------------------------------------------------------------- #
# plan-phase overrides (routing)
# --------------------------------------------------------------------------- #
def test_custom_route_planner_fully_controls_the_path(grid):
    system = _two_connected_equipment()
    forced = [ada.Point(0, 0, 0.1), ada.Point(0, 0, 2), ada.Point(4, 4, 2), ada.Point(4, 4, 0.1)]

    def planner(ctx):
        # A rule that fully encompasses routing: ignore A*, pin a fixed path.
        ctx.system.routed_path = forced
        return RoutePlan(system=ctx.system, polyline=forced)

    result = run_design([system], grid=grid, rules=DesignRules(plan_route=planner))
    assert system.routed_path == forced
    assert result.route_plans[system.name].polyline == forced
    assert isinstance(result.route_geometry[system.name][0], ada.Pipe)


def test_rules_for_injects_per_system_routing_rules(grid):
    system = _two_connected_equipment()
    seen = {}

    def rules_for(s):
        seen["called"] = s.name
        # forbid the top level so the route stays on z=0
        return RoutingRules(is_allowed=lambda idx, g: g.z_list[idx[2]] < 1.0)

    result = run_design([system], grid=grid, rules=DesignRules(rules_for=rules_for))
    assert seen["called"] == "CW"
    assert all(float(p[2]) < 1.0 or abs(float(p[2]) - 0.1) < 1e-9 for p in result.route_plans["CW"].polyline)


# --------------------------------------------------------------------------- #
# model-phase overrides (routing + penetration)
# --------------------------------------------------------------------------- #
def test_custom_route_modeller_emits_custom_geometry(grid):
    system = _two_connected_equipment()

    def modeller(plan, ctx):
        beam = ada.Beam(f"{plan.system.name}_tray", plan.polyline[0], plan.polyline[-1], "IPE200")
        plan.system.route_geometry.append(beam)
        return plan.system.route_geometry

    result = run_design([system], grid=grid, rules=DesignRules(model_route=modeller))
    geoms = result.route_geometry[system.name]
    assert any(isinstance(g, ada.Beam) for g in geoms)
    assert not any(isinstance(g, ada.Pipe) for g in geoms)


def test_custom_penetration_modeller_emits_custom_detail(walled_model):
    cell_graph, grid, system = walled_model
    built = []

    def model_penetration(pen, name):
        marker = ada.PrimBox(f"{name}_marker", tuple(pen.point), tuple(pen.point + ada.Direction(0.1, 0.1, 0.1)))
        built.append(name)
        return ada.Part(name) / marker

    result = run_design(
        [system], cell_graph=cell_graph, grid=grid, rules=DesignRules(model_penetration=model_penetration)
    )
    assert built, "custom penetration modeller should run once per crossing"
    names = [o.name for part in result.penetration_parts for o in part.get_all_physical_objects()]
    assert any(n.endswith("_marker") for n in names)


def test_custom_penetration_planner_short_circuits(walled_model):
    cell_graph, grid, system = walled_model
    calls = []

    def plan_penetration(ctx):
        calls.append(ctx.system.name)
        return []  # a rule that decides nothing penetrates

    result = run_design(
        [system],
        cell_graph=cell_graph,
        grid=grid,
        rules=DesignRules(plan_penetration=plan_penetration, model_penetration=lambda pen, name: ada.Part(name)),
    )
    assert calls == ["Service"]
    assert result.penetrations == [] and result.penetration_parts == []


# --------------------------------------------------------------------------- #
# resilience
# --------------------------------------------------------------------------- #
def test_unroutable_system_raises_by_default_and_skips_when_asked():
    system = _two_connected_equipment(p_a=(0, 0, 0), p_b=(2, 0, 0))
    blocked = CellGrid.from_bounds((0, 0, 0), (2, 0, 0), spacing=1.0)
    blocked.register((1, 0, 0), "wall")

    with pytest.raises(RoutingError):
        run_design([system], grid=blocked)

    result = run_design([system], grid=blocked, skip_failed=True)
    assert result.skipped == ["CW"]
    assert "CW" not in result.route_geometry
