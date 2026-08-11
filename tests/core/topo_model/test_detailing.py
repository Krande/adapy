"""Phase 1 of the detailing engine: the built-in ``adapy-default`` detailing
engine adds connection joints to the compiled SteelStru demo.

Detection is the OCC-free numpy clash path; the emitted ``Plate``/``Weld``
geometry tessellates through the libtess2/NGEOM stream. Counts are PINNED from
the verified first run on the 2x1 demo (mirroring ``test_steel_stru``): the demo
is fixed geometry, so a drift in these numbers means a detection/geometry change.
"""

from __future__ import annotations

import pytest

import ada
from ada.topo_model import build_topo_model
from ada.topo_model.detailing import (
    collect_base_plate_joints,
    collect_box_joints,
    collect_endplate_joints,
    collect_girder_joints,
    detail,
)

_BOX_GIRDER = "BG300x300x8x8"


@pytest.fixture()
def demo() -> ada.Assembly:
    return build_topo_model()


# ── detection counts (pinned) ────────────────────────────────────────


def test_starter_joint_counts_pinned(demo):
    assert len(collect_girder_joints(demo)) == 48
    assert len(collect_endplate_joints(demo, {})) == 36
    assert len(collect_base_plate_joints(demo, {})) == 6


# ── detail() end-to-end on the I-section demo ────────────────────────


def test_detail_adds_joints_part_with_pinned_geometry(demo):
    out = detail(demo, {})
    assert out is demo  # mutates + returns the same assembly

    joints = demo.get_by_name("Joints")
    assert joints is not None, "detailing produced no Joints part"

    # One Connection part per detected joint (48 girder + 36 endplate + 6 base).
    connections = list(joints.parts.values())
    assert len(connections) == 90

    plates = list(joints.get_all_physical_objects(by_type=ada.Plate))
    welds = list(joints.get_all_welds())
    # Each joint contributes exactly one plate; welds = 96 girder (2 each) + 36
    # endplate (1 each) + 24 base (4 each).
    assert len(plates) == 90
    assert len(welds) == 156


def test_each_connection_carries_expected_plate_and_weld_names(demo):
    detail(demo, {})
    joints = demo.get_by_name("Joints")

    plate_names = {p.name for p in joints.get_all_physical_objects(by_type=ada.Plate)}
    weld_names = {w.name for w in joints.get_all_welds()}

    # The three joint families each name their plate distinctively.
    assert any(n.endswith("_gusset") for n in plate_names)
    assert any(n.endswith("_endplate") for n in plate_names)
    assert any(n.endswith("_baseplate") for n in plate_names)
    # And each family emits welds.
    assert any("EP_" in n and n.endswith("_weld") for n in weld_names)
    assert any("BP_" in n and "_weld_" in n for n in weld_names)


def test_bolt_group_is_metadata_first(demo):
    # Bolts are modelled metadata-first (no first-class fastener primitive in
    # Phase 1): an end-plate Connection carries a ConnectionInfo-style record.
    detail(demo, {})
    joints = demo.get_by_name("Joints")
    ep = next(p for p in joints.parts.values() if p.name.startswith("EP_"))
    info = ep.metadata.get("connection_info")
    assert info and info["spec_name"] == "adapy.beam_column_endplate"
    assert info["member_roles"].get("landing")
    assert info["plate_names"] and info["weld_names"]


# ── per-joint-type toggles ───────────────────────────────────────────


def test_toggles_select_joint_families(demo):
    detail(demo, {"girder_gusset": False, "beam_column_endplate": False, "column_base_plate": True})
    joints = demo.get_by_name("Joints")
    # Only base plates -> 6 connections, all base plates.
    assert len(list(joints.parts.values())) == 6
    assert all(p.name.startswith("BP_") for p in joints.parts.values())


def test_none_selected_adds_no_joints():
    # Backward-compat: with no joint families enabled the assembly is untouched
    # (no Joints part) — the compile output stays byte-identical to today.
    a = build_topo_model()
    detail(
        a,
        {
            "girder_gusset": False,
            "beam_column_endplate": False,
            "column_base_plate": False,
            "box_to_box": False,
        },
    )
    assert a.get_by_name("Joints") is None


# ── swappable joint definitions: box girders + the box clash-cut ─────


def test_box_girder_demo_builds_with_box_sections():
    a = build_topo_model(girder_sec=_BOX_GIRDER)
    box_beams = [b for b in a.get_all_physical_objects(by_type=ada.Beam) if b.section.name == _BOX_GIRDER]
    # Same 14 girders as the I-section demo, now box sections.
    assert len(box_beams) == 14


def test_box_joint_bool_cuts_incoming_beams():
    # The simplified box joint adds NO plate/weld — it only bool-cuts the incoming
    # box beam with the landing member so they no longer interpenetrate. Enable
    # ONLY the box joint (the girder/end/base families would also fire on the
    # box-Girder members, so isolate it).
    a = build_topo_model(girder_sec=_BOX_GIRDER)
    box_beams = [b for b in a.get_all_physical_objects(by_type=ada.Beam) if b.section.name == _BOX_GIRDER]
    assert all(len(b.booleans) == 0 for b in box_beams)  # clean before

    assert len(collect_box_joints(a, {})) == 24

    detail(
        a,
        {
            "girder_gusset": False,
            "beam_column_endplate": False,
            "column_base_plate": False,
            "box_to_box": True,
        },
    )
    # No connective geometry: box joints never create a Joints part.
    assert a.get_by_name("Joints") is None
    # Every box girder gained the clash-cut boolean(s); 24 cuts total (one per
    # detected box-to-box junction), removing the interpenetration.
    with_cut = [b for b in box_beams if len(b.booleans) > 0]
    assert len(with_cut) == 14
    assert sum(len(b.booleans) for b in box_beams) == 24
