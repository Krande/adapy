"""Detection must be broad enough that nobody opens the Clash Check tab and sees zero joints.

The SteelStru pins in ``test_identify.py`` prove identification works on ONE model shape. They do
not prove the plate passes ever find anything real, that a box-section frame gets a generator
offered, that an IFC-sourced model (the path a user actually exercises in the viewer) survives the
round trip, or that a tiny, by-inspection model comes back non-zero. Each test below pins a
NON-ZERO count on a genuinely different input -- "the pass runs" and "the pass finds something"
are different claims, and only the second is worth shipping.
"""

from __future__ import annotations

import ada
from ada.api.connections.spec import _clear_registry, get_registered
from ada.clash import ClashOptions, run_clash_check
from ada.clash.builtin_specs import (
    BOX_JOINT_SPEC,
    GIRDER_GUSSET_SPEC,
    register_builtin_specs,
)
from ada.clash.identify import identify_joints
from ada.clash.match import applicable_specs, detail_pairs
from ada.topo_model import build_topo_model

# ── 1. plate joints actually detect (not just "don't raise") ────────


def test_demo_plate_to_beam_pass_finds_a_non_zero_pinned_count():
    # Pinned on the SteelStru demo: 4 plates, 64 plate-to-beam contacts. This is the pass that
    # was silently dead before the identify.py fix (`plate.poly.placement` does not exist on
    # `CurvePoly2d` -- see the report) -- it used to fail and get swallowed into a warning,
    # contributing zero joints while looking like it ran.
    model = build_topo_model()
    result = run_clash_check(model, source_key="demo", options=ClashOptions(include_plate_joints=True))
    plate_beam = [j for j in result.joints if {m.kind for m in j.members} == {"PLATE", "BEAM"}]
    assert result.warnings == ()
    assert len(plate_beam) == 64


def test_demo_plate_to_plate_pass_finds_a_non_zero_pinned_count():
    # Pinned: 2 of the demo's 4 plates touch at a perpendicular edge. This pass was ALSO
    # silently dead (`getattr(connections, "edges", ())` read an attribute `PlateConnections`
    # never had, so it always returned the empty default) -- fixed to read `edge_connected` /
    # `mid_span_connected`, the two dicts the type actually carries. See
    # `test_plate_checking.py` for the primitive this pass is built on.
    model = build_topo_model()
    result = run_clash_check(model, source_key="demo", options=ClashOptions(include_plate_joints=True))
    plate_plate = [j for j in result.joints if all(m.kind == "PLATE" for m in j.members)]
    assert len(plate_plate) == 2


def test_plate_to_plate_pass_on_a_minimal_hand_built_pair():
    # The same two-perpendicular-plates fixture `test_plate_checking.py` pins at the
    # `find_edge_connected_perpendicular_plates` level, run through the full clash pipeline --
    # one plate-plate joint, obvious by inspection, independent of the demo's own layout.
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pl1 = ada.Plate("pl1", square, 0.01, orientation=ada.Placement(xdir=(1, 0, 0), zdir=(0, -1, 0)))
    pl2 = ada.Plate("pl2", square, 0.01, orientation=ada.Placement(xdir=(0, 1, 0), zdir=(1, 0, 0)))
    model = ada.Assembly("A") / (ada.Part("P") / [pl1, pl2])

    result = run_clash_check(model, source_key="plate-plate", options=ClashOptions(include_plate_joints=True))
    assert result.counts["joints"] == 1
    assert all(m.kind == "PLATE" for m in result.joints[0].members)


# ── 2. a box-section variant: builtin.box_joint applicable, girder_gusset not ─


def test_box_joint_matches_a_genuinely_node_connected_box_pair():
    # NOTE (see report): `build_topo_model(girder_sec="BG300x300x8x8")` does NOT exercise this --
    # its box girders meet mid-span (a T-crossing `beam_cross_check` finds), never at a shared
    # end node, so `Connections.find`'s node-based pass (what `identify_joints` runs) never sees
    # them and `builtin.box_joint` matches zero joints on that model. A hand-built pair that
    # meets at a shared END node is the genuinely different, non-zero case.
    _clear_registry()
    register_builtin_specs()
    try:
        b1 = ada.Beam("bx1", (0, 0, 0), (2, 0, 0), "BG300x300x8x8")
        b2 = ada.Beam("bx2", (2, 0, 0), (2, 2, 0), "BG300x300x8x8")
        model = ada.Assembly("A") / (ada.Part("P") / [b1, b2])

        result = run_clash_check(model, source_key="box-corner", options=ClashOptions(include_plate_joints=False))

        assert result.counts["joints"] == 1
        assert result.counts["joints_with_a_generator"] == 1
        joint = result.joints[0]
        assert [a.spec for a in joint.applicable] == [BOX_JOINT_SPEC.name]
        # and the girder-gusset spec (I-family only) must NOT offer for a pure-box pair.
        assert GIRDER_GUSSET_SPEC.name not in [a.spec for a in joint.applicable]
    finally:
        _clear_registry()


