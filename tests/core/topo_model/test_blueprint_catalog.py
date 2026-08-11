"""Per-engine blueprint registry + the doc-carried ``blueprint_name`` the
compiler dispatches on (user-selectable structural blueprint)."""

from __future__ import annotations

from ada.topo_model import (
    procedural_blueprint_specs,
    register_procedural_blueprint,
)
from ada.topo_model.compile import compile_procedural_doc

_DOC = {
    "spaces": [
        {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ],
}


def _is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


# ── registry ─────────────────────────────────────────────────────────


def test_default_engine_advertises_steel_stru_and_none():
    specs = {s["slug"]: s for s in procedural_blueprint_specs("adapy-default")}
    assert set(specs) == {"steel_stru", "none"}
    # steel_stru is first -> the engine default.
    assert procedural_blueprint_specs("adapy-default")[0]["slug"] == "steel_stru"
    assert all(s["engine"] == "adapy-default" for s in specs.values())


def test_register_is_idempotent_and_engine_scoped():
    register_procedural_blueprint("some-engine", "raft", "Raft foundation", description="demo")
    register_procedural_blueprint("some-engine", "raft", "Raft foundation")  # replace, not dup
    rafts = [s for s in procedural_blueprint_specs("some-engine") if s["slug"] == "raft"]
    assert len(rafts) == 1
    assert rafts[0]["name"] == "Raft foundation"
    # union view carries every engine's blueprints, each tagged with its engine.
    union = procedural_blueprint_specs()
    assert {"engine", "slug", "name", "description"} <= set(rafts[0])
    assert any(s["slug"] == "raft" and s["engine"] == "some-engine" for s in union)
    assert "raft" not in {s["slug"] for s in procedural_blueprint_specs("adapy-default")}


def test_unknown_engine_has_no_blueprints():
    assert procedural_blueprint_specs("nope-not-registered") == []


# ── doc-carried blueprint_name (compile dispatch) ────────────────────


def test_doc_blueprint_name_none_compiles_raw_boxes():
    # A doc naming blueprint_name="none" compiles the raw boxes even though the
    # kwarg default is steel_stru — the doc value wins.
    doc = {**_DOC, "blueprint_name": "none"}
    glb = compile_procedural_doc(doc)
    assert _is_glb(glb)
    # Raw boxes are smaller than the framed steel structure.
    steel = compile_procedural_doc({**_DOC, "blueprint_name": "steel_stru"})
    assert len(glb) < len(steel)


def test_doc_blueprint_name_overrides_kwarg_default():
    # blueprint_name in the doc wins over an explicit conflicting kwarg.
    doc = {**_DOC, "blueprint_name": "none"}
    from_doc = compile_procedural_doc(doc, blueprint_name="steel_stru")
    raw = compile_procedural_doc(_DOC, blueprint_name="none")
    assert len(from_doc) == len(raw)


def test_legacy_doc_without_blueprint_name_defaults_to_steel_stru():
    # Backward compatible: a doc with no blueprint_name uses the kwarg fallback.
    legacy = compile_procedural_doc(_DOC)  # kwarg default steel_stru
    explicit = compile_procedural_doc({**_DOC, "blueprint_name": "steel_stru"})
    assert len(legacy) == len(explicit)


def test_unknown_doc_blueprint_name_falls_back_to_kwarg():
    # An unrecognised name is ignored (kwarg fallback), never an error.
    doc = {**_DOC, "blueprint_name": "bogus"}
    glb = compile_procedural_doc(doc, blueprint_name="none")
    assert len(glb) == len(compile_procedural_doc(_DOC, blueprint_name="none"))
