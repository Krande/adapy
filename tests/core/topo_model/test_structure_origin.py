"""Per-structure grouping as a core feature.

The ONE wire shape is ``groups[]`` (``CellGroup``: name/blueprint/include/origin).
``ada.topo_model.structures`` maps it to the builder's ``TopoStructure`` objects
(and the legacy ``structures[]`` shape, a documented input, merges in by NAME);
``ProceduralBuilder`` places one wrapper ``Part`` per structure, scopes its
spaces/openings/loft members to it (own ``STRUCTURE_NAME``, else its
``SPACE_NAME``'s host space's), and offsets equipment into WORLD coordinates
(structure origin + the existing space offset). adapy-default now advertises
``supports_grouping: True`` (see ``test_engine_catalog.py``)."""

from __future__ import annotations

import ada
from ada.comms.rest.procedural import doc_content_hash, validate_doc
from ada.topo_model import ProceduralBuilder
from ada.topo_model.compile import compile_procedural_doc

MASS = dict(COGx=0, COGy=0, COGz=0.5, massDry=0, massCont=0, LX=1, LY=1, LZ=1)


def _is_glb(b: bytes) -> bool:
    return b[:4] == b"glTF"


def _box(name: str, structure: str | None, x: float, y: float, z: float, dx=5.0, dy=5.0, dz=3.0) -> dict:
    d = {"NAME": name, "X": x, "Y": y, "Z": z, "DX": dx, "DY": dy, "DZ": dz}
    if structure is not None:
        d["STRUCTURE_NAME"] = structure
    return d


# Two structures: A (no origin -> [0,0,0]) and B (origin [20,5,0]), each with a space.
DOC = {
    "schema_version": "1.1",
    "groups": [
        {"name": "A", "blueprint": "steel_stru"},
        {"name": "B", "blueprint": "steel_stru", "origin": [20, 5, 0]},
    ],
    "spaces": [
        _box("SA", "A", 0, 0, 0),
        _box("SB", "B", 0, 0, 0),
    ],
}


def test_groups_map_to_structures():
    pb = ProceduralBuilder.from_dict(DOC)
    assert {s.NAME for s in pb.structures} == {"A", "B"}
    assert {s.NAME: s.origin() for s in pb.structures} == {"A": (0.0, 0.0, 0.0), "B": (20.0, 5.0, 0.0)}


def test_structure_part_placed():
    pb = ProceduralBuilder.from_dict(DOC)
    pb.build_structure()
    assert {"A", "B"} <= set(pb.assembly.parts)
    b = pb.assembly.parts["B"]
    assert tuple(float(v) for v in b.placement.origin) == (20.0, 5.0, 0.0)
    a = pb.assembly.parts["A"]
    assert tuple(float(v) for v in a.placement.origin) == (0.0, 0.0, 0.0)


def test_equipment_lands_in_structure_frame():
    """SB sits at (3,2,0) in B's own LOCAL frame -- not at B's origin -- so this fails if the
    space offset were ever dropped and only the structure offset applied."""
    doc = {
        **DOC,
        "spaces": [
            _box("SA", "A", 0, 0, 0),
            _box("SB", "B", 3, 2, 0),
        ],
        "equipments": [
            # Local to its space (SB) AND its structure (B): world = B's origin + SB's local
            # offset + E_B's own local X/Y/Z.
            {
                "NAME": "E_B",
                "SPACE_NAME": "SB",
                "STRUCTURE_NAME": "B",
                "SPACE_LOC": "FLOOR",
                "X": 1,
                "Y": 1,
                "Z": 0,
                **MASS,
            },
            # GLOBAL_COORDS: "global" is now to the STRUCTURE's frame, not the true world, when
            # structures are placed -- so it still carries B's origin, but drops SB's own offset.
            {
                "NAME": "E_G",
                "SPACE_NAME": "SB",
                "STRUCTURE_NAME": "B",
                "SPACE_LOC": "ROOF",
                "GLOBAL_COORDS": True,
                "X": 2,
                "Y": 3,
                "Z": 0.5,
                **MASS,
            },
        ],
    }
    pb = ProceduralBuilder.from_dict(doc)
    pb.build_structure()
    pb.build_equipment()
    objs = {o.name: o for o in pb.assembly.parts["Equipment"].get_all_physical_objects()}
    e_b, e_g = objs["E_B"], objs["E_G"]
    # E_B's min corner = B's origin (20,5,0) + SB's own local offset (3,2,0) + E_B's own local
    # X/Y/Z (1,1,0).
    assert tuple(float(c) for c in e_b.p1) == (24.0, 8.0, 0.0)
    # E_G: GLOBAL_COORDS drops the SPACE (SB) offset but not the structure's -- min corner = B's
    # origin + E_G's own X/Y/Z, unaffected by SB moving.
    assert tuple(float(c) for c in e_g.p1) == (22.0, 8.0, 0.5)


