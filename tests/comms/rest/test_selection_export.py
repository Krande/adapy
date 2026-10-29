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
    # No spaces, and a name's punctuation collapses to one separator.
    assert export_filename("PLATE 2 of FRAME /A1(3)-B", "ifc") == "PLATE_2_of_FRAME_A1_3_-B.ifc"


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


# --- export options: the STEP schema -------------------------------------------------------------


def _file_schema(path) -> str:
    import re

    text = path.read_text(errors="replace")
    m = re.search(r"FILE_SCHEMA\(\(\s*'([^']+)'", text)
    return m.group(1) if m else ""


@pytest.mark.parametrize(
    "schema, declared",
    [
        (None, "AUTOMOTIVE_DESIGN"),
        ("AP214", "AUTOMOTIVE_DESIGN"),
        ("AP242", "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"),
    ],
)
def test_a_step_export_declares_the_schema_asked_for(tmp_path, schema, declared):
    out = scope_to_selection(_model(), "deck_b")
    options = None if schema is None else {"step_schema": schema}
    dest = write_selection(out, "step", tmp_path / "deck_b.step", options)
    assert _file_schema(dest).startswith(declared)


def test_export_options_are_checked_not_dropped():
    from ada.comms.rest.selection_export import parse_export_options

    defaults = {"step_schema": "AP214", "ifc_schema": "IFC4", "ifc_swept_primitives": False, "ifc_header": None}
    assert parse_export_options(None) == defaults
    assert parse_export_options({"step_schema": "ap242"}) == {**defaults, "step_schema": "AP242"}
    assert parse_export_options({"ifc_swept_primitives": True})["ifc_swept_primitives"] is True
    with pytest.raises(SelectionExportError, match="ifc_swept_primitives"):
        parse_export_options({"ifc_swept_primitives": "yes"})
    assert parse_export_options({"ifc_schema": "ifc4x3"})["ifc_schema"] == "IFC4X3"
    with pytest.raises(SelectionExportError, match="step_schema"):
        parse_export_options({"step_schema": "AP203"})
    with pytest.raises(SelectionExportError, match="ifc_schema"):
        parse_export_options({"ifc_schema": "IFC2X3"})
    with pytest.raises(SelectionExportError, match="unknown export option"):
        parse_export_options({"tolerance": 1})


def test_only_options_that_change_the_file_change_its_key():
    target = {"source_key": "m.ifc"}
    args = dict(element="PL1", path=["deck_b", "PL1"])
    plain = selection_derived_key("tok", target, fmt="step", filename="PL1.step", **args)
    # The default asked for explicitly is the file it always was -- the cache stays warm.
    assert (
        selection_derived_key(
            "tok", target, fmt="step", filename="PL1.step", export_options={"step_schema": "AP214"}, **args
        )
        == plain
    )
    assert (
        selection_derived_key(
            "tok", target, fmt="step", filename="PL1.step", export_options={"step_schema": "AP242"}, **args
        )
        != plain
    )
    # A STEP schema does not make another IFC.
    ifc = selection_derived_key("tok", target, fmt="ifc", filename="PL1.ifc", **args)
    assert (
        selection_derived_key(
            "tok", target, fmt="ifc", filename="PL1.ifc", export_options={"step_schema": "AP242"}, **args
        )
        == ifc
    )
    # The IFC schema does, and an IFC export's key names it even at the default: earlier IFC
    # exports were written in another schema under the default key.
    ifc43 = selection_derived_key(
        "tok", target, fmt="ifc", filename="PL1.ifc", export_options={"ifc_schema": "IFC4X3"}, **args
    )
    assert ifc43 != ifc
    assert (
        selection_derived_key(
            "tok", target, fmt="step", filename="PL1.step", export_options={"ifc_schema": "IFC4X3"}, **args
        )
        == plain
    )


def test_an_ifc_header_names_the_file_its_author_and_project(tmp_path):
    import ifcopenshell

    out = scope_to_selection(_model(), "deck_b")
    header = {"file_name": "ABC_deck_b.ifc", "author": "Jane Doe", "project": "ABC"}
    # Written to a temporary path, as the worker does: the header still says what the file is.
    dest = write_selection(out, "ifc", tmp_path / "tmp1234.ifc", {"ifc_header": header})
    f = ifcopenshell.open(str(dest))
    assert f.header.file_name.name == "ABC_deck_b.ifc"
    assert tuple(f.header.file_name.author) == ("Jane Doe",)
    assert f.header.file_name.originating_system.startswith("adapy")
    assert f.by_type("IfcProject")[0].Name == "ABC"
    assert f.by_type("IfcPerson")[0].GivenName == "Jane Doe"


def test_an_ifc_header_makes_another_file_and_a_step_ignores_it():
    target = {"source_key": "m.ifc"}
    args = dict(element="PL1", path=["deck_b", "PL1"])
    one = selection_derived_key(
        "tok", target, fmt="ifc", filename="PL1.ifc", export_options={"ifc_header": {"author": "A"}}, **args
    )
    two = selection_derived_key(
        "tok", target, fmt="ifc", filename="PL1.ifc", export_options={"ifc_header": {"author": "B"}}, **args
    )
    assert one != two, "one person's export must not be served under another's name"
    step = selection_derived_key("tok", target, fmt="step", filename="PL1.step", **args)
    assert (
        selection_derived_key(
            "tok", target, fmt="step", filename="PL1.step", export_options={"ifc_header": {"author": "A"}}, **args
        )
        == step
    )


@pytest.mark.parametrize("schema, declared", [(None, "IFC4"), ("IFC4", "IFC4"), ("IFC4X3", "IFC4X3_ADD2")])
def test_an_ifc_export_is_written_in_the_schema_asked_for(tmp_path, schema, declared):
    import ifcopenshell

    out = scope_to_selection(_model(), "deck_b")
    options = None if schema is None else {"ifc_schema": schema}
    dest = write_selection(out, "ifc", tmp_path / "deck_b.ifc", options)
    f = ifcopenshell.open(str(dest))
    assert f.schema_identifier.upper() == declared
    assert f.by_type("IfcProduct"), "the selection's objects were not written"
