"""Equipment-relocation engine: the self-collision helper, a cramped layout that
gets a fixing proposal, and a clean layout that gets none.

Uses built-in archetype equipment only (no per-scope catalog / DB)."""

from __future__ import annotations

import copy

from ada.topo_model.relocate import propose_relocations, run_self_collides


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


def _apply(doc: dict, proposals: list[dict]) -> dict:
    """Apply the relocation proposals to a copy of ``doc`` — moving each named
    equipment so its origin (X+LX/2, Y+LY/2, Z) lands on the proposed ``to``."""
    out = copy.deepcopy(doc)
    by_name = {e["NAME"]: e for e in out["equipments"]}
    for p in proposals:
        e = by_name[p["equipment"]]
        e["X"] = p["to"][0] - e["LX"] / 2
        e["Y"] = p["to"][1] - e["LY"] / 2
    return out


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
