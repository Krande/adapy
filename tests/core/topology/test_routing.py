"""A* routing over a CellGrid: occupancy avoidance, snapping, collinear
simplification and system route -> geometry."""

from __future__ import annotations

import pytest

import ada
from ada.topology import (
    CellGrid,
    RoutingError,
    astar_route,
    nearest_index,
    path_to_polyline,
)
from ada.topology.routing import route_system, system_route_to_geometry


@pytest.fixture
def grid() -> CellGrid:
    return CellGrid.from_bounds((0, 0, 0), (4, 4, 2), spacing=1.0)


def test_from_bounds_axes(grid):
    assert grid.x_list == [0, 1, 2, 3, 4]
    assert grid.z_list == [0, 1, 2]


def test_nearest_index_snaps_off_grid(grid):
    assert nearest_index(grid, 1.2, 2.9, 0.4) == (1, 3, 0)
    assert nearest_index(grid, -5, 99, 1.6) == (0, 4, 2)


def test_astar_straight_line(grid):
    path = astar_route(grid, (0, 0, 0), (4, 0, 0))
    assert path[0] == (0, 0, 0) and path[-1] == (4, 0, 0)
    assert len(path) == 5
    polyline = path_to_polyline(grid, path)
    assert len(polyline) == 2  # collinear nodes removed, only the two ends remain


def test_astar_detours_around_occupied(grid):
    # wall of occupied nodes at x=2 for all y in 0..3 (gap at y=4), z=0
    for iy in range(4):
        grid.register((2, iy, 0), "wall")
    path = astar_route(grid, (0, 0, 0), (4, 0, 0))
    assert all(not grid.has_geometry(idx) for idx in path)
    assert (2, 4, 0) in path or any(idx[2] > 0 for idx in path)  # went around or over


def test_astar_no_route_raises():
    grid = CellGrid.from_bounds((0, 0, 0), (2, 0, 0), spacing=1.0)
    grid.register((1, 0, 0), "block")
    with pytest.raises(RoutingError, match="no route"):
        astar_route(grid, (0, 0, 0), (2, 0, 0))


def _two_connected_equipment():
    eq1 = ada.Equipment("E1", 1.0, (0, 0, 0), (0, 0, 0), 0.1, 0.1, 0.1)
    eq2 = ada.Equipment("E2", 1.0, (0, 0, 0), (4, 4, 0), 0.1, 0.1, 0.1)
    eq1.add_port(ada.Port("out", (0, 0, 0.1), (0, 0, 1), ada.PortDirection.OUT))
    eq2.add_port(ada.Port("in", (0, 0, 0.1), (0, 0, 1), ada.PortDirection.IN))
    system = ada.PipingSystem("CW", medium="water").connect(eq1, "out").connect(eq2, "in")
    return system


def test_route_system_endpoints_are_exact_port_positions(grid):
    system = _two_connected_equipment()
    polyline = route_system(system, grid)
    assert system.routed_path is polyline
    assert tuple(polyline[0]) == tuple(system.ports[0].get_global_position())
    assert tuple(polyline[-1]) == tuple(system.ports[-1].get_global_position())


def test_route_system_needs_two_ports(grid):
    eq = ada.Equipment("E1", 1.0, (0, 0, 0), (0, 0, 0), 0.1, 0.1, 0.1)
    eq.add_port(ada.Port("out", (0, 0, 0), (0, 0, 1), ada.PortDirection.OUT))
    system = ada.PipingSystem("CW").connect(eq, "out")
    with pytest.raises(RoutingError, match="need two ends"):
        route_system(system, grid)


def test_system_route_to_geometry(grid):
    system = _two_connected_equipment()
    route_system(system, grid)
    (pipe,) = system_route_to_geometry(system)
    assert isinstance(pipe, ada.Pipe)
    assert pipe.metadata["segment_ifc_class"] == "IfcPipeSegment"
    assert tuple(pipe.points[0]) == tuple(system.routed_path[0])
    assert tuple(pipe.points[-1]) == tuple(system.routed_path[-1])


def test_cable_system_route_geometry_class(grid):
    eq1 = ada.Equipment("E1", 1.0, (0, 0, 0), (0, 0, 0), 0.1, 0.1, 0.1)
    eq2 = ada.Equipment("E2", 1.0, (0, 0, 0), (4, 0, 0), 0.1, 0.1, 0.1)
    eq1.add_port(ada.Port("a", (0, 0, 0), (0, 0, 1), ada.PortDirection.INOUT, "signal"))
    eq2.add_port(ada.Port("b", (0, 0, 0), (0, 0, 1), ada.PortDirection.INOUT, "signal"))
    system = ada.CableSystem("Sig").connect(eq1, "a").connect(eq2, "b")
    system.route(grid)
    (pipe,) = system.route_geometry
    assert pipe.metadata["segment_ifc_class"] == "IfcCableSegment"
