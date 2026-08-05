"""Phase 3 I-girder joint modelling in the procedural detail model.

``lod="detail"`` upgrades each I-girder to I-girder intersection into a modelled
joint carrying visible connective geometry (gusset plate + weld beads) under a
``Part("Joints")``; ``lod="sim"`` adds no such geometry. The whole path is
OCC-free (numpy clash detection + ``Weld`` -> ``PrimExtrude``)."""

from __future__ import annotations

import ada
from ada.topo_model.compile import _apply_girder_joints, compile_procedural_doc

# Two IPE200 girders meeting at a shared corner node (the node-based clash check
# keys on shared endpoints, mirroring how the blueprint seats deck-edge girders).
_G1 = ("G1", (0, 0, 0), (4, 0, 0), "IPE200")
_G2 = ("G2", (4, 0, 0), (4, 4, 0), "IPE200")

_DOC = {
    "spaces": [
        {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ],
}


def _corner_girder_assembly() -> ada.Assembly:
    return ada.Assembly("T") / [ada.Beam(*_G1), ada.Beam(*_G2)]


def test_girder_joint_pass_adds_joints_part_with_plate_and_welds():
    a = _corner_girder_assembly()
    _apply_girder_joints(a)

    joints_part = a.get_by_name("Joints")
    assert joints_part is not None, "detail joint pass produced no Joints part"

    plates = list(joints_part.get_all_physical_objects(by_type=ada.Plate))
    welds = list(joints_part.get_all_welds())
    assert len(plates) == 1, f"expected one gusset plate, got {len(plates)}"
    assert len(welds) == 2, f"expected two weld beads, got {len(welds)}"


def test_sim_assembly_has_no_joints_part():
    # The sim path never runs the joint pass, so no Joints part is created.
    a = _corner_girder_assembly()
    assert a.get_by_name("Joints") is None


def test_detail_glb_larger_than_sim():
    sim = compile_procedural_doc(_DOC, lod="sim")
    detail = compile_procedural_doc(_DOC, lod="detail")
    assert sim[:4] == b"glTF" and detail[:4] == b"glTF"
    # The modelled joints (gusset plate + weld beads) add geometry.
    assert len(detail) > len(sim)
