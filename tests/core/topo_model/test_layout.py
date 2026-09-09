"""Rule-based topology generation: an equipment list in, decks + placements out.

Exercises ``ada.topo_model.layout`` on its own (no DEXPI, no importer): the
deterministic pack order, deck spill, the aisle/edge-clearance contract,
oversize + pre-placed + grouped items, the in-or-on-a-cell check, and — the one
that matters — a generated plan merged into a procedural document and compiled
to an assembly by the real engine.
"""

from __future__ import annotations

import math
import random

import pytest

import ada
from ada.topo_model.compile import build_procedural_assembly
from ada.topo_model.layout import (
    LayoutItem,
    LayoutRules,
    apply_layout,
    plan_layout,
    validate_equipment_in_cells,
)

# A deck a little bigger than the default, so the fixtures below pack into a
# predictable number of rows.
RULES = LayoutRules(max_length=20.0, max_width=10.0, deck_height=4.0, aisle=1.5, edge_clearance=1.0)


def _item(name, lx, ly, lz=1.0, **kw) -> LayoutItem:
    return LayoutItem(name=name, type_slug=kw.pop("slug", "tank"), lx=lx, ly=ly, lz=lz, mass=1000.0, **kw)


def _footprint(eq) -> tuple[float, float, float, float]:
    """The placed equipment's plan footprint (x0, y0, x1, y1) in DECK-LOCAL
    coordinates, honouring ROT_Z about the footprint centre."""
    a = math.radians(eq.ROT_Z or 0.0)
    fx = eq.LX * abs(math.cos(a)) + eq.LY * abs(math.sin(a))
    fy = eq.LX * abs(math.sin(a)) + eq.LY * abs(math.cos(a))
    cx, cy = eq.X + eq.LX / 2.0, eq.Y + eq.LY / 2.0
    return cx - fx / 2.0, cy - fy / 2.0, cx + fx / 2.0, cy + fy / 2.0