def test_opening_without_structure_name_is_cut():
    """An opening naming only its SPACE_NAME (no STRUCTURE_NAME) is scoped to its space's
    structure via `structure_of`, not dropped (the finding this item fixes)."""
    doc = {
        **DOC,
        "blueprint": {"enclosed_cells": ["SB"]},
        "openings": [
            {
                "NAME": "Door1",
                "SUBTYPE": "door",
                "SPACE_NAME": "SB",
                # No STRUCTURE_NAME: resolved through SB's own STRUCTURE_NAME ("B").
                "USE_GLOBAL_COORDS": True,
                "X": -0.2,
                "Y": 1.0,
                "Z": 0.0,
                "DX": 0.4,
                "DY": 1.0,
                "DZ": 2.1,
            }
        ],
    }
    pb = ProceduralBuilder.from_dict(doc)
    assert len(pb.openings) == 1, "the opening is not dropped by from_dict/_without_excluded"
    pb.build_structure()
    b = pb.assembly.parts["B"]
    cut = [p for p in b.get_all_physical_objects(by_type=ada.Plate) if p.booleans]
    assert len(cut) == 1, "the opening cut a wall plate of structure B, not nowhere"


def test_loft_in_structure_frame():
    from ada.topology.entities import LoftStation, TopoLoftMember

    member = TopoLoftMember(
        NAME="Column",
        STRUCTURE_NAME="B",
        STATIONS=[
            LoftStation(TYPE="rectangle", X=0.0, Y=0.0, Z=0.0, WIDTH=1.0, HEIGHT=1.0),
            LoftStation(TYPE="rectangle", X=0.0, Y=0.0, Z=2.0, WIDTH=1.0, HEIGHT=1.0),
        ],
        SURFACE_ONLY=True,
    )
    pb = ProceduralBuilder(
        spaces=[], structures=[s for s in ProceduralBuilder.from_dict(DOC).structures], loft_members=[member]
    )
    pb.build_structure()
    pb.build_lofts()
    # The loft's "Lofts" part is nested under B's wrapper (so B's placement offsets it), not
    # sitting loose on the assembly.
    b = pb.assembly.parts["B"]
    assert "Lofts" in b.parts
    assert "Lofts" not in pb.assembly.parts


def test_per_structure_blueprint():
    """A group's own `blueprint` overrides the model's default for ITS structure only."""
    doc = {
        **DOC,
        "groups": [
            {"name": "A", "blueprint": "steel_stru"},
            {"name": "B", "blueprint": "none", "origin": [20, 5, 0]},
        ],
    }
    pb = ProceduralBuilder.from_dict(doc, blueprint_name="steel_stru")
    pb.build_structure()
    assert set(pb.topologies) == {"A"}, "B used blueprint 'none': no topology/blueprint built for it"
    a_part = pb.assembly.parts["A"]
    b_part = pb.assembly.parts["B"]
    # 'none' renders the raw space box wrapped in a "Spaces" part; 'steel_stru' builds real walls.
    assert "Spaces" in b_part.parts
    assert any(p.name != "Spaces" for p in a_part.parts.values())


def test_legacy_structures_only_doc():
    """The legacy `structures[]` shape (no `groups` at all) still places, unmerged -- the
    documented backward-compatible input `structures_from_doc` reads when there is no group."""
    doc = {
        "structures": [{"NAME": "S1", "X": 0, "Y": 0, "Z": 0}, {"NAME": "S2", "X": 20, "Y": 0, "Z": 0}],
        "spaces": [_box("C1", "S1", 0, 0, 0), _box("C2", "S2", 0, 0, 0)],
    }
    pb = ProceduralBuilder.from_dict(doc)
    assert {s.NAME: s.origin() for s in pb.structures} == {"S1": (0.0, 0.0, 0.0), "S2": (20.0, 0.0, 0.0)}
    assert _is_glb(pb.compile())


