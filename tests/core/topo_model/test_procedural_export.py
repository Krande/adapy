"""Downloadable exports of a procedural model: the DETAIL model to IFC (the clash
cuts ride along as IfcRelVoidsElement voids, equipment as IfcPump/IfcTank) and the
SIMULATION model to a Genie concept XML. Exercises the same compile + serialize
path the worker's ``procedural_export_model`` job runs, on the built-in steel demo.
"""

from __future__ import annotations

import os
import tempfile

from ada.topo_model.compile import build_procedural_assembly
from ada.topo_model.templates import _eq, _localize, _steel_structure_demo_doc


def test_detail_model_exports_to_ifc_with_clash_voids():
    asm = build_procedural_assembly(
        _steel_structure_demo_doc(), name="SteelDemo", lod="detail", detailing="adapy-default"
    )
    # No parent-less object may reach the writer (rotated equipment bodies used to).
    assert not [o for o in asm.get_all_physical_objects() if getattr(o, "parent", None) is None]

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.ifc")
        asm.to_ifc(p, file_obj_only=False)
        txt = open(p, encoding="utf-8", errors="ignore").read().upper()

    # The clash cuts (and other booleans) are real IFC voids, not tessellation-only.
    assert txt.count("IFCRELVOIDSELEMENT") > 100
    assert "IFCBEAM(" in txt and "IFCPLATE(" in txt
    # Equipment archetypes export as their proper IFC element types.
    assert "IFCPUMP(" in txt and "IFCTANK(" in txt


def test_sim_model_exports_to_genie_xml_with_equipment_concepts():
    from ada.api.spatial.eq_types import EquipRepr
    from ada.api.spatial.equipment import Equipment

    asm = build_procedural_assembly(
        _steel_structure_demo_doc(), name="SteelDemo", lod="sim", detailing=None, equipment_resolver=(lambda _k: None)
    )
    # The worker promotes AS_IS equipment to FOOTPRINT_MASS so Genie writes it as a
    # concept (prism_shape) instead of dropping it — mirror that here.
    eqs = [p for p in asm.get_all_parts_in_assembly(include_self=True) if isinstance(p, Equipment)]
    assert eqs, "demo should place equipment"
    for eq in eqs:
        if eq.eq_repr == EquipRepr.AS_IS:
            eq.eq_repr = EquipRepr.FOOTPRINT_MASS

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.gxml")
        asm.to_genie_xml(p, embed_sat=False)
        txt = open(p, encoding="utf-8", errors="ignore").read()

    # A Genie concept model with the frame's beams + plates as <structure> entries,
    # and every equipment as its Genie equipment concept type.
    assert "<straight_beam" in txt
    assert "<flat_plate" in txt
    assert txt.count("<prism_shape") == len(eqs)


def _catalog_cad_doc():
    """A doc placing one catalog equipment (slug 'mypump') in a cell."""
    spaces = [{"NAME": "C1", "INCLUDE": True, "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}]
    equipments = _localize(spaces, [_eq("E1", "mypump", 2, 2, 0, 1, 1, 1, "C1")])
    return {
        "grid": {},
        "blueprint": {},
        "design_rules": "standard",
        "equipment_cad": True,
        "spaces": spaces,
        "equipments": equipments,
        "openings": [],
        "systems": [],
    }


def test_catalog_cad_equipment_is_faithful_in_ifc():
    # A catalog equipment with a linked CAD asset must export its REAL geometry (an
    # IfcTriangulatedFaceSet), not a placeholder box — via cad_as_objects, which
    # materialises the resolved CAD mesh as an assembly Shape on the equipment.
    import trimesh

    mesh = trimesh.creation.icosphere(subdivisions=3, radius=0.5)  # 1280 tris; not a box
    assert len(mesh.faces) > 100
    catalog_doc = {"bbox": {"lx": 1.0, "ly": 1.0, "lz": 1.0}, "mass": 1500.0, "ifc_element_class": "IfcPump"}

    asm = build_procedural_assembly(
        _catalog_cad_doc(),
        name="D",
        lod="detail",
        detailing="adapy-default",
        equipment_resolver=(lambda s: catalog_doc if s == "mypump" else None),
        cad_scene_resolver=(lambda s: mesh if s == "mypump" else None),
        cad_as_objects=True,
    )
    from ada.api.spatial.equipment import Equipment

    eq = next(p for p in asm.get_all_parts_in_assembly(include_self=True) if isinstance(p, Equipment))
    # The equipment carries the CAD geometry as a real body Shape (not a box).
    assert [s.name for s in eq.shapes] == ["E1_cad"]

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.ifc")
        asm.to_ifc(p, file_obj_only=False)
        txt = open(p, encoding="utf-8", errors="ignore").read().upper()
    assert txt.count("IFCPUMP(") == 1
    assert txt.count("IFCTRIANGULATEDFACESET") == 1  # the spliced CAD geometry


def test_catalog_equipment_without_cad_is_a_box():
    # CAD off (the "CAD equip" toggle off): the equipment is a placeholder box with
    # the catalog IFC class — no triangulated face set.
    catalog_doc = {"bbox": {"lx": 1.0, "ly": 1.0, "lz": 1.0}, "mass": 1500.0, "ifc_element_class": "IfcPump"}
    asm = build_procedural_assembly(
        {**_catalog_cad_doc(), "equipment_cad": False},
        name="D",
        lod="detail",
        detailing="adapy-default",
        equipment_resolver=(lambda s: catalog_doc if s == "mypump" else None),
    )
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.ifc")
        asm.to_ifc(p, file_obj_only=False)
        txt = open(p, encoding="utf-8", errors="ignore").read().upper()
    assert txt.count("IFCPUMP(") == 1
    assert txt.count("IFCTRIANGULATEDFACESET") == 0


def test_export_key_format_and_cad_variant():
    from ada.comms.rest.procedural import procedural_model_export_key

    assert procedural_model_export_key("abc", 3, "ifc") == "_procedural/abc/r3.ifc"
    assert procedural_model_export_key("abc", 3, "gxml") == "_procedural/abc/r3.gxml"
    # CAD-on IFC (default) and CAD-off (placeholder boxes) never collide in cache.
    assert procedural_model_export_key("abc", 3, "ifc", cad_equipment=True) == "_procedural/abc/r3.ifc"
    assert procedural_model_export_key("abc", 3, "ifc", cad_equipment=False) == "_procedural/abc/r3_box.ifc"
    # The variant suffix is IFC-only.
    assert procedural_model_export_key("abc", 3, "gxml", cad_equipment=False) == "_procedural/abc/r3.gxml"
