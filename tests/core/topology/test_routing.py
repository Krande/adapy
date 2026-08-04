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
def test_beam_run_is_segmented_swept_along_curved_directrix(kind):
    """A duct/cable run over an L-path is emitted as one swept solid PER directrix
    segment — a straight ``Edge`` leg or a curved ``ArcLine`` bend — each an
    individually named ``BeamCurved`` (so every leg and fitting is separately
    selectable in the viewer), the way a pipe's segments are. Each segment carries
    its own single-segment analytic directrix; the straight legs stay axis-aligned;
    one segment is the filleted 90° bend."""
    import numpy as np

    from ada.geom.curves import ArcLine, Edge
    from ada.topology.routing import system_route_to_geometry

    system = (ada.DuctSystem if kind == "duct" else ada.CableSystem)("Run")
    system.routed_path = [ada.Point(0, 0, 3), ada.Point(2, 0, 3), ada.Point(2, 2, 3)]
    geoms = system_route_to_geometry(system)

    # L-path: two straight legs + one filleted bend = three named segments
    assert len(geoms) == 3
    assert [g.name for g in geoms] == ["Run_route_0", "Run_route_1", "Run_route_2"]
    assert all(isinstance(g, ada.BeamCurved) for g in geoms)

    # each segment carries exactly one directrix segment; classify edge vs arc
    kinds = []
    for g in geoms:
        (seg,) = g.curve3d.segments
        assert isinstance(seg, (Edge, ArcLine))
        kinds.append("arc" if isinstance(seg, ArcLine) else "edge")
    assert kinds == ["edge", "arc", "edge"], f"{kind} not straight-curved-straight: {kinds}"

    # each straight leg is axis-aligned (+X in, +Y out)
    for g, k in zip(geoms, kinds):
        if k == "edge":
            d = np.asarray(g.n2.p, dtype=float) - np.asarray(g.n1.p, dtype=float)
            assert int(np.sum(np.abs(d) > 1e-6)) == 1, f"{kind} leg not axis-aligned: {d}"

    # the segments span the routed run end to end
    assert tuple(np.round(geoms[0].n1.p, 6)) == (0.0, 0.0, 3.0)
    assert tuple(np.round(geoms[-1].n2.p, 6)) == (2.0, 2.0, 3.0)


def test_segmented_run_is_frame_continuous_through_3d_bends():
    """A run that turns in plan AND climbs (out-of-plane bends) must have its
    individually-swept segments share a common frame at every join — otherwise the
    profile snaps ~90° at the vertical bend (each segment framed in isolation picks
    a different lateral for the ambiguous vertical tangent). The run is framed once
    (parallel transport) and sliced per segment, so adjacent segments agree exactly.
    Regression for the twisted-duct-bend bug."""
    import numpy as np

    from ada.topology.routing import system_route_to_geometry

    system = ada.DuctSystem("Air")
    # +X, then +Y (turn in plan), then +Z (climb): the +Y->+Z bend is out of the
    # first bend's plane — the case the memoryless t x up framing snapped on.
    system.routed_path = [
        ada.Point(0, 0, 0),
        ada.Point(2, 0, 0),
        ada.Point(2, 2, 0),
        ada.Point(2, 2, 2),
    ]
    geoms = system_route_to_geometry(system)
    assert len(geoms) >= 4  # edges + two fillet arcs

    prev_end = None
    for g in geoms:
        origins, dir_x, dir_y = (np.asarray(a, float) for a in g.solid_geom().geometry.precomputed_frames)
        assert len(origins) >= 2
        if prev_end is not None:
            # the shared join station carries the identical frame across the seam
            assert np.linalg.norm(prev_end[0] - dir_x[0]) < 1e-9
            assert np.linalg.norm(prev_end[1] - dir_y[0]) < 1e-9
        prev_end = (dir_x[-1], dir_y[-1])
        # every station's frame is orthonormal and perpendicular to nothing degenerate
        for u, v in zip(dir_x, dir_y):
            assert abs(np.dot(u, v)) < 1e-6
            assert abs(np.linalg.norm(u) - 1) < 1e-6 and abs(np.linalg.norm(v) - 1) < 1e-6

    # the first (horizontal) leg keeps the profile upright: up == +Z, as before
    first_dy = np.asarray(geoms[0].solid_geom().geometry.precomputed_frames[2], float)[0]
    assert np.allclose(first_dy, (0, 0, 1), atol=1e-9)


