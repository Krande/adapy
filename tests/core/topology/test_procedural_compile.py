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


def test_compile_pipe_leaves_ports_along_nozzle_normal():
    """The compiled run's first/last segments must follow the connected ports'
    direction vectors (issue: pipes ignored the equipment I/O vector). Pump2
    discharge faces +Z; Tank2 inlet faces +Z — so the pipe must rise straight
    out of the discharge and drop straight into the inlet."""
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
        "spaces": DOC["spaces"],
        "equipments": [_eq("Pump2", "pump", 2, 2, 0, 1, 1, 1), _eq("Tank2", "tank", 6.5, 1.5, 0, 2, 2, 2)],
        "systems": [
            {
                "NAME": "CW",
                "TYPE": "piping",
                "CONNECTIONS": [{"EQUIPMENT": "Pump2", "PORT": "discharge"}, {"EQUIPMENT": "Tank2", "PORT": "inlet"}],
            }
        ],
    }
    spaces = [TopoSpace(**s) for s in doc["spaces"]]
    builder = TopologyBuilder.from_prim_boxes([_space_to_box(s) for s in spaces], blueprint=SteelStru())
    builder.build()
    a = builder.get_output_assembly("M")
    objs = [_equipment_to_object(TopoEquipment(**e)) for e in doc["equipments"]]
    emap = {o.name: o for o in objs if isinstance(o, ada.Equipment)}
    a.add_part(ada.Part("Equipment") / objs)
    for part in _build_systems(doc, emap, spaces, builder.cell_graph):
        a.add_part(part)

    (pipe,) = a.get_all_physical_objects(by_type=ada.Pipe)
    pts = [tuple(round(float(v), 6) for v in p) for p in pipe.points]

    discharge = emap["Pump2"].get_port("discharge")
    inlet = emap["Tank2"].get_port("inlet")
    assert pts[0] == tuple(round(float(v), 6) for v in discharge.get_global_position())
    assert pts[-1] == tuple(round(float(v), 6) for v in inlet.get_global_position())

    def _unit_step(a_pt, b_pt):
        d = [b_pt[i] - a_pt[i] for i in range(3)]
        n = sum(c * c for c in d) ** 0.5
        assert n > 1e-9
        return [c / n for c in d]

    # first segment out of the discharge follows +Z; last segment into the inlet
    # arrives along +Z (i.e. steps -Z out of the port going backwards).
    assert _unit_step(pts[0], pts[1]) == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)
    assert _unit_step(pts[-1], pts[-2]) == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)


def _crossing_doc(design_rules=None):
    doc = {
        "blueprint": {"reinforce_internal_walls": True},
        "spaces": DOC["spaces"],
        "equipments": [_eq("Pump2", "pump", 2, 2, 0, 1, 1, 1), _eq("Tank2", "tank", 6.5, 1.5, 0, 2, 2, 2)],
        "systems": [
            {
                "NAME": "ServiceWater",
                "TYPE": "piping",
                "CONNECTIONS": [{"EQUIPMENT": "Pump2", "PORT": "discharge"}, {"EQUIPMENT": "Tank2", "PORT": "inlet"}],
            }
        ],
    }
    if design_rules is not None:
        doc["design_rules"] = design_rules
    return doc


def test_compile_design_rules_slug_selects_ruleset():
    """doc['design_rules'] resolves via the registry: 'route_only' drops the
    penetration detail geometry, so its GLB is smaller than 'standard'."""
    standard = compile_procedural_doc(_crossing_doc("standard"))
    route_only = compile_procedural_doc(_crossing_doc("route_only"))
    assert _is_glb(standard) and _is_glb(route_only)
    assert len(route_only) < len(standard)
    # unknown / absent slug falls back to standard (same output as explicit standard)
    assert len(compile_procedural_doc(_crossing_doc("nope"))) == len(standard)
    assert len(compile_procedural_doc(_crossing_doc())) == len(standard)


