"""Routing a branched system built with ``System.add_leg``: the trunk polyline and the metadata
the router leaves behind (see ``ada.api.systems.branch_meta``)."""

from __future__ import annotations

import ada
from ada.api.systems.branch_meta import (
    BRANCH_KEY,
    BRANCH_ROUTE_JUNCTION_POINT,
    BRANCH_ROUTE_KEY,
    BRANCH_ROUTE_TRUNK,
)
from ada.topology.grid import CellGrid
from ada.topology.routing import route_system


def _tee_system() -> tuple[ada.PipingSystem, ada.Equipment, ada.Equipment, ada.Equipment, ada.Equipment]:
    """A tee at (4, 4, 0) with three leaves: A to the west, B to the east (the two farthest apart,
    so they form the trunk) and C to the north."""
    tee = ada.Equipment("TEE", 1.0, (0, 0, 0), (4, 4, 0), 0.2, 0.2, 0.2)
    tee.add_port(ada.Port("n1", (-0.1, 0, 0), (-1, 0, 0), ada.PortDirection.IN))
    tee.add_port(ada.Port("n2", (0.1, 0, 0), (1, 0, 0), ada.PortDirection.OUT))
    tee.add_port(ada.Port("n3", (0, 0.1, 0), (0, 1, 0), ada.PortDirection.OUT))

    a = ada.Equipment("A", 1.0, (0, 0, 0), (0, 4, 0), 0.2, 0.2, 0.2)
    a.add_port(ada.Port("out", (0.1, 0, 0), (1, 0, 0), ada.PortDirection.OUT))
    b = ada.Equipment("B", 1.0, (0, 0, 0), (8, 4, 0), 0.2, 0.2, 0.2)
    b.add_port(ada.Port("in", (-0.1, 0, 0), (-1, 0, 0), ada.PortDirection.IN))
    c = ada.Equipment("C", 1.0, (0, 0, 0), (4, 8, 0), 0.2, 0.2, 0.2)
    c.add_port(ada.Port("in", (0, -0.1, 0), (0, -1, 0), ada.PortDirection.IN))

    system = (
        ada.PipingSystem("L-301")
        .add_leg("L-301/1", (a, "out"), (tee, "n1"))
        .add_leg("L-301/2", (tee, "n2"), (b, "in"))
        .add_leg("L-301/3", (tee, "n3"), (c, "in"))
    )
    return system, tee, a, b, c


def _grid() -> CellGrid:
    return CellGrid.from_bounds((-1, -1, -1), (9, 9, 1), spacing=1.0)


def test_the_trunk_runs_leaf_to_junction_to_leaf_without_a_back_jump():
    system, tee, a, b, _c = _tee_system()
    route_system(system, _grid())

    path = [tuple(p) for p in system.routed_path]
    assert path[0] == tuple(a.get_port("out").get_global_position())
    # Every leg routes leaf -> junction, so the second trunk leg has to be walked backwards; the
    # trunk used to end on the tee (junction A -> leaf B appended as routed, retracing leg B).
    assert path[-1] == tuple(b.get_port("in").get_global_position())

    # The tee sits between the two leaves, visited once: the trunk passes through it rather than
    # bouncing back out to a leaf and returning.
    junction_hits = [i for i, p in enumerate(path) if abs(p[0] - 4.0) < 0.5 and abs(p[1] - 4.0) < 0.5]
    assert junction_hits, "the trunk must pass through the tee"
    assert path[junction_hits[-1] + 1 :], "the trunk must continue past the tee to leaf B"
    # Monotone in x from A (west) to B (east): a back-jump would reverse direction.
    xs = [p[0] for p in path]
    assert all(x2 >= x1 - 1e-9 for x1, x2 in zip(xs, xs[1:])), xs
    # No point is visited twice.
    assert len(set(path)) == len(path)


def test_routing_records_its_result_under_its_own_metadata_key():
    system, _tee, _a, _b, _c = _tee_system()
    route_system(system, _grid())

    route_meta = system.metadata[BRANCH_ROUTE_KEY]
    assert set(route_meta[BRANCH_ROUTE_TRUNK]) == {"L-301/1", "L-301/2"}
    assert len(route_meta[BRANCH_ROUTE_JUNCTION_POINT]) == 3
    assert all(isinstance(c, float) for c in route_meta[BRANCH_ROUTE_JUNCTION_POINT])
    # The import-side dict (junction id, legs, per-leg source metadata) is another producer's;
    # routing neither writes nor clobbers it.
    assert BRANCH_KEY not in system.metadata


def test_the_import_side_branch_dict_survives_routing():
    system, _tee, _a, _b, _c = _tee_system()
    system.metadata[BRANCH_KEY] = {"junction_id": "tee-1", "legs": ["L-301/1", "L-301/2", "L-301/3"]}
    route_system(system, _grid())
    assert system.metadata[BRANCH_KEY] == {"junction_id": "tee-1", "legs": ["L-301/1", "L-301/2", "L-301/3"]}
    assert BRANCH_ROUTE_KEY in system.metadata
