"""Equipment-relocation engine: the self-collision helper, a cramped layout that
gets a fixing proposal, and a clean layout that gets none.

Uses built-in archetype equipment only (no per-scope catalog / DB)."""

from __future__ import annotations

import copy

import pytest

from ada.topo_model.relocate import (
    apply_relocations,
    propose_relocations,
    relocate_doc,
    run_self_collides,
)


def _eq(name, desc, x, y, z, lx, ly, lz, space="Room"):
    return {
        "NAME": name,
        "DESCRIPTION": desc,
        "SPACE_NAME": space,
        "SPACE_LOC": "FLOOR",
        "X": x,
        "Y": y,
        "Z": z,
        "LX": lx,
        "LY": ly,
        "LZ": lz,
        "COGx": 0,
        "COGy": 0,
        "COGz": lz / 2,
        "massDry": 100,
        "massCont": 0,
    }


#: This used to be a private helper here, inverting the origin convention by hand. It is now
#: ``relocate.apply_relocations``; the alias keeps the existing tests reading the same, and the two
#: agreeing is worth something -- the public function adds the origin *delta* to X/Y rather than
#: reconstructing the corner from ``to``, and these tests pin that the answers match.
_apply = apply_relocations


# --------------------------------------------------------------------------- #
# (a) run_self_collides
# --------------------------------------------------------------------------- #
def test_self_collides_true_for_foldback():
    # A run that doubles back on itself: the outgoing leg (y=0) and the return leg
    # (y=0.1) are 0.1 m apart — inside the run's 0.4 m body width (2 * 0.2).
    foldback = [(0, 0, 0), (2, 0, 0), (2, 0.1, 0), (0, 0.1, 0)]
    assert run_self_collides(foldback, half_extent=0.2) is True


def test_self_collides_false_for_monotonic():
    # A monotonic staircase never brings two non-adjacent segments within a body
    # width of each other.
    monotonic = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (2, 1, 0), (2, 2, 0)]
    assert run_self_collides(monotonic, half_extent=0.2) is False


def test_self_collides_needs_two_nonadjacent_segments():
    # A single corner (two segments, adjacent) can't self-collide.
    assert run_self_collides([(0, 0, 0), (1, 0, 0), (1, 1, 0)], half_extent=0.5) is False


def test_self_collides_false_when_far_apart():
    # The same fold-back shape but the legs are 2 m apart — well outside the body.
    wide = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    assert run_self_collides(wide, half_extent=0.2) is False


# --------------------------------------------------------------------------- #
# (b) a cramped doc gets a fixing proposal
# --------------------------------------------------------------------------- #
def _cramped_doc():
    # Two pumps sit close together near the -X wall, joined by a (wide) duct run
    # between their side-mounted suction nozzles (both facing -X). The duct can't
    # fit a clean run in the cramped corridor, so it doesn't route cleanly.
    return {
        "spaces": [{"NAME": "Room", "X": 0, "Y": 0, "Z": 0, "DX": 12, "DY": 12, "DZ": 2.0}],
        "equipments": [
            _eq("PumpA", "pump", 2, 5, 0, 1, 1, 1),
            _eq("PumpB", "pump", 3, 5, 0, 1, 1, 1),
        ],
        "systems": [
            {
                "NAME": "Cool",
                "TYPE": "duct",
                "MEDIUM": "air",
                "CONNECTIONS": [
                    {"EQUIPMENT": "PumpA", "PORT": "suction"},
                    {"EQUIPMENT": "PumpB", "PORT": "suction"},
                ],
            }
        ],
    }


def test_cramped_doc_gets_fixing_proposal():
    doc = _cramped_doc()
    result = propose_relocations(doc)

    # Baseline has a problem, and at least one relocation is proposed.
    assert result["baseline_problems"] >= 1
    assert len(result["proposals"]) >= 1

    prop = result["proposals"][0]
    assert prop["equipment"] in ("PumpA", "PumpB")
    assert prop["fixes"], "a proposal must name the systems it fixes"
    assert prop["from"] != prop["to"]
    # One move per moved piece of equipment (minimal by construction).
    moved = [p["equipment"] for p in result["proposals"]]
    assert len(moved) == len(set(moved))

    # Applying the proposals makes every run route cleanly.
    fixed = _apply(doc, result["proposals"])
    after = propose_relocations(fixed)
    assert after["baseline_problems"] == 0
    assert after["proposals"] == []


# --------------------------------------------------------------------------- #
# (c) a clean doc gets no proposals
# --------------------------------------------------------------------------- #
def test_clean_doc_gets_no_proposals():
    doc = {
        "spaces": [{"NAME": "Room", "X": 0, "Y": 0, "Z": 0, "DX": 20, "DY": 20, "DZ": 5}],
        "equipments": [
            _eq("PumpA", "pump", 3, 3, 0, 1, 1, 1),
            _eq("TankA", "tank", 12, 12, 0, 2, 2, 2),
        ],
        "systems": [
            {
                "NAME": "CW",
                "TYPE": "piping",
                "MEDIUM": "water",
                "CONNECTIONS": [
                    {"EQUIPMENT": "PumpA", "PORT": "discharge"},
                    {"EQUIPMENT": "TankA", "PORT": "inlet"},
                ],
            }
        ],
    }
    result = propose_relocations(doc)
    assert result["baseline_problems"] == 0
    assert result["proposals"] == []
    assert result["unresolved"] == []


