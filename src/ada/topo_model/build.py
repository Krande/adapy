"""One-liner entry points for the topo_model demo.

The flow is the whole engine in three steps: space boxes ->
``TopologyBuilder.from_prim_boxes`` (builds the cell graph) -> ``SteelStru``
blueprint (turns classified faces/edges into structure) -> output assembly.
"""

from __future__ import annotations

import ada
from ada.topology import CellGrid, TopologyBuilder

from .blueprint import SteelStru

__all__ = ["make_space_boxes", "build_topo_model"]


def make_space_boxes() -> list[ada.PrimBox]:
    """Two adjacent 5 m x 5 m x 3 m spaces. Two cells (rather than one)
    exercise the topology machinery: the shared internal wall and the
    deduplication of shared girder/column edges."""
    return [
        ada.PrimBox("Cell1", (0, 0, 0), (5, 5, 3)),
        ada.PrimBox("Cell2", (5, 0, 0), (10, 5, 3)),
    ]


def build_topo_model(name: str = "TopoModelDemo") -> ada.Assembly:
    """Build the demo model with default profiles and return the assembly."""
    builder = TopologyBuilder.from_prim_boxes(make_space_boxes(), blueprint=SteelStru())
    builder.build()
    return builder.get_output_assembly(name)


def build_routing_grid(spacing: float = 0.5) -> CellGrid:
    """Routing lattice above the top deck (z 3.0..5.5 covers the tallest
    equipment plus one clear level) over the 10 x 5 plan."""
    return CellGrid.from_bounds((0, 0, 3.0), (10, 5, 5.5), spacing=spacing)


