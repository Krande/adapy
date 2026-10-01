"""``ada.comms.rest.selection_export`` -- finding a tree selection again in a re-read model, and
moving it into an assembly a writer can serialise.

The model below is built so that the cases that matter are present at once: a NAME THAT REPEATS
in two decks (the normal case in a real model), a part nested under a PLACED part (the
re-parenting has to keep it where its ancestors put it), and a lone object whose owner is placed.
"""

from __future__ import annotations

import pytest

import ada
from ada.base.changes import ChangeAction
from ada.comms.rest.selection_export import (
    SelectionExportError,
    export_filename,
    find_selection,
    scope_to_selection,
    selection_derived_key,
    write_selection,
)


def _model() -> ada.Assembly:
    return ada.Assembly("asm") / [
        ada.Part("deck_a", placement=ada.Placement(origin=(0, 0, 10)))
        / [
            ada.Beam("bm1", (0, 0, 0), (5, 0, 0), "IPE300"),
            ada.Plate("pl1", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01),
            ada.Part("sub", placement=ada.Placement(origin=(1, 0, 0))) / ada.PrimBox("PL1", (0, 0, 0), (1, 1, 1)),
        ],
        ada.Part("deck_b")
        / [ada.Beam("bm2", (0, 0, 0), (0, 5, 0), "IPE300"), ada.PrimBox("PL1", (0, 0, 0), (1, 1, 1))],
    ]


def _origin(obj) -> list[float]:
    """Where the writer will put ``obj``: its placement accumulated through what it now sits in."""
    return [round(float(v), 6) for v in obj.placement.get_absolute_placement(include_rotations=True).origin]


# ── find_selection ─────────────────────────────────────────────────────


def test_a_unique_name_is_found_without_a_path():
    assert find_selection(_model(), "bm2").name == "bm2"


def test_a_repeated_name_is_refused_without_a_path_rather_than_guessed():
    with pytest.raises(SelectionExportError, match="2 elements are named 'PL1'"):
        find_selection(_model(), "PL1")


def test_the_path_tells_two_same_named_elements_apart():
    found = find_selection(_model(), "PL1", ["deck_b", "PL1"])
    assert found.parent.name == "deck_b"
    nested = find_selection(_model(), "PL1", ["deck_a", "sub", "PL1"])
    assert nested.parent.name == "sub"


def test_a_path_only_has_to_match_its_end():
    """The GLB may carry levels above what the viewer sent (its root row is relabelled, so the
    viewer never sends it); a shorter path is a suffix, never a prefix."""
    assert find_selection(_model(), "PL1", ["sub", "PL1"]).parent.name == "sub"


def test_a_name_the_model_does_not_carry_says_how_to_export_anyway():
    with pytest.raises(SelectionExportError, match="root row"):
        find_selection(_model(), "nope")


def test_a_path_under_which_the_name_does_not_sit_is_refused():
    with pytest.raises(SelectionExportError, match="none sits under deck_x"):
        find_selection(_model(), "PL1", ["deck_x", "PL1"])


def test_a_path_that_does_not_end_at_the_element_is_refused():
    with pytest.raises(SelectionExportError, match="does not end at"):
        find_selection(_model(), "PL1", ["deck_b"])


# ── scope_to_selection ─────────────────────────────────────────────────


def test_the_whole_model_of_an_assembly_is_the_assembly_itself():
    model = _model()
    assert scope_to_selection(model, None) is model


def test_a_bare_part_is_wrapped_for_the_whole_model():
    """What a provider's concepts reader hands back is a Part; the IFC writer needs an Assembly."""
    part = ada.Part("node-1") / ada.Beam("b", (0, 0, 0), (1, 0, 0), "IPE200")
    out = scope_to_selection(part, None)
    assert isinstance(out, ada.Assembly)
    assert [o.name for o in out.get_all_physical_objects()] == ["b"]


def test_a_selected_part_brings_its_subtree_and_nothing_else():
    out = scope_to_selection(_model(), "deck_a", ["deck_a"])
    assert sorted(o.name for o in out.get_all_physical_objects()) == ["PL1", "bm1", "pl1"]
    assert [p.name for p in out.get_all_subparts()] == ["deck_a", "sub"]


def test_a_nested_part_stays_where_its_ancestors_placed_it():
    out = scope_to_selection(_model(), "sub", ["deck_a", "sub"])
    (box,) = out.get_all_physical_objects()
    assert _origin(box.parent) == [1.0, 0.0, 10.0]


def test_a_lone_object_keeps_its_owner_as_a_placed_holder():
    out = scope_to_selection(_model(), "bm1", ["deck_a", "bm1"])
    (beam,) = out.get_all_physical_objects()
    assert beam.parent.name == "deck_a"
    assert _origin(beam.parent) == [0.0, 0.0, 10.0]


def test_everything_moved_is_marked_for_the_writer():
    """The IFC writer emits ADDED objects only; one read from an IFC arrives NOCHANGE."""
    model = _model()
    for obj in model.get_all_physical_objects():
        obj.change_type = ChangeAction.NOCHANGE
    out = scope_to_selection(model, "deck_a", ["deck_a"])
    assert {o.change_type for o in out.get_all_physical_objects()} == {ChangeAction.ADDED}


# ── writing ──────────────────────────────────────────────────────────


def test_an_ifc_read_selection_round_trips_through_ifc(tmp_path):
    """The whole point of re-parenting: objects read FROM an IFC write back out as a new one."""
    src = tmp_path / "src.ifc"
    _model().to_ifc(src)
    out = scope_to_selection(ada.from_ifc(src), "deck_a", ["deck_a"])
    dest = write_selection(out, "ifc", tmp_path / "deck_a.ifc")
    back = ada.from_ifc(dest)
    assert sorted(o.name for o in back.get_all_physical_objects()) == ["PL1", "bm1", "pl1"]


def test_an_unknown_format_is_refused(tmp_path):
    with pytest.raises(SelectionExportError, match="unsupported export format"):
        write_selection(scope_to_selection(_model(), None), "obj", tmp_path / "x.obj")


# ── naming ───────────────────────────────────────────────────────────


def test_the_download_is_named_after_the_selection_and_never_a_path():
    assert export_filename("deck_a", "step") == "deck_a.step"
    assert export_filename("../../etc/passwd", "ifc") == "etc_passwd.ifc"
    assert export_filename(None, "ifc") == "selection.ifc"


def test_two_same_named_selections_in_two_places_are_two_keys():
    target = {"source_key": "models/a.ifc"}
    one = selection_derived_key("tok", target, fmt="ifc", element="PL1", path=["deck_a", "PL1"], filename="PL1.ifc")
    two = selection_derived_key("tok", target, fmt="ifc", element="PL1", path=["deck_b", "PL1"], filename="PL1.ifc")
    assert one != two
    assert one.startswith("_derived/export/tok/") and one.endswith("/PL1.ifc")


def test_the_same_selection_is_the_same_key():
    target = {"collection": "c", "subject": "s", "revision": "r", "node": "n"}
    args = dict(fmt="step", element=None, path=[], filename="s.step")
    assert selection_derived_key("tok", target, **args) == selection_derived_key("tok", dict(target), **args)