# --------------------------------------------------------------------------- #
# (d) applying proposals, and the loop that closes back onto the layout
# --------------------------------------------------------------------------- #
def test_apply_relocations_does_not_mutate_the_input():
    doc = _cramped_doc()
    before = copy.deepcopy(doc)
    result = propose_relocations(doc)

    apply_relocations(doc, result["proposals"])

    assert doc == before, "apply_relocations must return a copy, never edit in place"


def test_apply_relocations_moves_the_origin_exactly_where_proposed():
    """The document places equipment by its corner and a proposal names its origin; the two differ
    by a constant half-extent, so applying the delta must land the origin on ``to``."""
    doc = _cramped_doc()
    result = propose_relocations(doc)
    proposal = result["proposals"][0]

    moved = apply_relocations(doc, result["proposals"])
    row = next(e for e in moved["equipments"] if e["NAME"] == proposal["equipment"])

    assert row["X"] + row["LX"] / 2 == pytest.approx(proposal["to"][0])
    assert row["Y"] + row["LY"] / 2 == pytest.approx(proposal["to"][1])


def test_an_unknown_equipment_name_is_skipped_not_raised():
    """A proposal list can outlive an edit to the document; dropping a stale move beats refusing
    the rest of them."""
    doc = _cramped_doc()
    stale = [{"equipment": "NoSuchPump", "from": [0, 0, 0], "to": [5, 0, 0]}]

    assert apply_relocations(doc, stale)["equipments"] == doc["equipments"]


def test_relocate_doc_clears_the_runs_that_did_not_route():
    """The feedback edge itself: plan, route, move what did not fit, route again."""
    doc = _cramped_doc()
    assert propose_relocations(doc)["baseline_problems"] >= 1

    moved, record = relocate_doc(doc)

    assert record["applied"], "nothing was applied, so nothing was fed back"
    assert record["baseline_problems"] >= 1
    assert record["unresolved"] == []
    assert propose_relocations(moved)["baseline_problems"] == 0


def test_relocate_doc_leaves_a_clean_document_alone():
    """A model that already routes costs one probe and no moves."""
    doc = {
        "spaces": [{"NAME": "Room", "X": 0, "Y": 0, "Z": 0, "DX": 20, "DY": 20, "DZ": 5}],
        "equipments": [
            _eq("PumpA", "pump", 3, 3, 0, 1, 1, 1),
            _eq("PumpB", "pump", 12, 12, 0, 1, 1, 1),
        ],
        "systems": [
            {
                "NAME": "Cool",
                "TYPE": "duct",
                "MEDIUM": "air",
                "CONNECTIONS": [
                    {"EQUIPMENT": "PumpA", "PORT": "suction"},
                    {"EQUIPMENT": "PumpB", "PORT": "suction"},
                ],
            }
        ],
    }

    moved, record = relocate_doc(doc)

    assert record["applied"] == []
    assert record["passes"] == 1
    assert moved == doc


def test_relocate_doc_never_mutates_the_document_it_was_given():
    doc = _cramped_doc()
    before = copy.deepcopy(doc)

    relocate_doc(doc)

    assert doc == before


# --------------------------------------------------------------------------- #
# (e) the probe has to agree with the compiler, or it proposes nothing
# --------------------------------------------------------------------------- #
def test_the_probe_grid_carries_the_port_lines_the_compiler_inserts():
    """This is the difference between the loop working and silently doing nothing.

    ``_route_and_collect`` mirrors the routing half of ``compile._build_systems``. It used to skip
    ``_augment_grid_with_ports``, which made it strictly *more permissive* than the compiler: it
    reported a model as routing cleanly that the compiler then failed to route, so
    ``propose_relocations`` found no baseline problems and proposed nothing.

    Asserted against a bare ``_routing_grid`` rather than against a routing outcome on purpose: a
    fixture cramped enough to fail in *both* probes cannot tell the two apart, so an outcome-based
    assertion here is vacuous. This compares the grids directly.
    """
    from ada.topo_model.compile import _routing_grid, _wire_systems
    from ada.topo_model.relocate import _build_equipment_map, _probe_grid
    from ada.topology.entities import TopoEquipment, TopoSpace

    # Equipment deliberately placed OFF the 0.5 m lattice, because that is the only case where
    # augmentation changes anything: a port that already lands on a grid line needs no new line,
    # which is why the cramped fixture above cannot tell the two grids apart.
    doc = _cramped_doc()
    doc["equipments"] = [
        _eq("PumpA", "pump", 2.3, 5.1, 0, 1, 1, 1),
        _eq("PumpB", "pump", 3.3, 5.1, 0, 1, 1, 1),
    ]
    spaces = [TopoSpace(**s) for s in doc["spaces"]]
    equipments = [TopoEquipment(**e) for e in doc["equipments"]]
    positions = {e.NAME: (float(e.X), float(e.Y)) for e in equipments}

    equipment_map = _build_equipment_map(equipments, positions, None)
    systems = _wire_systems(doc["systems"], _build_equipment_map(equipments, positions, None))
    assert systems, "fixture no longer wires; every assertion below would be vacuous"

    bare = _routing_grid(spaces, list(equipment_map.values()))
    probed = _probe_grid(spaces, equipment_map, systems)

    def lines(grid):
        return (len(grid.x_list), len(grid.y_list), len(grid.z_list))

    assert lines(probed) > lines(bare), "the probe grid gained no port lines over a bare routing grid"
    # And occupancy is stamped on it, so the blocking covers those new lines rather than
    # leaving them as a free corridor through an equipment box.
    assert probed.occupancy, "the probe grid carries no occupancy at all"