def build_topo_model_with_systems(name: str = "TopoModelDemo") -> ada.Assembly:
    """The full demo: Phase A structure (with the shared internal wall built as
    a reinforced wall) + pump, tank and a switchboard on the top deck wired into
    piping and electrical systems routed over the deck grid, plus an interior
    service run routed straight THROUGH the reinforced wall — its crossing gets a
    penetration detail (sleeve + wall-plate hole) from the
    ``StandardPenetrations`` blueprint.

    Two systems terminate at the site boundary rather than at equipment: the
    ``Mains`` feed enters at a *site input* (the grid connection) and runs to the
    switchboard, and the tank's ``Drain`` leaves at a *site output*. The signal
    ports are deliberately left unconnected so the missing-I/O report has
    something to say."""
    from ada.api.systems import ElectricalSystem, PipingSystem, PortDirection, Voltage

    from .equipment import create_pump, create_switchboard, create_tank
    from .penetration import StandardPenetrations

    builder = TopologyBuilder.from_prim_boxes(make_space_boxes(), blueprint=SteelStru(reinforce_internal_walls=True))
    builder.build()
    a = builder.get_output_assembly(name)
    cg = builder.cell_graph

    # Deck equipment + systems.
    pump = create_pump("Pump1", origin=(2.5, 2.5, 3.0))
    tank = create_tank("Tank1", origin=(7.5, 2.5, 3.0))
    switchboard = create_switchboard("Switchboard1", origin=(0.5, 2.5, 3.0))

    cooling = PipingSystem("CoolingWater", medium="water").connect(pump, "discharge").connect(tank, "inlet")
    # Two-ended electrical: the switchboard's outgoing feeder powers the pump.
    power = ElectricalSystem("PowerFeed", voltage=Voltage.LV_690).connect(switchboard, "feeder").connect(pump, "power")
    # Site input: the high-voltage grid supply enters at the deck edge and feeds
    # the switchboard's incoming terminal.
    mains = (
        ElectricalSystem("Mains", voltage=Voltage.MV_11000)
        .connect_site("grid_supply", (0.0, 0.0, 3.5), PortDirection.IN)
        .connect(switchboard, "incoming")
    )
    # Site output: the tank drains off the model boundary at the far deck edge.
    drain = (
        PipingSystem("Drain", medium="water")
        .connect(tank, "outlet")
        .connect_site("drain_to_site", (10.0, 2.5, 3.0), PortDirection.OUT)
    )

    # All deck systems route on one fine 0.5 m lattice for precise detours; swept
    # runs are then pulled taut in the clear corridor (system.route -> grid-aware
    # system_route_to_geometry) so their bends come out few and well-separated.
    from ada.config import logger
    from ada.topology.routing import occupy_faces, occupy_run, run_half_extent

    grid = build_routing_grid()
    # Inflate each equipment box by the widest run's cross-section half-extent so a
    # run's *body* — not just its centreline — clears the equipment (the nozzle
    # stub sits a full cell out, well beyond this halo, so ports stay reachable).
    deck_clear = max(run_half_extent(s) for s in (cooling, power, mains, drain))
    for eq in (pump, tank, switchboard):
        _occupy_equipment_nodes(grid, eq, clearance=deck_clear)
    # No-go walls: treat the reinforced internal wall (x=5) as an impenetrable
    # obstacle for the *deck* runs, so a system crossing the bay (CoolingWater)
    # climbs over the wall instead of skimming through its top. The interior
    # service run uses a separate grid where this wall stays penetrable (it is
    # meant to cross it and get a penetration detail).
    occupy_faces(grid, cg.get_internal_walls(), clearance=deck_clear, tag="no_go:wall")

    # Interior service run: pump in Cell1 to tank in Cell2 — the route must
    # cross the reinforced wall at x=5.
    pump2 = create_pump("Pump2", origin=(2.5, 2.5, 0.0))
    tank2 = create_tank("Tank2", origin=(7.5, 2.5, 0.0))
    service = PipingSystem("ServiceWater", medium="water").connect(pump2, "discharge").connect(tank2, "inlet")

    interior_grid = CellGrid.from_bounds((0, 0, 0), (10, 5, 3.0), spacing=0.5)
    for eq in (pump2, tank2):
        _occupy_equipment_nodes(interior_grid, eq, clearance=run_half_extent(service))

    systems_part = ada.Part("Systems")
    deck_systems = (cooling, power, mains, drain)
    routed = [(s, grid) for s in deck_systems] + [(service, interior_grid)]
    # Route sequentially, marking each run's body occupied so the next system
    # routes around it (systems don't overlap); swept runs then pull taut clear
    # of both equipment and prior runs.
    other_clear = max(run_half_extent(s) for s, _ in routed)
    for system, sys_grid in routed:
        for geom in system.route(sys_grid):
            systems_part.add_object(geom)
        for w in system.route_warnings:
            logger.warning("route %s: %s", system.name, w)
        if system.routed_path:
            occupy_run(sys_grid, system.routed_path, run_half_extent(system) + other_clear, tag=f"system:{system.name}")

    # Penetration details wherever a routed system crosses an internal wall
    # (also cuts the through-hole in the reinforced wall's plate). The deck runs
    # stay above the walls; only the interior service run penetrates.
    penetrations = StandardPenetrations(systems=[*deck_systems, service], faces=cg.get_internal_walls())
    a.add_part(penetrations.build())

    a.add_part(ada.Part("Equipment") / [pump, tank, switchboard, pump2, tank2])
    a.add_part(systems_part)
    a.systems.extend([*deck_systems, service])
    return a


def _occupy_equipment_nodes(grid: CellGrid, eq: ada.Equipment, clearance: float = 0.0) -> None:
    """Mark the grid nodes inside the equipment's body — inflated by ``clearance``
    (a run's cross-section half-extent) — as occupied so a run's *body*, not just
    its centreline, detours around it. With ``clearance == 0`` the bounds are
    exclusive so surface nodes stay free (ports on the body remain reachable); with
    a clearance the inflated bounds are inclusive, and the nozzle stub — a full
    cell out along the port normal, beyond the halo — keeps the port reachable."""
    ox, oy, oz = (float(v) for v in eq.origin)
    c = float(clearance)
    x0, x1 = ox - eq.lx / 2 - c, ox + eq.lx / 2 + c
    y0, y1 = oy - eq.ly / 2 - c, oy + eq.ly / 2 + c
    z0, z1 = oz - c, oz + eq.lz + c
    tol = 1e-9
    inside = (lambda lo, v, hi: lo - tol <= v <= hi + tol) if c > 0.0 else (lambda lo, v, hi: lo + tol < v < hi - tol)
    for ix, x in enumerate(grid.x_list):
        if not inside(x0, x, x1):
            continue
        for iy, y in enumerate(grid.y_list):
            if not inside(y0, y, y1):
                continue
            for iz, z in enumerate(grid.z_list):
                if inside(z0, z, z1):
                    grid.register((ix, iy, iz), eq.name)
