"""One-liner entry points for the topo_model demo.

The flow is the whole engine in three steps: space boxes ->
``TopologyBuilder.from_prim_boxes`` (builds the cell graph) -> ``SteelStru``
blueprint (turns classified faces/edges into structure) -> output assembly.
"""

from __future__ import annotations

import ada
from ada.api.systems import Port, PortDirection
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
    a reinforced wall) + pump and tank on the top deck wired into a piping and
    an electrical system routed over the deck grid, plus an interior service
    run routed straight THROUGH the reinforced wall — its crossing gets a
    penetration detail (sleeve + wall-plate hole) from the
    ``StandardPenetrations`` blueprint. The signal ports are deliberately left
    unconnected so the missing-I/O report has something to say."""
    from ada.api.systems import ElectricalSystem, PipingSystem, Voltage

    from .equipment import create_pump, create_tank
    from .penetration import StandardPenetrations

    builder = TopologyBuilder.from_prim_boxes(make_space_boxes(), blueprint=SteelStru(reinforce_internal_walls=True))
    builder.build()
    a = builder.get_output_assembly(name)
    cg = builder.cell_graph

    # Deck equipment + systems.
    pump = create_pump("Pump1", origin=(2.5, 2.5, 3.0))
    tank = create_tank("Tank1", origin=(7.5, 2.5, 3.0))

    cooling = PipingSystem("CoolingWater", medium="water").connect(pump, "discharge").connect(tank, "inlet")
    power = ElectricalSystem("PowerFeed", voltage=Voltage.LV_690).connect(pump, "power")
    # single-ended: route from the pump's power port to a supply stub at the deck edge
    supply_stub = Port("supply", (0.0, 0.0, 3.5), (0, 0, 1), PortDirection.OUT, "electrical")
    power.ports.append(supply_stub)

    grid = build_routing_grid()
    for eq in (pump, tank):
        _occupy_equipment_nodes(grid, eq)

    # Interior service run: pump in Cell1 to tank in Cell2 — the route must
    # cross the reinforced wall at x=5.
    pump2 = create_pump("Pump2", origin=(2.5, 2.5, 0.0))
    tank2 = create_tank("Tank2", origin=(7.5, 2.5, 0.0))
    service = PipingSystem("ServiceWater", medium="water").connect(pump2, "discharge").connect(tank2, "inlet")

    interior_grid = CellGrid.from_bounds((0, 0, 0), (10, 5, 3.0), spacing=0.5)
    for eq in (pump2, tank2):
        _occupy_equipment_nodes(interior_grid, eq)

    systems_part = ada.Part("Systems")
    for system, sys_grid in ((cooling, grid), (power, grid), (service, interior_grid)):
        for geom in system.route(sys_grid):
            systems_part.add_object(geom)

    # Penetration details wherever a routed system crosses an internal wall
    # (also cuts the through-hole in the reinforced wall's plate).
    penetrations = StandardPenetrations(systems=[cooling, power, service], faces=cg.get_internal_walls())
    a.add_part(penetrations.build())

    a.add_part(ada.Part("Equipment") / [pump, tank, pump2, tank2])
    a.add_part(systems_part)
    a.systems.extend([cooling, power, service])
    return a


def _occupy_equipment_nodes(grid: CellGrid, eq: ada.Equipment) -> None:
    """Mark the grid nodes strictly inside the equipment's body volume as
    occupied so routes detour around it. Bounds are exclusive: surface nodes
    stay free so ports sitting on the body (nozzles) remain reachable."""
    ox, oy, oz = (float(v) for v in eq.origin)
    x0, x1 = ox - eq.lx / 2, ox + eq.lx / 2
    y0, y1 = oy - eq.ly / 2, oy + eq.ly / 2
    z0, z1 = oz, oz + eq.lz
    tol = 1e-9
    for ix, x in enumerate(grid.x_list):
        if not (x0 + tol < x < x1 - tol):
            continue
        for iy, y in enumerate(grid.y_list):
            if not (y0 + tol < y < y1 - tol):
                continue
            for iz, z in enumerate(grid.z_list):
                if z0 + tol < z < z1 - tol:
                    grid.register((ix, iy, iz), eq.name)