def test_compile_bad_system_skipped_not_fatal():
    doc = {
        "spaces": DOC["spaces"],
        "equipments": [_eq("Pump2", "pump", 2, 2, 0, 1, 1, 1)],
        "systems": [{"NAME": "Broken", "TYPE": "piping", "CONNECTIONS": [{"EQUIPMENT": "Nope", "PORT": "x"}]}],
    }
    # unknown equipment -> system skipped, compile still succeeds
    glb = compile_procedural_doc(doc, blueprint_name="steel_stru")
    assert _is_glb(glb)


def test_compile_catalog_equipment_resolver():
    """An equipment whose DESCRIPTION is a per-scope catalog slug compiles into
    a full Equipment (ports + IFC class) via the resolver, taking precedence
    over the built-in archetypes."""
    import ada
    from ada.topo_model.compile import _equipment_to_object
    from ada.topology.entities import TopoEquipment

    catalog = {
        "big-pump": {
            "bbox": {"lx": 1, "ly": 1, "lz": 2},
            "mass": 750,
            "ifc_element_class": "IfcPump",
            "ports": [
                {"name": "suction", "position": [-0.5, 0, 1], "direction_vector": [-1, 0, 0], "direction": "IN"},
                {"name": "discharge", "position": [0, 0, 2], "direction_vector": [0, 0, 1], "direction": "OUT"},
            ],
        }
    }
    eq_dict = _eq("BP1", "big-pump", 2, 2, 0, 1, 1, 2)
    obj = _equipment_to_object(TopoEquipment(**eq_dict), catalog.get)
    assert isinstance(obj, ada.Equipment)
    assert obj.ifc_element_class == "IfcPump"
    assert sorted(p.name for p in obj.ports) == ["discharge", "suction"]

    glb = compile_procedural_doc(
        {"spaces": DOC["spaces"], "equipments": [eq_dict]},
        blueprint_name="none",
        equipment_resolver=catalog.get,
    )
    assert _is_glb(glb)


def test_build_catalog_equipment_without_body():
    from ada.topo_model.equipment import build_equipment_from_catalog

    eq = build_equipment_from_catalog("x", (0, 0, 0), {"ports": []}, lx=1, ly=1, lz=1, add_body=False)
    assert list(eq.get_all_physical_objects()) == []  # no placeholder box body
    eq2 = build_equipment_from_catalog("y", (0, 0, 0), {"ports": []}, lx=1, ly=1, lz=1, add_body=True)
    assert len(list(eq2.get_all_physical_objects())) == 1  # box body present


def test_compile_equipment_cad_splice():
    """equipment_cad on + a resolvable CAD mesh splices the real geometry into
    the GLB (via the trimesh-merge export path)."""
    import trimesh

    mesh = trimesh.creation.box(extents=(1.0, 1.0, 2.0))
    catalog = {
        "big-pump": {"bbox": {"lx": 1, "ly": 1, "lz": 2}, "mass": 100, "ifc_element_class": "IfcPump", "ports": []}
    }
    doc = {
        "equipment_cad": True,
        "spaces": DOC["spaces"],
        "equipments": [_eq("BP1", "big-pump", 2, 2, 0, 1, 1, 2)],
    }
    glb = compile_procedural_doc(
        doc,
        blueprint_name="none",
        equipment_resolver=catalog.get,
        cad_scene_resolver={"big-pump": mesh}.get,
    )
    assert _is_glb(glb) and len(glb) > 500
    # a missing CAD mesh falls back to the box path without error
    glb2 = compile_procedural_doc(doc, blueprint_name="none", equipment_resolver=catalog.get, cad_scene_resolver={}.get)
    assert _is_glb(glb2)


def test_compile_empty_doc_raises():
    with pytest.raises(ValueError, match="no spaces"):
        compile_procedural_doc({"spaces": []})


def test_compile_missing_coords_raises():
    with pytest.raises(ValueError, match="missing coordinates"):
        compile_procedural_doc({"spaces": [{"NAME": "Cell1"}]}, blueprint_name="none")
