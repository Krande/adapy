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
    # HP bulb-flat stringers are excluded from connection detailing (they are
    # direction-classified as "Girder" but are secondary members) — so the demo's
    # only girder–girder crossings (all with a column present) form no 2-member
    # gusset, and every stringer end is left plain.
    assert len(collect_girder_joints(demo)) == 0
    assert len(collect_endplate_joints(demo, {})) == 36
    assert len(collect_base_plate_joints(demo, {})) == 6


# ── detail() end-to-end on the I-section demo ────────────────────────


def test_detail_adds_joints_part_with_pinned_geometry(demo):
    out = detail(demo, {})
    assert out is demo  # mutates + returns the same assembly

    joints = demo.get_by_name("Joints")
    assert joints is not None, "detailing produced no Joints part"

    # One Connection part per detected joint (0 girder gusset + 36 endplate + 6
    # base — HP stringers excluded, so no stringer gussets).
    connections = list(joints.parts.values())
    assert len(connections) == 42

    plates = list(joints.get_all_physical_objects(by_type=ada.Plate))
    welds = list(joints.get_all_welds())
    # Each joint contributes exactly one plate; welds = 36 endplate (1 each) + 24
    # base (4 each).
    assert len(plates) == 42
    assert len(welds) == 60


def test_each_connection_carries_expected_plate_and_weld_names(demo):
    detail(demo, {})
    joints = demo.get_by_name("Joints")

    plate_names = {p.name for p in joints.get_all_physical_objects(by_type=ada.Plate)}
    weld_names = {w.name for w in joints.get_all_welds()}

    # Girder gussets require a bare girder–girder crossing; the demo has none
    # (every girder node also carries a column, and stringers are excluded), so
    # only end plates + base plates are emitted here.
    assert not any(n.endswith("_gusset") for n in plate_names)
    assert any(n.endswith("_endplate") for n in plate_names)
    assert any(n.endswith("_baseplate") for n in plate_names)
    # And each emitted family welds.
    assert any("EP_" in n and n.endswith("_weld") for n in weld_names)
    assert any("BP_" in n and "_weld_" in n for n in weld_names)


def test_hp_stringers_never_get_connection_joints(demo):
    # The user requirement: no end plate / weld on any HP profile. HP bulb-flat
    # stringers are secondary members — is_frame_member rejects them, so no
    # connection references a stringer, at any joint family.
    from ada.topo_model.detail_joints import is_frame_member

    stringers = [
        b
        for b in demo.get_all_physical_objects(by_type=ada.Beam)
        if b.section.name.upper().startswith("HP")
    ]
    assert stringers, "demo should have HP stringers to exercise the exclusion"
    assert all(not is_frame_member(b) for b in stringers)

    detail(demo, {})
    joints = demo.get_by_name("Joints")
    stringer_names = {b.name for b in stringers}
    for conn in joints.parts.values():
        roles = conn.metadata.get("connection_info", {}).get("member_roles", {})
        members = {m for names in roles.values() for m in (names or [])}
        assert members.isdisjoint(stringer_names), f"{conn.name} details a stringer"


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


# ── Phase 3: per-joint option specs reach real geometry ──────────────


def _endplate_thickness(a: ada.Assembly) -> float:
    ep = next(
        p
        for p in a.get_by_name("Joints").get_all_physical_objects(by_type=ada.Plate)
        if p.name.endswith("_endplate")
    )
    return float(ep.t)


def test_nested_per_joint_option_alters_endplate_thickness():
    # The Phase-3 nested per-joint option shape ({slug: {enabled, <field>}}) must
    # reach the emitted geometry: the advertised end-plate thickness (mm) becomes
    # the Plate's thickness (m). Only the endplate family is enabled to isolate it.
    only_ep = {"girder_gusset": {"enabled": False}, "column_base_plate": {"enabled": False}}

    thin = build_topo_model()
    detail(thin, {**only_ep, "beam_column_endplate": {"enabled": True, "plate_t": 20.0}})
    thick = build_topo_model()
    detail(thick, {**only_ep, "beam_column_endplate": {"enabled": True, "plate_t": 50.0}})

    assert _endplate_thickness(thin) == pytest.approx(0.020)
    assert _endplate_thickness(thick) == pytest.approx(0.050)
    assert _endplate_thickness(thick) > _endplate_thickness(thin)


def test_base_plate_overhang_option_alters_plate_size():
    # The base-plate overhang (mm) grows the emitted Plate. Larger overhang -> a
    # larger footprint bounding box.
    def bp_extent(overhang_mm: float) -> float:
        a = build_topo_model()
        detail(
            a,
            {
                "girder_gusset": {"enabled": False},
                "beam_column_endplate": {"enabled": False},
                "column_base_plate": {"enabled": True, "overhang": overhang_mm},
            },
        )
        bp = next(
            p
            for p in a.get_by_name("Joints").get_all_physical_objects(by_type=ada.Plate)
            if p.name.endswith("_baseplate")
        )
        (x0, y0, _), (x1, y1, _) = bp.bbox().minmax
        return max(x1 - x0, y1 - y0)

    assert bp_extent(120.0) > bp_extent(20.0)


def test_box_clearance_option_grows_the_cut():
    # A larger box clash-cut clearance (mm) grows the PrimBox cut on the incoming
    # box beam (bigger cut volume).
    def cut_volume(clearance_mm: float) -> float:
        a = build_topo_model(girder_sec=_BOX_GIRDER)
        detail(
            a,
            {
                "girder_gusset": {"enabled": False},
                "beam_column_endplate": {"enabled": False},
                "column_base_plate": {"enabled": False},
                "box_to_box": {"enabled": True, "clearance": clearance_mm},
            },
        )
        box_beams = [b for b in a.get_all_physical_objects(by_type=ada.Beam) if b.section.name == _BOX_GIRDER]
        vol = 0.0
        for b in box_beams:
            for bl in b.booleans:
                p1, p2 = bl.primitive.p1, bl.primitive.p2
                vol += abs((p2[0] - p1[0]) * (p2[1] - p1[1]) * (p2[2] - p1[2]))
        return vol

    assert cut_volume(10.0) > cut_volume(0.0)


def test_flat_and_nested_option_shapes_are_equivalent():
    # Backward-compat: the historical FLAT toggle shape and the Phase-3 NESTED
    # per-joint shape select the same joint families.
    flat = build_topo_model()
    detail(flat, {"girder_gusset": False, "beam_column_endplate": True, "column_base_plate": False})
    nested = build_topo_model()
    detail(
        nested,
        {
            "girder_gusset": {"enabled": False},
            "beam_column_endplate": {"enabled": True},
            "column_base_plate": {"enabled": False},
        },
    )
    assert len(list(flat.get_by_name("Joints").parts.values())) == len(
        list(nested.get_by_name("Joints").parts.values())
    )