def test_group_wins_over_structure():
    """A group and a legacy `structures[]` entry of the same name: the group's origin wins."""
    doc = {
        "groups": [{"name": "S1", "blueprint": "steel_stru", "origin": [99, 0, 0]}],
        "structures": [{"NAME": "S1", "X": 1, "Y": 2, "Z": 3}],
        "spaces": [_box("C1", "S1", 0, 0, 0)],
    }
    pb = ProceduralBuilder.from_dict(doc)
    assert len(pb.structures) == 1
    assert pb.structures[0].origin() == (99.0, 0.0, 0.0)


def test_excluded_group_drops_its_equipment():
    """A legacy structure excluded via the GROUP's `include: false` also drops its equipment
    (not only its spaces) -- the `_without_excluded` fix: before, only `groups[].include` was
    read, so a legacy `structures[].INCLUDE: false` left its equipment floating at the top level."""
    doc = {
        **DOC,
        "groups": [
            {"name": "A", "blueprint": "steel_stru"},
            {"name": "B", "blueprint": "steel_stru", "origin": [20, 5, 0], "include": False},
        ],
        "equipments": [
            {
                "NAME": "E_B",
                "SPACE_NAME": "SB",
                "STRUCTURE_NAME": "B",
                "SPACE_LOC": "FLOOR",
                "X": 1,
                "Y": 1,
                "Z": 0,
                **MASS,
            },
            {
                "NAME": "E_A",
                "SPACE_NAME": "SA",
                "STRUCTURE_NAME": "A",
                "SPACE_LOC": "FLOOR",
                "X": 1,
                "Y": 1,
                "Z": 0,
                **MASS,
            },
        ],
    }
    pb = ProceduralBuilder.from_dict(doc)
    assert {e.NAME for e in pb.equipments} == {"E_A"}, "E_B (structure B, excluded) was dropped by from_dict"
    pb.build_structure()
    assert set(pb.topologies) == {"A"}


def test_ungrouped_spaces_still_build_beside_groups():
    """A doc with ONE group plus a space naming no structure at all used to drop that space from
    the compiled model entirely (every space not claimed by a placed structure vanished, with no
    warning). It must still build: as an implicit root group at the origin, alongside the named
    structure, with its own openings/equipment following it there."""
    doc = {
        "groups": [{"name": "A", "blueprint": "steel_stru"}],
        "blueprint": {"enclosed_cells": ["FREE"]},
        "spaces": [
            _box("SA", "A", 0, 0, 0),
            _box("FREE", None, 0, 0, 0),  # no STRUCTURE_NAME at all
        ],
        "equipments": [{"NAME": "E_FREE", "SPACE_NAME": "FREE", "SPACE_LOC": "FLOOR", "X": 1, "Y": 1, "Z": 0, **MASS}],
        "openings": [
            {
                "NAME": "FreeDoor",
                "SUBTYPE": "door",
                "SPACE_NAME": "FREE",
                "USE_GLOBAL_COORDS": True,
                "X": -0.2,
                "Y": 1.0,
                "Z": 0.0,
                "DX": 0.4,
                "DY": 1.0,
                "DZ": 2.1,
            }
        ],
    }
    pb = ProceduralBuilder.from_dict(doc)
    assert {s.NAME for s in pb.spaces} == {"SA", "FREE"}, "FREE is not dropped before build_structure runs"
    assert len(pb.openings) == 1, "FreeDoor is not dropped before build_structure runs either"
    pb.build_structure()
    # The named structure still gets its own wrapper Part; the free group's topology is a SECOND
    # one, registered under the builder's own name, not silently skipped.
    assert "A" in pb.assembly.parts
    assert set(pb.topologies) == {"A", pb.name}, "the ungrouped space's topology must still be built"
    # It sits loose on the assembly (no wrapper, since it is at the origin) -- DIRECT children of
    # the assembly, the same place the no-structures path would have put it, not nested under A's
    # wrapper Part.
    free_parts = [p for name, p in pb.assembly.parts.items() if name != "A"]
    assert free_parts, "the ungrouped root group must still sit on the assembly"
    # Its door still cut a wall plate -- the ungrouped group's own openings are not dropped either.
    cut = [p for part in free_parts for p in part.get_all_physical_objects(by_type=ada.Plate) if p.booleans]
    assert len(cut) == 1, "FreeDoor cut a wall plate of the ungrouped root group, not nowhere"
    pb.build_equipment()
    # Its equipment, seated with no structure offset (origin (0,0,0)), still lands at FREE's own
    # local position -- not dropped, and not shifted by any structure's origin.
    e_free = next(o for o in pb.assembly.parts["Equipment"].get_all_physical_objects() if o.name == "E_FREE")
    assert tuple(float(c) for c in e_free.p1) == (1.0, 1.0, 0.0)
    # The GLB still compiles with both the grouped and the ungrouped space present.
    assert _is_glb(pb.compile())