def test_site_terminal_orientation_respected_when_stub_is_occupied(grid):
    # A site terminal whose one-cell nozzle stub lands inside an equipment's
    # occupied halo (a wall terminal right next to a switchboard) must STILL leave
    # along its orientation vector — the old code dropped the stub whenever it was
    # occupied, silently discarding the specified direction. A* exempts its goal
    # node from occupancy, so the run can still terminate along the nozzle.
    from ada.topology.routing import nearest_index

    # occupy the terminal's stub node (one +X cell in from the x=0 wall at (0,2,1))
    stub_idx = nearest_index(grid, 1.0, 2.0, 1.0)
    grid.register(stub_idx, "blocker")

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
    # first step is still along +X (orientation preserved despite the blocked stub)
    assert (polyline[1][1], polyline[1][2]) == (p_start[1], p_start[2])
    assert polyline[1][0] > p_start[0]


def test_open_channel_swept_run_builds_occ_solid():
    """An open UNP cable tray swept along a directrix with *consecutive* arc fillets
    (a tight zig-zag with a vertical rise) must build a valid, non-empty OCC solid —
    not raise ``StdFail_NotDone`` and get skipped. The shared directrix sampler emits
    many near-coincident stations around such tight bends; the OCC ruled loft decimates
    them before lofting, so ThruSections no longer chokes on degenerate sections.

    This is the OCC-fallback (no-adacpp) render path for routed cable trays; the
    NGEOM/libtess2 stream already renders it. Regression for that parity gap."""
    pytest.importorskip("OCC")
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.TopAbs import TopAbs_SOLID
    from OCC.Core.TopExp import TopExp_Explorer

    from ada.geom.curves import ArcLine
    from ada.occ.geom.solids import make_fixed_reference_swept_area_shape_from_geom
    from ada.topology.routing import (
        _orthogonalize_polyline,
        _polyline_to_directrix,
        _SweptRun,
    )

    path = [
        ada.Point(0, 0, 0.5),
        ada.Point(-0.2, 0, 0.5),
        ada.Point(0, 0, 0.5),
        ada.Point(0, 0, 4.5),
        ada.Point(0.5, 0, 4.5),
        ada.Point(0.5, 0.2, 4.7),
    ]
    ortho = _orthogonalize_polyline(path)
    directrix = _polyline_to_directrix(ortho, 0.1)

    # the directrix must actually exercise the consecutive-arc case this fix targets
    kinds = [type(s).__name__ for s in directrix.segments]
    assert any(
        isinstance(a, ArcLine) and isinstance(b, ArcLine) for a, b in zip(directrix.segments, directrix.segments[1:])
    ), f"repro no longer has consecutive arcs: {kinds}"

    sec = ada.Section("c", "UNP", h=0.3, w_top=0.1, w_btn=0.1, t_w=0.003, t_ftop=0.003, t_fbtn=0.003)
    run = _SweptRun("tray", ada.Point(*ortho[0]), ada.Point(*ortho[-1]), directrix, sec, open_channel=True)

    shape = make_fixed_reference_swept_area_shape_from_geom(run.solid_geom().geometry)

    assert TopExp_Explorer(shape, TopAbs_SOLID).More(), "swept tray did not build a solid"
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    assert abs(props.Mass()) > 1e-9, "swept tray solid is empty (zero volume)"


