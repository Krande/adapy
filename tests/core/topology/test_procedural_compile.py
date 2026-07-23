"""compile_procedural_doc: procedural cell-model doc -> GLB bytes."""

from __future__ import annotations

import pytest

from ada.topo_model.compile import compile_procedural_doc

DOC = {
    "spaces": [
        {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ],
    "equipments": [
        {
            "NAME": "P1",
            "SPACE_NAME": "Cell1",
            "SPACE_LOC": "ROOF",
            "DESCRIPTION": "pump",
            "X": 2.0,
            "Y": 2.0,
            "Z": 3.0,
            "LX": 1.0,
            "LY": 1.0,
            "LZ": 1.0,
            "COGx": 0,
            "COGy": 0,
            "COGz": 0.5,
            "massDry": 1000,
            "massCont": 0,
        }
    ],
}


def _is_glb(data: bytes) -> bool:
    return data[:4] == b"glTF"


def test_compile_raw_boxes():
    glb = compile_procedural_doc(DOC, blueprint_name="none")
    assert _is_glb(glb) and len(glb) > 500


def test_compile_steel_stru():
    glb = compile_procedural_doc(DOC, blueprint_name="steel_stru")
    assert _is_glb(glb)
    # the structural compile carries plates+beams and is substantially larger
    assert len(glb) > len(compile_procedural_doc(DOC, blueprint_name="none"))


def test_compile_blueprint_options_reinforced_wall():
    doc = dict(DOC)
    doc["blueprint"] = {"reinforce_internal_walls": True, "not_whitelisted": "ignored"}
    glb = compile_procedural_doc(doc, blueprint_name="steel_stru")
    assert _is_glb(glb)
    # the reinforced wall adds a plate + stiffeners -> bigger than the plain compile
    assert len(glb) > len(compile_procedural_doc(DOC, blueprint_name="steel_stru"))


def _eq(name, desc, x, y, z, lx, ly, lz):
    return {
        "NAME": name,
        "DESCRIPTION": desc,
        "SPACE_NAME": "Cell1",
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
        "massDry": 1000,
        "massCont": 0,
    }


def test_compile_renders_routed_systems():
    import ada
    from ada.topo_model.blueprint import SteelStru
    from ada.topo_model.compile import (
        _build_systems,
        _equipment_to_object,
        _space_to_box,
    )
    from ada.topology import TopologyBuilder
    from ada.topology.entities import TopoEquipment, TopoSpace

    doc = {
        "blueprint": {"reinforce_internal_walls": True},
        "spaces": DOC["spaces"],
        "equipments": [_eq("Pump2", "pump", 2, 2, 0, 1, 1, 1), _eq("Tank2", "tank", 6.5, 1.5, 0, 2, 2, 2)],
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
    spaces = [TopoSpace(**s) for s in doc["spaces"]]
    builder = TopologyBuilder.from_prim_boxes(
        [_space_to_box(s) for s in spaces], blueprint=SteelStru(reinforce_internal_walls=True)
    )
    builder.build()
    a = builder.get_output_assembly("M")
    objs = [_equipment_to_object(TopoEquipment(**e)) for e in doc["equipments"]]
    emap = {o.name: o for o in objs if isinstance(o, ada.Equipment)}
    a.add_part(ada.Part("Equipment") / objs)
    for part in _build_systems(doc, emap, spaces, builder.cell_graph):
        a.add_part(part)

    pipes = [p.name for p in a.get_all_physical_objects(by_type=ada.Pipe)]
    sleeves = [s.name for s in a.get_all_physical_objects() if s.name.endswith("_sleeve")]
    assert pipes == ["ServiceWater_route"]
    assert sleeves == ["ServiceWater_pen_00_sleeve"]


def test_compile_bad_system_skipped_not_fatal():
    doc = {
        "spaces": DOC["spaces"],
        "equipments": [_eq("Pump2", "pump", 2, 2, 0, 1, 1, 1)],
        "systems": [{"NAME": "Broken", "TYPE": "piping", "CONNECTIONS": [{"EQUIPMENT": "Nope", "PORT": "x"}]}],
    }
    # unknown equipment -> system skipped, compile still succeeds
    glb = compile_procedural_doc(doc, blueprint_name="steel_stru")
    assert _is_glb(glb)


def test_compile_empty_doc_raises():
    with pytest.raises(ValueError, match="no spaces"):
        compile_procedural_doc({"spaces": []})


def test_compile_missing_coords_raises():
    with pytest.raises(ValueError, match="missing coordinates"):
        compile_procedural_doc({"spaces": [{"NAME": "Cell1"}]}, blueprint_name="none")