def test_box_section_demo_variant_offers_only_joints_its_builder_can_make():
    # This used to assert ZERO matches on the box-girder variant, and the zero was an artefact:
    # the matcher bound the LANDING role to the joint's own landing member, which at a column head
    # is the COLUMN, so a box-to-box spec could never bind the two box girders meeting there. The
    # same artefact in reverse made the I-section demo offer 12 joints the builder then refused at
    # build time ("Not all Pre-requisite member types ['Girder', 'Girder'] are found").
    #
    # So the fact worth pinning is not a number on its own: it is that everything offered can
    # actually be BUILT. A spec that claims a joint its builder rejects is worse than one that
    # claims nothing, because the refusal only surfaces as a failed job.
    _clear_registry()
    register_builtin_specs()
    try:
        model = build_topo_model(girder_sec="BG300x300x8x8")
        result = run_clash_check(model, source_key="box-demo", options=ClashOptions(include_plate_joints=False))
        assert result.counts["joints"] == 84  # same beam-beam topology as the I-section demo
        assert result.counts["joints_with_a_generator"] > 0

        outcome = identify_joints(model, ClashOptions(include_plate_joints=False))
        offered = 0
        for found in outcome.joints:
            specs = applicable_specs(found.members, landing=found.landing)
            if not specs:
                continue
            offered += 1
            registered = get_registered(sorted(specs, key=lambda s: (-s.priority, s.spec))[0].spec)
            for landing, incoming in detail_pairs(registered.spec, found):
                # Builds, rather than merely binds: the builder's own prerequisites are the test.
                registered.fn(landing=landing, incoming=incoming, centre=found.centre, name=f"t{offered}")
        assert offered == result.counts["joints_with_a_generator"]
    finally:
        _clear_registry()


# ── 3. an IFC round trip: the path a user actually exercises in the viewer ────


def test_ifc_round_trip_preserves_detection(tmp_path):
    # Export the demo with adapy's own IFC writer, read it back with `from_ifc`, and run the
    # same check. A reader that lost `member_type` or the section family would silently produce
    # zero matches while still producing joints -- so this pins BOTH the joint count and that it
    # equals the in-memory model's, not just that it is non-zero.
    model = build_topo_model()
    in_memory = run_clash_check(model, source_key="demo", options=ClashOptions(include_plate_joints=False))

    ifc_path = tmp_path / "demo.ifc"
    model.to_ifc(ifc_path)
    reloaded = ada.from_ifc(ifc_path)

    reloaded_result = run_clash_check(
        reloaded, source_key="demo-ifc-roundtrip", options=ClashOptions(include_plate_joints=False)
    )

    assert reloaded_result.counts["joints"] > 0
    assert reloaded_result.counts["joints"] == in_memory.counts["joints"] == 84
    assert {g.type_label for g in reloaded_result.groups} == {g.type_label for g in in_memory.groups}
    assert {g.count for g in reloaded_result.groups} == {g.count for g in in_memory.groups}


# ── 4. a hand-built minimal frame, obvious by inspection ────────────


def test_minimal_three_beam_frame_is_obviously_one_joint():
    # A column and two girders meeting at one node: by inspection, exactly one joint with all
    # three members. A regression that halves detection on the 72-member demo is invisible; on
    # three beams it is not.
    column = ada.Beam("col1", (0, 0, -1), (0, 0, 0), "IPE200")
    girder_a = ada.Beam("g1", (0, 0, 0), (2, 0, 0), "IPE200")
    girder_b = ada.Beam("g2", (0, 0, 0), (0, 2, 0), "IPE200")
    model = ada.Assembly("A") / (ada.Part("P") / [column, girder_a, girder_b])

    result = run_clash_check(model, source_key="tripod", options=ClashOptions(include_plate_joints=False))

    assert result.counts["joints"] == 1
    assert {m.name for m in result.joints[0].members} == {"col1", "g1", "g2"}


def test_minimal_frame_with_specs_registered_opens_non_empty_in_both_counts():
    # The failure the user is worried about, stated as one assertion: a tab that opens to
    # `counts.joints == 0` OR `counts.joints_with_a_generator == 0` reads as "nothing here" even
    # when detection quietly worked. Both must be non-zero together on a model this small.
    _clear_registry()
    register_builtin_specs()
    try:
        column = ada.Beam("col1", (0, 0, -1), (0, 0, 0), "IPE200")
        girder_a = ada.Beam("g1", (0, 0, 0), (2, 0, 0), "IPE200")
        girder_b = ada.Beam("g2", (0, 0, 0), (0, 2, 0), "IPE200")
        model = ada.Assembly("A") / (ada.Part("P") / [column, girder_a, girder_b])

        result = run_clash_check(model, source_key="tripod", options=ClashOptions(include_plate_joints=False))

        assert result.counts["joints"] > 0
        assert result.counts["joints_with_a_generator"] > 0
        assert [a.spec for a in result.joints[0].applicable] == [GIRDER_GUSSET_SPEC.name]
    finally:
        _clear_registry()


def test_demo_opens_non_empty_in_both_counts_with_builtins_registered():
    # The same property, pinned once more on the full SteelStru demo with the built-in specs
    # registered -- the exact condition of opening the real tab on the real demo.
    _clear_registry()
    register_builtin_specs()
    try:
        model = build_topo_model()
        result = run_clash_check(model, source_key="demo", options=ClashOptions(include_plate_joints=False))
        assert result.counts["joints"] == 84
        assert result.counts["joints_with_a_generator"] == 12
        assert result.counts["joints"] > 0 and result.counts["joints_with_a_generator"] > 0
    finally:
        _clear_registry()
