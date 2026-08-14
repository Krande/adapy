"""Phase 3 I-girder joint modelling in the procedural detail model.

``lod="detail"`` upgrades each I-girder to I-girder intersection into a modelled
joint carrying visible connective geometry (gusset plate + weld beads) under a
``Part("Joints")``; ``lod="sim"`` adds no such geometry. The whole path is
OCC-free (numpy clash detection + ``Weld`` -> ``PrimExtrude``)."""

from __future__ import annotations

import ada
from ada.topo_model.compile import _apply_girder_joints

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


def test_mid_span_cross_junction_is_jointed():
    # A pure "+" crossing (two girders through each other's mid-span, no shared node)
    # is missed by connections.find's node-based prefilter — the interior-crossing
    # pass must still joint it. A node/T junction stays a single joint (no double).
    from ada.topo_model.detail_joints import collect_girder_joints

    cross = ada.Assembly("X") / [
        ada.Beam("g1", (0, 2.5, 0), (5, 2.5, 0), "IPE200"),
        ada.Beam("g2", (2.5, 0, 0), (2.5, 5, 0), "IPE200"),
    ]
    joints = collect_girder_joints(cross)
    assert len(joints) == 1
    assert tuple(round(float(v), 2) for v in joints[0].centre) == (2.5, 2.5, 0.0)

    # An L-corner combined with a crossing yields exactly two distinct joints.
    mixed = ada.Assembly("M") / [
        ada.Beam("g1", (0, 0, 0), (5, 0, 0), "IPE200"),
        ada.Beam("g2", (5, 0, 0), (5, 5, 0), "IPE200"),
        ada.Beam("g3", (2.5, -2, 0), (2.5, 2, 0), "IPE200"),
    ]
    centres = sorted(tuple(round(float(v), 2) for v in j.centre) for j in collect_girder_joints(mixed))
    assert centres == [(2.5, 0.0, 0.0), (5.0, 0.0, 0.0)]