def test_to_doc_writes_groups_not_structures():
    pb = ProceduralBuilder.from_dict(DOC)
    doc = pb.to_doc()
    assert "structures" not in doc
    groups = {g["name"]: g for g in doc["groups"]}
    assert groups["B"]["origin"] == (20.0, 5.0, 0.0)
    assert groups["B"]["blueprint"] == "steel_stru"
    assert "origin" not in groups["A"], "A sits at [0,0,0]: byte-identical to not having one"


def test_carries_the_sibling_tools_extra_columns_as_extra_group_keys():
    """TopoStructure's MODEL_INPUT/BLUEPRINT/CONDITION_NAME (another tool's Structures-sheet
    columns) are carried as extra keys on the GROUP, never acted on, so nothing is lost."""
    doc = {
        "structures": [
            {
                "NAME": "S1",
                "X": 0,
                "Y": 0,
                "Z": 0,
                "MODEL_INPUT": "SECTION_LOFT",
                "BLUEPRINT": "Jacket",
                "CONDITION_NAME": "Op",
            }
        ],
        "spaces": [_box("C1", "S1", 0, 0, 0)],
    }
    pb = ProceduralBuilder.from_dict(doc)
    out = pb.to_doc()
    group = next(g for g in out["groups"] if g["name"] == "S1")
    assert group["MODEL_INPUT"] == "SECTION_LOFT"
    assert group["BLUEPRINT"] == "Jacket"
    assert group["CONDITION_NAME"] == "Op"
    # adapy's OWN per-group blueprint key ("blueprint", lowercase) is untouched by this.
    assert "blueprint" not in group


def test_sibling_tools_extra_columns_survive_a_second_round_trip():
    """The ONE wire shape is ``groups[]``: once a document has round-tripped through ``to_doc``
    once, its sibling-tool columns live directly on the GROUP entry, not in a separate
    ``structures[]`` list any more. A second ``from_dict``/``to_doc`` hop must still carry them --
    not lose them because ``structures_from_doc`` only ever looked at a legacy ``structures[]``
    entry for these columns."""
    doc = {
        "structures": [
            {
                "NAME": "S1",
                "X": 0,
                "Y": 0,
                "Z": 0,
                "MODEL_INPUT": "SECTION_LOFT",
                "BLUEPRINT": "Jacket",
                "CONDITION_NAME": "Op",
            }
        ],
        "spaces": [_box("C1", "S1", 0, 0, 0)],
    }
    once = ProceduralBuilder.from_dict(doc).to_doc()
    twice = ProceduralBuilder.from_dict(once).to_doc()
    group = next(g for g in twice["groups"] if g["name"] == "S1")
    assert group["MODEL_INPUT"] == "SECTION_LOFT"
    assert group["BLUEPRINT"] == "Jacket"
    assert group["CONDITION_NAME"] == "Op"


def test_validate_doc_cellgroup_origin():
    """A malformed origin is dropped to None (never raises), and a group with no origin at all
    is byte- and hash-identical to a doc that never carried the field."""
    doc = {"spaces": [{"NAME": "c1"}], "groups": [{"name": "A", "blueprint": "steel_stru", "origin": "not-a-tuple"}]}
    out = validate_doc(doc)
    group = out["groups"][0]
    assert "origin" not in group, "a malformed origin is dropped, not raised on or passed through"

    plain = validate_doc({"spaces": [{"NAME": "c1"}], "groups": [{"name": "A", "blueprint": "steel_stru"}]})
    assert doc_content_hash(out) == doc_content_hash(plain), "a dropped-to-None origin hashes as absent"

    with_origin = validate_doc(
        {"spaces": [{"NAME": "c1"}], "groups": [{"name": "A", "blueprint": "steel_stru", "origin": [1, 2, 3]}]}
    )
    assert doc_content_hash(with_origin) != doc_content_hash(plain), "a real origin changes the cache key"


def test_compile_procedural_doc_smoke():
    glb = compile_procedural_doc(DOC)
    assert _is_glb(glb)
    # Grouping is now acted on: the two-structure GLB differs from a single-structure version of
    # roughly the same spaces (see test_engine_catalog.py for the dedicated assertion).
    assert len(glb) > 0
