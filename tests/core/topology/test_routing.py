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


def test_route_system_leaves_ports_along_nozzle_normal(grid):
    # Both ports face +Z; the run must step straight up out of each nozzle (same
    # x/y, increasing z) before turning onto the grid — it must not leave the
    # port on a diagonal (issue: pipes ignored the port vector orientation). The
    # nozzle stub may overshoot the first grid node; route_system cleans that
    # anti-parallel overshoot so the leg stays a clean vertical hop rather than a
    # degenerate 180° spike (which crashes pipe-elbow generation).
    system = _two_connected_equipment()
    polyline = route_system(system, grid)
    p_start = system.ports[0].get_global_position()  # (0, 0, 0.1), dir +Z
    p_end = system.ports[-1].get_global_position()  # (4, 4, 0.1), dir +Z
    assert (polyline[1][0], polyline[1][1]) == (p_start[0], p_start[1])
    assert polyline[1][2] > p_start[2]
    assert (polyline[-2][0], polyline[-2][1]) == (p_end[0], p_end[1])
    assert polyline[-2][2] > p_end[2]


def test_route_system_leaves_site_terminal_along_its_orientation(grid):
    # A site terminal on the x=0 wall faces +X (into the model); the run must
    # step out along +X (increasing x, same y/z) before turning onto the grid —
    # the terminal's orientation, not just its position, drives the departure.
    # Contrast with the default +Z orientation, which would step up instead.
    eq = ada.Equipment("E1", 1.0, (0, 0, 0), (4, 4, 1), 0.1, 0.1, 0.1)
    eq.add_port(ada.Port("in", (0, 0, 0), (0, 0, 1), ada.PortDirection.IN, "process"))
    system = (
        ada.PipingSystem("Supply")
        .connect_site("grid", (0, 2, 1), ada.PortDirection.IN, direction_vector=(1, 0, 0))
        .connect(eq, "in")
    )
    polyline = route_system(system, grid)
    p_start = system.ports[0].get_global_position()  # (0, 2, 1), faces +X
    assert tuple(polyline[0]) == tuple(p_start)
    assert (polyline[1][1], polyline[1][2]) == (p_start[1], p_start[2])
    assert polyline[1][0] > p_start[0]


def test_route_system_needs_two_ports(grid):
    eq = ada.Equipment("E1", 1.0, (0, 0, 0), (0, 0, 0), 0.1, 0.1, 0.1)
    eq.add_port(ada.Port("out", (0, 0, 0), (0, 0, 1), ada.PortDirection.OUT))
    system = ada.PipingSystem("CW").connect(eq, "out")
    with pytest.raises(RoutingError, match="need two ends"):
        route_system(system, grid)


def test_route_system_has_no_degenerate_spikes(grid):
    # Ports sit off-grid (z=0.1), so the nozzle stub overshoots the first grid
    # node — the raw cap used to leave a 180° spike (out along +Z, straight back
    # down) that crashes pipe-elbow generation. The sanitised run must contain no
    # zero-length hop and no anti-parallel reversal, and its pipe segments must
    # realise cleanly.
    system = _two_connected_equipment()
    polyline = route_system(system, grid)
    pts = [tuple(float(v) for v in p) for p in polyline]
    for a, b in zip(pts, pts[1:]):
        assert a != b, f"zero-length segment at {a}"
    for prev, cur, nxt in zip(pts, pts[1:], pts[2:]):
        d1 = [cur[i] - prev[i] for i in range(3)]
        d2 = [nxt[i] - cur[i] for i in range(3)]
        cross = (
            d1[1] * d2[2] - d1[2] * d2[1],
            d1[2] * d2[0] - d1[0] * d2[2],
            d1[0] * d2[1] - d1[1] * d2[0],
        )
        assert sum(c * c for c in cross) > 1e-12, f"collinear/anti-parallel spike at {cur}"
    (pipe,) = system_route_to_geometry(system)
    assert len(pipe.segments) > 0  # elbow generation no longer raises


def test_system_route_to_geometry(grid):
    system = _two_connected_equipment()
    route_system(system, grid)
    (pipe,) = system_route_to_geometry(system)
    assert isinstance(pipe, ada.Pipe)
    assert pipe.metadata["segment_ifc_class"] == "IfcPipeSegment"
    assert tuple(pipe.points[0]) == tuple(system.routed_path[0])
    assert tuple(pipe.points[-1]) == tuple(system.routed_path[-1])


