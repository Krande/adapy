"""``hierarchy.json`` -- the three reader rules, each of which has bitten the prior art."""

import json

import pytest

from ada.assets.projection import (
    BASE_COLS,
    HIERARCHY_SCHEMA,
    HierarchyError,
    HierarchySlice,
    build_hierarchy,
    parse_hierarchy,
)

NODES = [
    {"id": "root", "parent": None, "label": "Plant A", "kind": "site", "leaf": 0, "delivery": ""},
    {"id": "n1", "parent": "root", "label": "Unit 1", "kind": "unit", "leaf": 0, "delivery": "build"},
    {"id": "n2", "parent": "n1", "label": "Pump", "kind": "equipment", "leaf": 1, "delivery": "mesh"},
]


def _slice(**over):
    kw = dict(provider="fixture-lines", collection="plant-a", produced_at="2026-09-21T14:29:00Z", nodes=NODES)
    kw.update(over)
    return build_hierarchy(**kw)


def test_roundtrip():
    s = _slice()
    assert parse_hierarchy(s.to_json()) == s
    assert s.node_ids() == ("root", "n1", "n2")


def test_unknown_schema_is_refused():
    doc = json.loads(_slice().to_json())
    doc["schema"] = "ada.assets/hierarchy@2"
    with pytest.raises(HierarchyError, match="unknown hierarchy schema"):
        parse_hierarchy(json.dumps(doc))


def test_columns_are_indexed_by_name_not_position():
    """Adding a column must not reinterpret existing rows for a reader that predates it."""
    s = build_hierarchy(
        provider="p",
        collection="c",
        produced_at="t",
        nodes=[{**NODES[2], "path": "/root/n1/n2"}],
        extra_cols=("path",),
    )
    back = parse_hierarchy(s.to_json())
    assert back.cols[: len(BASE_COLS)] == BASE_COLS
    rec = next(back.records())
    assert rec["id"] == "n2" and rec["delivery"] == "mesh" and rec["path"] == "/root/n1/n2"
    # Reaching in by name still lands on the right value with the extra column present.
    assert back.rows[0][back.column("kind")] == "equipment"


def test_unknown_column_name_is_an_error_not_a_wrong_answer():
    with pytest.raises(HierarchyError, match="no column 'nope'"):
        _slice().column("nope")


@pytest.mark.parametrize("truthy", [1, True, "1", "true", "TRUE", "yes"])
def test_leaf_accepts_every_form_producers_emit(truthy):
    """A strict reader turns every node into a branch and the tree stops expanding correctly."""
    s = build_hierarchy(provider="p", collection="c", produced_at="t", nodes=[{**NODES[2], "leaf": truthy}])
    assert next(s.records())["leaf"] is True


@pytest.mark.parametrize("falsy", [0, False, "0", "false", None, ""])
def test_leaf_falsy_forms(falsy):
    s = build_hierarchy(provider="p", collection="c", produced_at="t", nodes=[{**NODES[0], "leaf": falsy}])
    assert next(s.records())["leaf"] is False


def test_delivery_vocabulary_is_cores_but_kind_and_label_are_not():
    with pytest.raises(HierarchyError, match="delivery 'glb' not in"):
        build_hierarchy(provider="p", collection="c", produced_at="t", nodes=[{**NODES[0], "delivery": "glb"}])
    # kind is the provider's to choose -- anything goes.
    s = build_hierarchy(
        provider="p", collection="c", produced_at="t", nodes=[{**NODES[0], "kind": "whatever-vendor-says"}]
    )
    assert next(s.records())["kind"] == "whatever-vendor-says"


def test_missing_required_column_is_refused():
    with pytest.raises(HierarchyError, match="missing required column"):
        HierarchySlice(provider="p", collection="c", produced_at="t", cols=("id", "parent"), rows=())


def test_short_row_is_refused_rather_than_padded():
    with pytest.raises(HierarchyError, match="rows are positional"):
        HierarchySlice(provider="p", collection="c", produced_at="t", cols=BASE_COLS, rows=(("a", None),))


def test_columnar_form_stays_compact():
    """The reason for the shape: ~64 B/node, so a 41k spine is one ~2.6 MB fetch."""
    nodes = [
        {"id": f"n{i}", "parent": "root", "label": f"Node {i}", "kind": "part", "leaf": 1, "delivery": ""}
        for i in range(2000)
    ]
    payload = build_hierarchy(provider="p", collection="c", produced_at="t", nodes=nodes).to_json()
    assert len(payload) / len(nodes) < 80, f"{len(payload) / len(nodes):.0f} B/node is too fat for a 41k spine"
    assert json.loads(payload)["schema"] == HIERARCHY_SCHEMA
