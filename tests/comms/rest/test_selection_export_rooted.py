"""An exported element keeps its ancestry: every level above it, up to its model's top, is in the
file, so nothing is written without the levels it sits in."""

from __future__ import annotations

import numpy as np

import ada
from ada.clash.group import normalise_group
from ada.clash.group_model import build_group_model
from ada.comms.rest.selection_export import scope_to_selection


def _site_model(name: str = "asm", offset=(0.0, 0.0, 0.0)) -> ada.Assembly:
    deck = ada.Part("deck", placement=ada.Placement(origin=offset)) / [
        ada.Part("frame")
        / [ada.Beam("bm1", (0, 0, 0), (5, 0, 0), "IPE300"), ada.Beam("bm2", (0, 0, 0), (0, 5, 0), "IPE300")],
        ada.Part("stair") / [ada.Beam("bm3", (0, 0, 3), (5, 0, 3), "IPE300")],
    ]
    return ada.Assembly(name) / [ada.Part("site") / [deck]]


def _chain(obj) -> list[str]:
    """Names of the levels above ``obj``, top first, without the file's root."""
    return [p.name for p in reversed(obj.get_ancestors(include_self=False)) if not isinstance(p, ada.Assembly)]


def _beam(asm, name):
    return next(o for o in asm.get_all_physical_objects() if o.name == name)


def test_a_selected_element_keeps_every_level_above_it():
    out = scope_to_selection(_site_model(), "bm1", ["site", "deck", "frame", "bm1"])
    assert _chain(_beam(out, "bm1")) == ["site", "deck", "frame"]
    assert [o.name for o in out.get_all_physical_objects()] == ["bm1"]


def test_a_selected_level_keeps_the_levels_above_it_and_everything_below():
    out = scope_to_selection(_site_model(), "stair", ["site", "deck", "stair"])
    assert _chain(_beam(out, "bm3")) == ["site", "deck", "stair"]


def test_an_element_stays_where_its_levels_placed_it():
    src = _site_model(offset=(10.0, 20.0, 0.0))
    before = _beam(src, "bm1").placement.get_absolute_placement().origin.copy()
    out = scope_to_selection(src, "bm1", ["site", "deck", "frame", "bm1"])
    after = _beam(out, "bm1").placement.get_absolute_placement().origin
    assert np.allclose(before, after)


def _node(subject: str, element: str, path: list[str]) -> dict:
    return {
        "target": {"kind": "node", "provider": "p", "collection": "c", "subject": subject, "revision": "r"},
        "element": element,
        "path": path,
    }


def _read(models: dict):
    return lambda *, collection, subject, storage, revision, node: models[subject]


def test_members_of_one_model_share_their_ancestors_and_the_root_is_that_model():
    group = normalise_group(
        {
            "name": "picked",
            "members": [
                _node("s1", "bm1", ["site", "deck", "frame", "bm1"]),
                _node("s1", "bm3", ["site", "deck", "stair", "bm3"]),
            ],
        }
    )
    built = build_group_model(group, storage=None, read_node=_read({"s1": _site_model("plant")}), rooted=True)
    asm = built.model
    assert asm.name == "plant"
    assert list(asm.parts) == ["site"]
    assert _chain(_beam(asm, "bm1")) == ["site", "deck", "frame"]
    assert _chain(_beam(asm, "bm3")) == ["site", "deck", "stair"]
    # One deck, holding both branches.
    decks = [p for p in asm.get_all_subparts() if p.name == "deck"]
    assert len(decks) == 1


def test_members_of_two_models_each_keep_their_own_root():
    group = normalise_group(
        {
            "name": "picked",
            "members": [
                _node("s1", "bm1", ["site", "deck", "frame", "bm1"]),
                _node("s2", "bm2", ["site", "deck", "frame", "bm2"]),
            ],
        }
    )
    models = {"s1": _site_model("plant_a"), "s2": _site_model("plant_b")}
    asm = build_group_model(group, storage=None, read_node=_read(models), rooted=True).model
    assert sorted(asm.parts) == ["plant_a", "plant_b"]
    assert _chain(_beam(asm, "bm1")) == ["plant_a", "site", "deck", "frame"]
    assert _chain(_beam(asm, "bm2")) == ["plant_b", "site", "deck", "frame"]


def test_the_clash_check_layout_is_unchanged_without_rooted():
    group = normalise_group({"name": "g", "members": [_node("s1", "bm1", ["site", "deck", "frame", "bm1"])]})
    asm = build_group_model(group, storage=None, read_node=_read({"s1": _site_model()})).model
    assert list(asm.parts) == ["1: bm1"]