def _gap(a, b) -> float:
    """Separation between two footprints — the largest axis gap, negative when
    they overlap on both axes."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return max(max(bx0 - ax1, ax0 - bx1), max(by0 - ay1, ay0 - by1))


# --- ordering --------------------------------------------------------------- #
def test_pack_order_is_deterministic_and_by_descending_footprint():
    items = [
        _item("Small", 1.0, 1.0),
        _item("Big", 4.0, 3.0),
        _item("Mid", 2.0, 2.0),
        # Same footprint as Mid; the taller one packs first, then name order.
        _item("MidTall", 2.0, 2.0, lz=3.0),
        _item("AlsoMid", 2.0, 2.0),
    ]
    plan = plan_layout(items, RULES)
    assert [e.NAME for e in plan.placements] == ["Big", "MidTall", "AlsoMid", "Mid", "Small"]

    # Input order must not matter: any shuffle plans identically.
    rng = random.Random(7)
    for _ in range(5):
        shuffled = items[:]
        rng.shuffle(shuffled)
        other = plan_layout(shuffled, RULES)
        assert [e.model_dump() for e in other.placements] == [e.model_dump() for e in plan.placements]
        assert [s.model_dump() for s in other.spaces] == [s.model_dump() for s in plan.spaces]


# --- deck spill ------------------------------------------------------------- #
def test_full_deck_spills_onto_the_next_deck():
    # 4x4 items on an 18x8 usable deck: 3 fit per row (4+1.5 pitch fits 18 three
    # times, a fourth would need 22), and a second row would start at y=6.5 and
    # need to reach 10.5 > 9 -- so one row, 3 per deck, and 8 items take 3 decks.
    items = [_item(f"E{i:02d}", 4.0, 4.0, lz=2.0) for i in range(8)]
    plan = plan_layout(items, RULES)

    assert [s.NAME for s in plan.spaces] == ["Deck1", "Deck2", "Deck3"]
    assert [s.Z for s in plan.spaces] == [0.0, 4.0, 8.0]
    assert [s.DZ for s in plan.spaces] == [4.0, 4.0, 4.0]
    per_deck = {}
    for e in plan.placements:
        per_deck.setdefault(e.SPACE_NAME, []).append(e.NAME)
    assert per_deck == {
        "Deck1": ["E00", "E01", "E02"],
        "Deck2": ["E03", "E04", "E05"],
        "Deck3": ["E06", "E07"],
    }
    assert plan.unplaced == []
    # Placements are cell-local and seated on the deck floor.
    assert all(e.GLOBAL_COORDS is False and e.SPACE_LOC == "FLOOR" and e.Z == 0.0 for e in plan.placements)


def test_deck_height_defaults_to_tallest_item_plus_headroom():
    rules = LayoutRules(max_length=20.0, max_width=10.0, headroom=1.5)
    plan = plan_layout([_item("A", 2.0, 2.0, lz=3.0), _item("B", 1.0, 1.0, lz=6.0)], rules)
    assert plan.spaces[0].DZ == pytest.approx(7.5)


# --- clearances ------------------------------------------------------------- #
def test_aisle_and_edge_clearance_are_respected():
    rng = random.Random(3)
    items = [_item(f"E{i:02d}", rng.uniform(0.5, 3.0), rng.uniform(0.5, 3.0), lz=2.0) for i in range(20)]
    # One rotated item, so the reserved slot is the ROTATED footprint.
    items.append(_item("Spun", 3.0, 1.0, lz=2.0, rot_z=45.0))
    plan = plan_layout(items, RULES)
    assert plan.unplaced == []

    by_deck: dict[str, list] = {}
    for e in plan.placements:
        by_deck.setdefault(e.SPACE_NAME, []).append(e)

    spaces = {s.NAME: s for s in plan.spaces}
    for deck, placed in by_deck.items():
        space = spaces[deck]
        for e in placed:
            x0, y0, x1, y1 = _footprint(e)
            # Nothing outside its cell, and nothing inside the edge clearance.
            assert x0 >= RULES.edge_clearance - 1e-6, e.NAME
            assert y0 >= RULES.edge_clearance - 1e-6, e.NAME
            assert x1 <= space.DX - RULES.edge_clearance + 1e-6, e.NAME
            assert y1 <= space.DY - RULES.edge_clearance + 1e-6, e.NAME
            assert e.Z + e.LZ <= space.DZ + 1e-6, e.NAME
        for i, a in enumerate(placed):
            for b in placed[i + 1 :]:
                # No footprint overlap, and at least an aisle between any two.
                assert _gap(_footprint(a), _footprint(b)) >= RULES.aisle - 1e-6, (a.NAME, b.NAME)


# --- oversize --------------------------------------------------------------- #
def test_oversize_items_land_in_unplaced_and_are_named():
    items = [
        _item("Ok", 2.0, 2.0),
        _item("TooLong", 19.0, 2.0),  # > max_length - 2*edge_clearance
        _item("TooWide", 2.0, 9.0),  # > max_width  - 2*edge_clearance
    ]
    plan = plan_layout(items, RULES)

    assert sorted(plan.unplaced) == ["TooLong", "TooWide"]
    assert [e.NAME for e in plan.placements] == ["Ok"]
    # Named, never silently dropped: each appears in a warning of its own.
    assert all(any(name in w for w in plan.warnings) for name in plan.unplaced)


# --- pre-placed ------------------------------------------------------------- #
def test_fixed_items_keep_their_place_and_are_packed_around():
    fixed = _item("Anchor", 4.0, 4.0, lz=2.0, fixed=(6.0, 2.0, 0.0))
    items = [fixed] + [_item(f"E{i}", 3.0, 3.0, lz=2.0) for i in range(4)]
    plan = plan_layout(items, RULES)

    placed = {e.NAME: e for e in plan.placements}
    anchor = placed["Anchor"]
    assert (anchor.X, anchor.Y, anchor.Z) == (6.0, 2.0, 0.0)
    assert anchor.SPACE_NAME == "Deck1"

    # Everything on Deck1 keeps its aisle from the anchor (i.e. it was packed
    # around, not through).
    for e in plan.placements:
        if e.NAME == "Anchor" or e.SPACE_NAME != "Deck1":
            continue
        assert _gap(_footprint(anchor), _footprint(e)) >= RULES.aisle - 1e-6, e.NAME

    # A pre-placed item high up gets the deck its elevation falls on.
    upstairs = plan_layout([_item("High", 2.0, 2.0, fixed=(3.0, 3.0, 8.0))], RULES)
    high = upstairs.placements[0]
    assert high.SPACE_NAME == "Deck3" and high.Z == 0.0
    assert [s.Z for s in upstairs.spaces] == [0.0, 4.0, 8.0]


# --- grouping --------------------------------------------------------------- #
def test_grouped_items_are_packed_contiguously():
    items = [
        _item("BigA", 4.0, 4.0, group="A"),
        _item("SmallA", 1.0, 1.0, group="A"),
        _item("MidB", 3.0, 3.0, group="B"),
        _item("SmallB", 1.5, 1.5, group="B"),
        _item("Loose", 2.0, 2.0),
    ]
    order = [e.NAME for e in plan_layout(items, RULES).placements]
    # Groups keep their members together, ranked by their largest member; the
    # ungrouped item sorts on its own size (between the two groups' leads).
    assert order == ["BigA", "SmallA", "MidB", "SmallB", "Loose"]

    # group_key overrides LayoutItem.group without changing the signature.
    keyed = LayoutRules(
        max_length=20.0, max_width=10.0, deck_height=4.0, group_key=lambda it: it.name.startswith("Small")
    )
    order = [e.NAME for e in plan_layout(items, keyed).placements]
    assert order == ["BigA", "MidB", "Loose", "SmallB", "SmallA"]


# --- the in-or-on-a-cell rule ----------------------------------------------- #
def test_validate_equipment_in_cells_accepts_a_plan_and_catches_a_stray():
    plan = plan_layout([_item("A", 3.0, 3.0), _item("B", 2.0, 2.0)], RULES)
    doc = apply_layout({}, plan)
    assert validate_equipment_in_cells(doc) == []

    # Shove one equipment out past the deck's +X face.
    doc["equipments"][1]["X"] = 40.0
    problems = validate_equipment_in_cells(doc)
    assert len(problems) == 1 and problems[0].startswith("B:")

    # An unknown cell and missing coordinates are reported too.
    doc["equipments"][1]["X"] = 3.0
    doc["equipments"][1]["SPACE_NAME"] = "NoSuchDeck"
    assert validate_equipment_in_cells(doc)[0].startswith("B: SPACE_NAME 'NoSuchDeck'")

    doc["equipments"][0].pop("LZ")
    assert "missing coordinates ['LZ']" in validate_equipment_in_cells(doc)[0]


def test_validate_accepts_a_roof_seated_equipment():
    plan = plan_layout([_item("A", 3.0, 3.0)], RULES)
    doc = apply_layout({}, plan)
    doc["equipments"][0]["SPACE_LOC"] = "ROOF"  # sits ON the cell, not in it
    assert validate_equipment_in_cells(doc) == []


# --- document merge --------------------------------------------------------- #
def test_apply_layout_merges_without_disturbing_the_rest_of_the_doc():
    base = {
        "engine": "adapy-default",
        "spaces": [{"NAME": "Existing", "X": 0, "Y": 0, "Z": 0, "DX": 1, "DY": 1, "DZ": 1}],
        "systems": [{"NAME": "CW", "TYPE": "piping", "CONNECTIONS": []}],
        "openings": [],
    }
    plan = plan_layout([_item("A", 3.0, 3.0)], RULES)
    out = apply_layout(base, plan)

    assert out["engine"] == "adapy-default"
    assert out["systems"] == base["systems"]
    assert [s["NAME"] for s in out["spaces"]] == ["Existing", "Deck1"]
    assert [e["NAME"] for e in out["equipments"]] == ["A"]
    # The input document is not mutated.
    assert "equipments" not in base and len(base["spaces"]) == 1

    # Re-applying replaces the generated entries in place rather than appending.
    again = apply_layout(out, plan)
    assert [s["NAME"] for s in again["spaces"]] == ["Existing", "Deck1"]
    assert [e["NAME"] for e in again["equipments"]] == ["A"]


# --- the integration that matters ------------------------------------------- #
def test_plan_compiles_through_the_procedural_engine():
    """A generated plan is a real procedural document: merge it, hand it to
    ProceduralBuilder.from_dict via build_procedural_assembly, get a structure
    with the equipment placed on its decks and the system routed between them."""
    items = [
        LayoutItem("T-100", "tank", lx=2.0, ly=2.0, lz=2.0, mass=2000.0),
        LayoutItem("P-100", "pump", lx=1.0, ly=1.0, lz=1.0, mass=500.0),
        LayoutItem("SB-100", "switchboard", lx=0.8, ly=0.4, lz=1.2, mass=300.0, rot_z=-90.0),
    ]
    plan = plan_layout(items, LayoutRules(max_length=12.0, max_width=8.0, deck_height=4.0))
    assert plan.unplaced == []

    doc = apply_layout(
        {
            "systems": [
                {
                    "NAME": "CoolingWater",
                    "TYPE": "piping",
                    "MEDIUM": "water",
                    "CONNECTIONS": [
                        {"EQUIPMENT": "P-100", "PORT": "discharge"},
                        {"EQUIPMENT": "T-100", "PORT": "inlet"},
                    ],
                }
            ]
        },
        plan,
    )
    assert validate_equipment_in_cells(doc) == []

    assembly = build_procedural_assembly(doc, name="LayoutModel")
    placed = {p.name: p for p in assembly.get_all_parts_in_assembly() if isinstance(p, ada.Equipment)}
    assert set(placed) == {"T-100", "P-100", "SB-100"}

    # Each equipment landed at its planned world position (deck origin is (0,0,0)
    # for Deck1, so the local X/Y are the world X/Y of the footprint corner).
    by_name = {e.NAME: e for e in plan.placements}
    for name, obj in placed.items():
        eq = by_name[name]
        assert obj.origin[0] == pytest.approx(eq.X + eq.LX / 2.0)
        assert obj.origin[1] == pytest.approx(eq.Y + eq.LY / 2.0)
        assert obj.origin[2] == pytest.approx(0.0)

    # The structural blueprint built the deck, and the run was routed.
    assert list(assembly.get_all_physical_objects(by_type=ada.Beam))
    assert [p.name for p in assembly.parts.values() if p.name == "Systems"]