def test_graceful_swept_run_collapses_microjog_no_inverted_fillet():
    """A routed centreline carrying a sub-profile micro-jog (a grid-remainder step
    or an off-grid nozzle cap) must NOT fillet into a tiny arc whose radius falls
    below the section half-width — that inverts the sweep's inner wall into a
    self-intersecting crush. The graceful path collapses such jogs and never emits
    an arc tighter than the half-width."""
    from ada.geom.curves import ArcLine
    from ada.topology.routing import (
        _collapse_short_legs,
        _orthogonalize_polyline,
        _polyline_to_directrix,
        _v_norm,
        _v_sub,
    )

    # A long straight run interrupted by a 0.1 m up-down micro-jog (like a port cap
    # landing 0.1 m off the lattice) — the classic 14k-vert crush source.
    path = [
        ada.Point(0.0, 0.0, 3.6),
        ada.Point(1.4, 0.0, 3.6),
        ada.Point(1.5, 0.0, 3.5),
        ada.Point(3.0, 0.0, 3.5),
    ]
    ortho = _orthogonalize_polyline(path)
    half = 0.5 * 0.3  # tray section max dim 0.3 -> half-width 0.15
    clean = _collapse_short_legs(ortho, half)
    directrix = _polyline_to_directrix(clean, 0.3, min_radius=half)

    # No arc chord shorter than the half-width survives (no inverted micro-arc).
    for s in directrix.segments:
        if isinstance(s, ArcLine):
            chord = _v_norm(_v_sub(tuple(s.end), tuple(s.start)))
            assert chord > half, f"micro-arc survived collapse: chord={chord:.3f}"


def test_strict_swept_run_raises_naming_offending_points(grid):
    """A *strict* duct/cable-tray run whose routed points are too close to fit its
    fixed catalog bend radius must raise, naming the offending point sequence — real
    products don't deform to fit, they fail so the route can be respaced."""
    a = ada.Equipment("A", 1.0, (1, 1, 0), (0, 0, 0), 1, 1, 1)
    a.add_port(ada.Port("out", (0.5, 0, 0.5), (1, 0, 0), ada.PortDirection.OUT, "process"))
    b = ada.Equipment("B", 1.0, (3, 2, 0), (0, 0, 0), 1, 1, 1)
    b.add_port(ada.Port("in", (-0.5, 0, 0.5), (-1, 0, 0), ada.PortDirection.IN, "process"))
    # 1 m grid: legs are ~1 m, but a 0.9 m bend needs >= 1.8 m of straight -> too tight.
    duct = ada.DuctSystem("HvacExhaust", strict=True, bend_radius=0.9).connect(a, "out").connect(b, "in")
    route_system(duct, grid)
    with pytest.raises(RoutingError) as exc:
        system_route_to_geometry(duct)
    msg = str(exc.value)
    assert "HvacExhaust" in msg
    assert "bend" in msg and "too short" in msg
    # names concrete coordinates so the user can find the spot
    assert "(" in msg and ")" in msg


def test_strict_swept_run_builds_uniform_radius_on_adequate_spacing():
    """With legs long enough for its fixed radius, a strict run builds clean regular
    segments — every bend is a full-radius arc, none clamped down."""
    from ada.geom.curves import ArcLine
    from ada.topology.routing import _orthogonalize_polyline, _polyline_to_directrix, _v_norm, _v_sub

    # A single 90 deg corner with 3 m legs, catalog radius 0.4 m: comfortably fits.
    path = [ada.Point(0, 0, 3), ada.Point(3, 0, 3), ada.Point(3, 3, 3)]
    ortho = _orthogonalize_polyline(path)
    directrix = _polyline_to_directrix(ortho, 0.4, min_radius=0.2, strict=True, run_name="D")
    arcs = [s for s in directrix.segments if isinstance(s, ArcLine)]
    assert len(arcs) == 1
    # a 90 deg arc of radius 0.4 has chord r*sqrt(2)
    chord = _v_norm(_v_sub(tuple(arcs[0].end), tuple(arcs[0].start)))
    assert abs(chord - 0.4 * (2 ** 0.5)) < 1e-6