def test_cable_system_route_geometry_class(grid):
    from ada.sections.categories import BaseTypes

    eq1 = ada.Equipment("E1", 1.0, (0, 0, 0), (0, 0, 0), 0.1, 0.1, 0.1)
    eq2 = ada.Equipment("E2", 1.0, (0, 0, 0), (4, 0, 0), 0.1, 0.1, 0.1)
    eq1.add_port(ada.Port("a", (0, 0, 0), (0, 0, 1), ada.PortDirection.INOUT, "signal"))
    eq2.add_port(ada.Port("b", (0, 0, 0), (0, 0, 1), ada.PortDirection.INOUT, "signal"))
    system = ada.CableSystem("Sig").connect(eq1, "a").connect(eq2, "b")
    system.route(grid)
    # A cable tray is an open UNP channel run of straight beams, not a pipe.
    beams = system.route_geometry
    assert beams and all(isinstance(b, ada.Beam) for b in beams)
    assert all(b.section.type == BaseTypes.CHANNEL for b in beams)
    assert beams[0].metadata["segment_ifc_class"] == "IfcCableSegment"


def test_duct_system_route_geometry_class(grid):
    from ada.sections.categories import BaseTypes

    eq1 = ada.Equipment("E1", 1.0, (0, 0, 0), (0, 0, 0), 0.1, 0.1, 0.1)
    eq2 = ada.Equipment("E2", 1.0, (0, 0, 0), (4, 0, 0), 0.1, 0.1, 0.1)
    eq1.add_port(ada.Port("a", (0, 0, 0), (0, 0, 1), ada.PortDirection.INOUT, "process"))
    eq2.add_port(ada.Port("b", (0, 0, 0), (0, 0, 1), ada.PortDirection.INOUT, "process"))
    system = ada.DuctSystem("Air").connect(eq1, "a").connect(eq2, "b")
    system.route(grid)
    # A duct is a rectangular BOX run of straight beams, not a pipe.
    beams = system.route_geometry
    assert beams and all(isinstance(b, ada.Beam) for b in beams)
    assert all(b.section.type == BaseTypes.BOX for b in beams)
    assert beams[0].metadata["segment_ifc_class"] == "IfcDuctSegment"


@pytest.mark.parametrize("kind", ["duct", "cable"])
def test_beam_run_is_swept_along_a_curved_directrix(kind):
    """A duct/cable run over an L-path is a single solid swept along its routed 3D
    curve (like a pipe), not a chain of straight beams: one ``BeamCurved`` whose
    directrix is an ``IndexedPolyCurve`` with the 90° corner filleted into an
    ``ArcLine``. Its straight legs stay axis-aligned and its endpoints are the
    run ends."""
    import numpy as np

    from ada.geom.curves import ArcLine, Edge, IndexedPolyCurve
    from ada.topology.routing import system_route_to_geometry

    system = (ada.DuctSystem if kind == "duct" else ada.CableSystem)("Run")
    system.routed_path = [ada.Point(0, 0, 3), ada.Point(2, 0, 3), ada.Point(2, 2, 3)]
    geoms = system_route_to_geometry(system)

    # one swept run, carrying its analytic directrix (not a polyline approximation)
    assert len(geoms) == 1
    (run,) = geoms
    assert isinstance(run, ada.BeamCurved)
    directrix = run.curve3d
    assert isinstance(directrix, IndexedPolyCurve)

    # the corner is a real circular bend (an ArcLine), flanked by straight Edges
    arcs = [s for s in directrix.segments if isinstance(s, ArcLine)]
    edges = [s for s in directrix.segments if isinstance(s, Edge)]
    assert len(arcs) == 1, f"{kind} bend is not a single arc: {directrix.segments}"
    assert len(edges) == 2, f"{kind} run is missing its straight legs: {directrix.segments}"

    # each straight leg is axis-aligned (+X in, +Y out)
    for e in edges:
        d = np.asarray(e.end, dtype=float) - np.asarray(e.start, dtype=float)
        assert int(np.sum(np.abs(d) > 1e-6)) == 1, f"{kind} leg not axis-aligned: {d}"

    # the run spans the routed ends
    assert tuple(np.round(run.n1.p, 6)) == (0.0, 0.0, 3.0)
    assert tuple(np.round(run.n2.p, 6)) == (2.0, 2.0, 3.0)
