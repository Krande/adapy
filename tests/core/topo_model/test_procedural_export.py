"""Downloadable exports of a procedural model: the DETAIL model to IFC (the clash
cuts ride along as IfcRelVoidsElement voids, equipment as IfcPump/IfcTank) and the
SIMULATION model to a Genie concept XML. Exercises the same compile + serialize
path the worker's ``procedural_export_model`` job runs, on the built-in steel demo.
"""

from __future__ import annotations

import os
import tempfile

from ada.topo_model.compile import compile_procedural_doc_with_assembly
from ada.topo_model.templates import _steel_structure_demo_doc


def test_detail_model_exports_to_ifc_with_clash_voids():
    _glb, _stats, asm = compile_procedural_doc_with_assembly(
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

    _glb, _stats, asm = compile_procedural_doc_with_assembly(
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


def test_export_key_format_and_cad_variant():
    from ada.comms.rest.procedural import procedural_model_export_key

    assert procedural_model_export_key("abc", 3, "ifc") == "_procedural/abc/r3.ifc"
    assert procedural_model_export_key("abc", 3, "gxml") == "_procedural/abc/r3.gxml"
    # CAD-on IFC (default) and CAD-off (placeholder boxes) never collide in cache.
    assert procedural_model_export_key("abc", 3, "ifc", cad_equipment=True) == "_procedural/abc/r3.ifc"
    assert procedural_model_export_key("abc", 3, "ifc", cad_equipment=False) == "_procedural/abc/r3_box.ifc"
    # The variant suffix is IFC-only.
    assert procedural_model_export_key("abc", 3, "gxml", cad_equipment=False) == "_procedural/abc/r3.gxml"
