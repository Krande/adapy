"""ProceduralBuilder: the root object that owns a procedural cell-model compile.

Exercises the documented usage patterns — object-first construction, the
from_dict/from_json/from_excel/to_excel round-trips, phase-by-phase driving, the
``.procedural`` root back-reference reachable from the blueprint and any
GraphFace, LOD on the root, and equivalence with the ``compile_procedural_doc``
wrapper.
"""

from __future__ import annotations

import json

import pytest

from ada.topo_model import ProceduralBuilder
from ada.topo_model.compile import compile_procedural_doc
from ada.topology.entities import TopoEquipment, TopoSpace, TopoSystem

DOC = {
    "spaces": [
        {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ],
    "equipments": [
        {
            "NAME": "Pump2", "DESCRIPTION": "pump", "SPACE_NAME": "Cell1", "SPACE_LOC": "FLOOR",
            "X": 2.0, "Y": 2.0, "Z": 0.0, "LX": 1.0, "LY": 1.0, "LZ": 1.0,
            "COGx": 0, "COGy": 0, "COGz": 0.5, "massDry": 1000, "massCont": 0,
        },
        {
            "NAME": "Tank2", "DESCRIPTION": "tank", "SPACE_NAME": "Cell2", "SPACE_LOC": "FLOOR",
            "X": 6.5, "Y": 1.5, "Z": 0.0, "LX": 2.0, "LY": 2.0, "LZ": 2.0,
            "COGx": 0, "COGy": 0, "COGz": 1.0, "massDry": 1000, "massCont": 0,
        },
    ],
    "systems": [
        {
            "NAME": "ServiceWater",
            "TYPE": "piping",
            "CONNECTIONS": [
                {"EQUIPMENT": "Pump2", "PORT": "discharge"},
                {"EQUIPMENT": "Tank2", "PORT": "inlet"},
            ],
        }
    ],
}


def _is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


# --- construction: object-first + from_dict -------------------------------- #
def test_object_first_construction_compiles():
    """The primary API is explicit entity objects, not a dict."""
    pb = ProceduralBuilder(
        spaces=[TopoSpace(**s) for s in DOC["spaces"]],
        equipments=[TopoEquipment(**e) for e in DOC["equipments"]],
        systems=[TopoSystem(**s) for s in DOC["systems"]],
    )
    glb = pb.compile()
    assert _is_glb(glb) and len(glb) > 500


def test_from_dict_compiles():
    glb = ProceduralBuilder.from_dict(DOC).compile()
    assert _is_glb(glb) and len(glb) > 500


def test_wrapper_matches_builder():
    """``compile_procedural_doc`` is a thin wrapper over from_dict + compile — for
    the same document both must yield the same model. (Object GUIDs are freshly
    generated per build, so the GLB is compared by size, not byte-for-byte.)"""
    from_wrapper = compile_procedural_doc(DOC)
    from_builder = ProceduralBuilder.from_dict(DOC).compile()
    assert _is_glb(from_wrapper) and _is_glb(from_builder)
    assert len(from_wrapper) == len(from_builder)


def test_no_spaces_raises():
    with pytest.raises(ValueError, match="no spaces"):
        ProceduralBuilder(spaces=[])
    with pytest.raises(ValueError, match="no spaces"):
        ProceduralBuilder.from_dict({"spaces": []})


# --- phase-by-phase driving + owned state ---------------------------------- #
def test_phases_populate_owned_state():
    pb = ProceduralBuilder.from_dict(DOC)
    assert pb.blueprint is None and pb.assembly is None and pb.cell_graph is None

    pb.build_structure()
    assert pb.blueprint is not None and pb.assembly is not None and pb.cell_graph is not None

    pb.build_equipment()
    assert set(pb.equipment_map) == {"Pump2", "Tank2"}

    pb.build_systems()
    assert any(p.name == "Systems" for p in pb.systems_parts)

    assert _is_glb(pb.to_glb())


# --- the .procedural root back-reference ----------------------------------- #
def test_blueprint_reaches_root():
    pb = ProceduralBuilder.from_dict(DOC)
    pb.build_structure()
    assert pb.blueprint.procedural is pb


def test_graphface_reaches_root():
    """Any GraphFace reaches the root through
    ``face.parent_cell.cell_graph.procedural`` — chaining up cell -> graph -> root."""
    pb = ProceduralBuilder.from_dict(DOC)
    pb.build_structure()
    faces = pb.cell_graph.get_external_floors()
    assert faces
    for face in faces:
        assert face.parent_cell.cell_graph.procedural is pb


# --- LOD lives on the root ------------------------------------------------- #
def test_detail_flag_lives_on_root():
    sim = ProceduralBuilder.from_dict(DOC, lod="sim")
    detail = ProceduralBuilder.from_dict(DOC, lod="detail")
    assert sim.detail is False and detail.detail is True

    sim.build_structure()
    detail.build_structure()
    assert sim.blueprint.detail is False
    assert detail.blueprint.detail is True


def test_detail_compile_differs_from_sim():
    sim = ProceduralBuilder.from_dict(DOC, lod="sim").compile()
    detail = ProceduralBuilder.from_dict(DOC, lod="detail").compile()
    assert _is_glb(sim) and _is_glb(detail)
    assert sim != detail


# --- blueprint_name="none" ------------------------------------------------- #
def test_none_blueprint_skips_topology():
    pb = ProceduralBuilder.from_dict(DOC, blueprint_name="none")
    pb.build_structure()
    assert pb.cell_graph is None and pb.assembly is not None
    assert _is_glb(ProceduralBuilder.from_dict(DOC, blueprint_name="none").compile())


# --- serialization round-trips --------------------------------------------- #
def test_to_doc_roundtrips():
    """to_doc() -> from_dict() preserves the entities and the whitelisted options."""
    doc = dict(DOC, blueprint={"reinforce_internal_walls": True}, design_rules="route_only")
    pb = ProceduralBuilder.from_dict(doc)
    back = ProceduralBuilder.from_dict(pb.to_doc())
    assert [s.NAME for s in back.spaces] == ["Cell1", "Cell2"]
    assert [e.NAME for e in back.equipments] == ["Pump2", "Tank2"]
    assert [s.NAME for s in back.systems] == ["ServiceWater"]
    assert back.blueprint_options == {"reinforce_internal_walls": True}
    assert back.design_rules_slug == "route_only"


def test_engine_binding_roundtrips_through_doc_and_excel(tmp_path):
    """The engine/schema_version routing header is stamped into to_doc(), read
    back by from_dict, and survives the Model-sheet excel round-trip."""
    from ada.topo_model.engines import DEFAULT_ENGINE_SLUG, PROCEDURAL_SCHEMA_VERSION

    # Default builder stamps the built-in engine + current schema version.
    default_doc = ProceduralBuilder.from_dict(DOC).to_doc()
    assert default_doc["engine"] == DEFAULT_ENGINE_SLUG
    assert default_doc["schema_version"] == PROCEDURAL_SCHEMA_VERSION

    # An explicit engine round-trips: object -> doc -> object, and via excel.
    pb = ProceduralBuilder(spaces=list(ProceduralBuilder.from_dict(DOC).spaces), engine="param_models")
    assert pb.to_doc()["engine"] == "param_models"
    assert ProceduralBuilder.from_dict(pb.to_doc()).engine == "param_models"
    xlsx = tmp_path / "engine.xlsx"
    pb.to_excel(xlsx)
    assert ProceduralBuilder.from_excel(xlsx).engine == "param_models"


def test_from_json_string_and_file(tmp_path):
    pb_str = ProceduralBuilder.from_json(json.dumps(DOC))
    assert [s.NAME for s in pb_str.spaces] == ["Cell1", "Cell2"]

    p = tmp_path / "model.json"
    p.write_text(json.dumps(DOC))
    pb_file = ProceduralBuilder.from_json(p)
    assert [e.NAME for e in pb_file.equipments] == ["Pump2", "Tank2"]


def test_excel_roundtrip_compiles(tmp_path):
    """to_excel -> from_excel round-trips the WHOLE model (spaces + equipment +
    systems + doc-level scalars) and the reloaded model still compiles."""
    doc = dict(DOC, blueprint={"reinforce_internal_walls": True, "enclosed_cells": ["Cell1"]}, design_rules="route_only")
    pb = ProceduralBuilder.from_dict(doc)

    xlsx = tmp_path / "model.xlsx"
    pb.to_excel(xlsx)
    back = ProceduralBuilder.from_excel(xlsx)

    assert [s.NAME for s in back.spaces] == ["Cell1", "Cell2"]
    assert [e.NAME for e in back.equipments] == ["Pump2", "Tank2"]
    assert [s.NAME for s in back.systems] == ["ServiceWater"]
    assert back.systems[0].CONNECTIONS[0].EQUIPMENT == "Pump2"
    assert back.blueprint_options == {"reinforce_internal_walls": True, "enclosed_cells": ["Cell1"]}
    assert back.design_rules_slug == "route_only"
    assert _is_glb(back.compile())
