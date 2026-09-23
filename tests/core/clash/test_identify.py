"""Joint identification -- finding contacts and turning them into a result document.

Numbers below are PINNED against the ``SteelStru`` demo (``ada.topo_model.build_topo_model``),
which is fixed geometry (mirrors ``tests/core/topo_model/test_detailing.py``'s pinning style): a
drift here means identification itself changed, not that the demo did.
"""

from __future__ import annotations

import pytest

import ada
from ada.clash import ClashOptions, identify_joints, run_clash_check
from ada.topo_model import build_topo_model


@pytest.fixture()
def demo() -> ada.Assembly:
    return build_topo_model()


# ── pinned counts on the demo ────────────────────────────────────────


def test_demo_joint_and_member_counts_pinned(demo):
    # Plate joints excluded so the beam-only baseline is stable and independent of the
    # plate-to-beam/plate-to-plate passes -- see test_plate_joints_are_found_when_enabled below
    # for those.
    result = run_clash_check(demo, source_key="demo", options=ClashOptions(include_plate_joints=False))
    assert dict(result.counts) == {
        "members": 72,
        "beams": 68,
        "plates": 4,
        "joints": 84,
        "joints_with_a_generator": 0,  # no specs registered in this test
    }


def test_demo_groups_pinned(demo):
    # The four families the pinned SteelStru layout actually contains -- a change to any of
    # these numbers means the beam-to-beam pass or the classifier changed, not the demo.
    result = run_clash_check(demo, source_key="demo", options=ClashOptions(include_plate_joints=False))
    seen = {(g.count, g.type_label) for g in result.groups}
    assert seen == {
        (48, "2 × BEAM · HP/I · Girder→Girder · perpendicular"),
        (24, "3 × BEAM · HP/I · Girder→Girder→Girder · parallel"),
        (8, "3 × BEAM · I · Column→Girder→Girder · perpendicular"),
        (4, "4 × BEAM · I · Column→Girder→Girder→Girder · perpendicular"),
    }


def test_plate_joints_are_found_when_enabled(demo):
    # The demo carries 4 plates; with the plate passes on, the joint count must exceed the
    # beam-only baseline -- a regression here means the plate-to-beam/plate-to-plate passes
    # silently stopped finding anything (they previously raised and were swallowed into a
    # warning -- see the identify.py fix note in the report). Exact pinned split (64
    # plate-to-beam + 2 plate-to-plate + the 84 beam-to-beam baseline = 150) is in
    # ``test_detection_coverage.py``, which exists specifically to pin non-zero detection per
    # pass rather than just "more than the baseline".
    beam_only = run_clash_check(demo, source_key="demo", options=ClashOptions(include_plate_joints=False))
    with_plates = run_clash_check(demo, source_key="demo", options=ClashOptions(include_plate_joints=True))
    assert with_plates.warnings == ()
    assert with_plates.counts["joints"] == 150
    assert with_plates.counts["joints"] > beam_only.counts["joints"]


# ── the property the panel's grouping leans on ───────────────────────


def test_every_joint_belongs_to_exactly_one_group(demo):
    # "sum(group.count) == len(joints)" is the property the module's own docstring names: a
    # joint that fell out of every group would be invisible in the only view (`groups`) the
    # panel reads joints through.
    result = run_clash_check(demo, source_key="demo", options=ClashOptions(include_plate_joints=False))
    assert sum(g.count for g in result.groups) == len(result.joints)
    # and no joint id appears in two groups
    ids_in_groups = [jid for g in result.groups for jid in g.joint_ids]
    assert len(ids_in_groups) == len(set(ids_in_groups))
    assert set(ids_in_groups) == {j.id for j in result.joints}


# ── determinism ───────────────────────────────────────────────────────


def test_joint_ids_are_deterministic_across_runs_with_the_same_options(demo):
    # A `clash_detail` job re-derives the joints a user selected from a CACHED result by id
    # alone (Decision 10, item 2): if the same (source, options) produced different ids on two
    # runs, that hand-off would silently detail the wrong joints.
    opts = ClashOptions(include_plate_joints=False)
    ids_a = sorted(j.id for j in run_clash_check(demo, source_key="demo", options=opts).joints)
    ids_b = sorted(j.id for j in run_clash_check(demo, source_key="demo", options=opts).joints)
    assert ids_a == ids_b


def test_joint_id_set_changes_when_the_option_set_changes(demo):
    # Changing an option that changes WHICH joints are found (here: whether the plate passes
    # run at all) must change the id set -- an option-blind id would let a cached result answer
    # for a check that was never actually run with those options.
    without_plates = run_clash_check(demo, source_key="demo", options=ClashOptions(include_plate_joints=False))
    with_plates = run_clash_check(demo, source_key="demo", options=ClashOptions(include_plate_joints=True))
    assert {j.id for j in without_plates.joints} != {j.id for j in with_plates.joints}


# ── root scoping ──────────────────────────────────────────────────────


def test_root_scopes_the_check_to_a_subtree(demo):
    # `root` exists so a plant-scale source can be asked about one area at a time (ClashOptions'
    # own docstring) -- scoping must actually shrink the member set, not just be accepted.
    whole = identify_joints(demo, ClashOptions(include_plate_joints=False))
    scoped = identify_joints(demo, ClashOptions(root="Columns", include_plate_joints=False))
    assert scoped.counts["members"] == 6
    assert scoped.counts["members"] < whole.counts["members"]


def test_root_refuses_a_name_that_does_not_exist(demo):
    with pytest.raises(ValueError, match="no part named"):
        identify_joints(demo, ClashOptions(root="does-not-exist"))


# ── the "no members" refusal, without raising ────────────────────────


def _model_with_only_a_shape() -> ada.Assembly:
    """The smallest source that reads into geometry but has no Beam/Plate at all."""
    a = ada.Assembly("A") / ada.Part("P")
    a.get_by_name("P").add_shape(ada.PrimBox("box", (0, 0, 0), (1, 1, 1)))
    return a


def test_a_shapes_only_source_yields_zero_members_and_a_warning_not_an_exception():
    # The honest answer for a STEP-like read (shapes, no members) is "cannot be checked", never
    # a stack trace and never a silent zero-joints result that reads as "no joints were found".
    model = _model_with_only_a_shape()
    outcome = identify_joints(model)
    assert outcome.counts["members"] == 0
    assert outcome.warnings and "no beams or plates" in outcome.warnings[0]


def test_run_clash_check_on_a_shapes_only_source_does_not_raise():
    model = _model_with_only_a_shape()
    result = run_clash_check(model, source_key="shapes-only")
    assert result.counts["members"] == 0
    assert result.joints == ()
    assert result.warnings
