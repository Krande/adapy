"""Tests for the base spatial-topology entities (TopoSpace/Opening/Equipment).

These are pure pydantic value objects — most assertions need no CAD kernel. One
test builds a CellGraph carrying a TopoSpace as cell metadata to prove the
duck-typed ``name``/``get`` surface works in the generic graph.
"""

from __future__ import annotations

import subprocess
import sys

import ada
from ada.cad import active_backend
from ada.topology import CellGraph
from ada.topology.entities import (
    TopoEquipment,
    TopoOpening,
    TopoSpace,
    entity_field_help,
    from_ada_meta,
    from_ada_obj,
)


def test_entities_import_without_kernel():
    # The entities module is pure pydantic + ada; importing it must not drag a
    # CAD kernel into sys.modules.
    import os
    import pathlib

    code = (
        "import sys; import ada.topology.entities; "
        "bad=[m for m in sys.modules if m=='OCC' or m.startswith('OCC.') or m=='adacpp' or m.startswith('adacpp.')]; "
        "print('LOADED:'+','.join(sorted(bad)))"
    )
    # The subprocess does not inherit pytest's ``pythonpath=["src"]`` injection, so
    # hand it a PYTHONPATH derived from the already-imported ada package (changes only
    # where ``ada`` is found, not which modules import). Works installed or from source.
    src_dir = str(pathlib.Path(ada.__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in [src_dir, os.environ.get("PYTHONPATH", "")] if p)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split("LOADED:")[1].strip() == ""


def test_topospace_geometry_helpers():
    s = TopoSpace(NAME="A", X=0, Y=0, Z=0, DX=2, DY=3, DZ=4)
    assert tuple(s.get_p1()) == (0, 0, 0)
    assert tuple(s.get_p2()) == (2, 3, 4)
    # -X side: the four corners at x=0
    pts = s.get_side_points("-X")
    assert all(p.x == 0 for p in pts) and len(pts) == 4


def test_topospace_exclude_and_cantilever():
    # SE accepts a comma-separated string and merges with SE<i> flags.
    s = TopoSpace(NAME="A", X=0, Y=0, Z=0, DX=1, DY=1, DZ=1, SE="2,3", SE4=True)
    assert sorted(s.get_exclude_indices()) == [2, 3, 4]
    assert s.is_cantilevered_deck is False

    # Explicit function marks a cantilevered deck.
    deck = TopoSpace(NAME="D", X=0, Y=0, Z=0, DX=1, DY=1, DZ=1, FUNCTION="cantilevered_deck")
    assert deck.is_cantilevered_deck is True

    # Excluding all sides but the top (1) is detected as a cantilever too.
    deck2 = TopoSpace(NAME="D2", X=0, Y=0, Z=0, DX=1, DY=1, DZ=1, SE="0,2,3,4,5")
    assert deck2.is_cantilevered_deck is True


def test_topospace_is_cell_metadata_duck_type():
    s = TopoSpace(NAME="RoomA", X=0, Y=0, Z=0, DX=1, DY=1, DZ=1, PRIORITY=7)
    # The two members the generic graph relies on:
    assert s.name == "RoomA"
    assert s.get("PRIORITY", 0) == 7
    assert s.get("DOES_NOT_EXIST", "fallback") == "fallback"
    assert "PRIORITY" in s


def test_from_ada_obj_space():
    box = ada.PrimBox("RoomB", (1, 1, 1), (3, 4, 5), metadata={"FUNCTION": "space", "NAME": "RoomB", "PRIORITY": 2})
    ada.Part("AreaP") / box  # parent supplies the default AREA
    obj = from_ada_obj(box)
    assert isinstance(obj, TopoSpace)
    assert obj.NAME == "RoomB"
    assert obj.AREA == "AreaP"
    assert obj.PRIORITY == 2
    assert tuple(obj.get_p1()) == (1, 1, 1)
    assert tuple(obj.get_p2()) == (3, 4, 5)


def test_from_ada_obj_equipment_and_opening_function_routing():
    eq = from_ada_obj(
        ada.PrimBox(
            "EqA",
            (0, 0, 0),
            (1, 1, 1),
            metadata={
                "FUNCTION": "equipment",
                "NAME": "EqA",
                "SPACE_NAME": "RoomA",
                "SPACE_LOC": "FLOOR",
                "COGx": 0.0,
                "COGy": 0.0,
                "COGz": 0.5,
                "massDry": 100.0,
                "massCont": 0.0,
            },
        )
    )
    assert isinstance(eq, TopoEquipment)

    op = from_ada_obj(ada.PrimBox("OpA", (0, 0, 0), (1, 1, 1), metadata={"FUNCTION": "opening", "NAME": "OpA"}))
    assert isinstance(op, TopoOpening)
    assert op.USE_GLOBAL_COORDS is True


def test_from_ada_meta_roundtrip():
    m = from_ada_meta({"FUNCTION": "space", "NAME": "RoomC", "X": 0, "Y": 0, "Z": 0, "DX": 1, "DY": 1, "DZ": 1})
    assert isinstance(m, TopoSpace)
    assert m.NAME == "RoomC"


def test_cellgraph_carries_topospace_metadata():
    # A TopoSpace can be the cell's metadata: the graph reads .name / .get off it.
    be = active_backend()
    pairs = [
        (
            ada.PrimBox("RoomA", (0, 0, 0), (1, 1, 1)).solid_occ(),
            TopoSpace(NAME="RoomA", X=0, Y=0, Z=0, DX=1, DY=1, DZ=1, PRIORITY=1),
        ),
        (
            ada.PrimBox("RoomB", (1, 0, 0), (2, 1, 1)).solid_occ(),
            TopoSpace(NAME="RoomB", X=1, Y=0, Z=0, DX=1, DY=1, DZ=1, PRIORITY=2),
        ),
    ]
    cg = CellGraph.from_cell_solids(pairs, merge=True)
    assert sorted(c.name for c in cg.cells) == ["RoomA", "RoomB"]
    # PRIORITY drives internal-wall ownership (read via metadata.get on TopoSpace).
    assert len(cg.get_internal_walls()) == 1
    _ = be  # backend used to build the solids


# ── UI labels ────────────────────────────────────────────────────────────────────────────


def test_ui_labels_keys_are_fields():
    # Every UI_LABELS key must still be a field of its class -- a label for a renamed or removed
    # column would otherwise sit there doing nothing (entity_field_help filters it out silently,
    # which is the right runtime behaviour, but a stale key here is a mistake worth catching).
    for cls in (TopoSpace, TopoEquipment, TopoOpening):
        unknown = sorted(set(cls.UI_LABELS) - set(cls.model_fields))
        assert unknown == [], f"{cls.__name__}.UI_LABELS names fields it does not have: {unknown}"


def test_ui_labels_non_empty_for_the_generic_label_classes():
    # TopoSpace, TopoOpening and TopoEquipment are adapy's own entities that carry generic
    # (cryptic, abbreviated) column names needing a human-readable label. A class with no
    # UI_LABELS is still valid (every column just shows its own name), but these three are
    # expected to have at least one.
    for cls in (TopoSpace, TopoEquipment, TopoOpening):
        assert cls.UI_LABELS, f"{cls.__name__} has no UI_LABELS"


def test_entity_field_help_emits_a_labels_sibling_per_list():
    help_ = entity_field_help()
    # Every list that has a UI_LABELS-bearing entity gets a "<list>.labels" sibling, same shape
    # as the help text itself ({str: {str: str}}), so an installed server's clean_field_help (which
    # only keeps values of exactly that shape) passes it through untouched.
    assert "spaces.labels" in help_
    assert "equipments.labels" in help_
    assert "openings.labels" in help_
    for list_key, labels in (
        ("spaces", help_["spaces.labels"]),
        ("equipments", help_["equipments.labels"]),
        ("openings", help_["openings.labels"]),
    ):
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()), list_key
        assert labels, list_key
    # A couple of the actual values, pinned against Space's own SE0..SE5 side-exclude labels.
    assert help_["spaces.labels"]["SE4"] == "Exclude -X"
    assert help_["spaces.labels"]["SE5"] == "Exclude X"
    assert help_["spaces.labels"]["GIRDER_AS_PLATE"] == "Girder as plate"


def test_entity_field_help_drops_a_stale_label_key():
    # A UI_LABELS entry naming a column the class no longer has must not reach the viewer as a
    # label for a field that does not exist.
    class _StaleLabels(TopoSpace):
        UI_LABELS = {**TopoSpace.UI_LABELS, "NO_SUCH_COLUMN": "Ghost"}

    from ada.topology import entities as entities_mod

    saved = dict(entities_mod._FIELD_HELP_ENTITIES)
    try:
        entities_mod._FIELD_HELP_ENTITIES["spaces"] = _StaleLabels
        labels = entity_field_help()["spaces.labels"]
        assert "NO_SUCH_COLUMN" not in labels
        assert labels["SE0"] == "Exclude -Z"
    finally:
        entities_mod._FIELD_HELP_ENTITIES.clear()
        entities_mod._FIELD_HELP_ENTITIES.update(saved)
