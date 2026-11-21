"""ada.assoc: element keys, the connection key, rows and their flags, and member resolution."""

from __future__ import annotations

import pytest

from ada.assoc import (
    LinkRow,
    SourceState,
    connection_key,
    file_key,
    flag_rows,
    group_rows,
    key_ancestors,
    node_key,
    normalise_path,
    normalise_segment,
    parse_key,
)
from ada.assoc.records import (
    decode_sheet,
    decode_state,
    encode_sheet,
    encode_state,
    params_hash,
)
from ada.assoc.resolve import member_identities, resolver_for
from ada.assoc.store import LinkQuery, query_rows
from ada.clash.sidecar import ContactRecord, ContactRun


def test_names_normalise_to_one_spelling():
    assert normalise_segment("  beam   b1 ") == "BEAM B1"
    assert normalise_segment("a/b#c%d") == "A%2FB%23C%25D"
    assert normalise_path(["rack", " Level 1", "", "beam/1"]) == "RACK/LEVEL 1/BEAM%2F1"
    assert normalise_path("/rack//level 1/") == "RACK/LEVEL 1"


def test_keys_round_trip():
    k = node_key("prov", "coll", "n-42")
    assert k == "node:prov/coll/n-42"
    p = parse_key(k)
    assert (p.form, p.provider, p.collection, p.node_id) == ("node", "prov", "coll", "n-42")
    assert str(p) == k

    g = file_key("models/frame.ifc", guid="2O2Fr$t4X7Zf8NOew3FLOH")
    assert parse_key(g).guid == "2O2Fr$t4X7Zf8NOew3FLOH" and str(parse_key(g)) == g

    f = file_key("/models/frame.stp", path=["Frame", "Beam 1"])
    assert f == "file:models/frame.stp#p:FRAME/BEAM 1"
    assert parse_key(f).path == "FRAME/BEAM 1"
    assert parse_key(f).base == "file:models/frame.stp"

    sub = node_key("", "coll", "subject", path=["Rack", "B1"])
    assert sub == "node:/coll/subject#p:RACK/B1"
    assert parse_key(sub).node_id == "subject"

    for bad in ("", "x:y", "node:a/b", "file:", "file:a#q:1", "node:a/b/c#g:1"):
        with pytest.raises(ValueError):
            parse_key(bad)


def test_a_name_path_spells_its_ancestors():
    key = file_key("m.stp", path=["rack", "level 1", "beam"])
    assert key_ancestors(key) == ["file:m.stp", "file:m.stp#p:RACK", "file:m.stp#p:RACK/LEVEL 1"]
    assert key_ancestors(node_key("p", "c", "n")) == []


def test_the_connection_key_is_order_blind_and_millimetre_rounded():
    a = connection_key("spec", [("landing", "k1"), ("incoming", "k2")], (1.0, 2.0, 3.0))
    b = connection_key("spec", [("incoming", "k2"), ("landing", "k1")], (1.0002, 2.0004, 2.9996))
    assert a == b and len(a) == 16
    assert connection_key("other", [("landing", "k1"), ("incoming", "k2")], (1.0, 2.0, 3.0)) != a
    assert connection_key("spec", [("landing", "k1"), ("incoming", "k2")], (1.002, 2.0, 3.0)) != a
    assert connection_key("spec", [("landing", "k2"), ("incoming", "k1")], (1.0, 2.0, 3.0)) != a


def _row(kind="contact", key="file:m#p:A", created="2026-01-01T00:00:00Z", **kw):
    return LinkRow(kind=kind, element_key=key, doc_key=kw.pop("doc_key", "doc1"), created_at=created, **kw)


def test_rows_are_flagged_stale_orphan_and_superseded():
    old = _row(group="g1", fp="aaa", source="s1", created="2026-01-01T00:00:00Z")
    new = _row(group="g1", fp="bbb", source="s1", created="2026-01-02T00:00:00Z", doc_key="doc2")
    gone = _row(key="file:m#p:GONE", group="g2", fp="ccc", source="s1")
    snapshot = SourceState("s1", "2026-01-03T00:00:00Z", fps={"file:m#p:A": "bbb"})
    flagged = {(d["doc_key"], d["element_key"]): d for d in flag_rows([old, new, gone], sources={"s1": snapshot})}
    assert flagged[("doc1", "file:m#p:A")]["stale"] and flagged[("doc1", "file:m#p:A")]["superseded"]
    assert not flagged[("doc2", "file:m#p:A")]["stale"] and not flagged[("doc2", "file:m#p:A")]["superseded"]
    assert flagged[("doc1", "file:m#p:GONE")]["orphan"]
    # A snapshot older than a row says nothing about it.
    early = SourceState("s1", "2025-01-01T00:00:00Z", fps={})
    assert not any(d["orphan"] for d in flag_rows([old], sources={"s1": early}))
    groups = group_rows(flag_rows([old, new, gone]))
    assert [g["group"] for g in groups] == ["g1", "g2"] and groups[0]["kinds"] == {"contact": 2}


def test_sheets_and_states_round_trip():
    rows = [_row(anc=("file:m",), ck="0" * 16, data={"x": 1}), _row(kind="weld")]
    kind, doc, back = decode_sheet(encode_sheet("contact", "doc1", rows))
    assert (kind, doc) == ("contact", "doc1") and [r.to_dict() for r in back] == [rows[0].to_dict()]
    s = SourceState("s", "2026-01-01T00:00:00Z", revision="r1", fps={"k": "f"})
    assert decode_state(encode_state(s)) == s
    assert params_hash({"a": 1, "b": 2}) == params_hash({"b": 2, "a": 1})


def test_a_query_pages_by_group_and_reads_under_a_node():
    top = "file:m#p:RACK"
    rows = [
        _row(key=f"file:m#p:RACK/B{i}", anc=("file:m", top), group=f"g{i:02d}", doc_key=f"d{i}") for i in range(5)
    ] + [_row(key="file:m#p:OTHER", group="zz")]
    q = LinkQuery(under=[top], limit=2)
    page = query_rows(rows, q)
    assert page["summary"]["rows"] == 5 and [g["group"] for g in page["groups"]] == ["g00", "g01"]
    page2 = query_rows(rows, LinkQuery(under=[top], limit=2, cursor=page["next"]))
    assert [g["group"] for g in page2["groups"]] == ["g02", "g03"]
    page3 = query_rows(rows, LinkQuery(under=[top], limit=2, cursor=page2["next"]))
    assert [g["group"] for g in page3["groups"]] == ["g04"] and page3["next"] is None


def _run(target, members, contacts=(), **kw):
    return ContactRun(
        run_id="r1",
        created_at="2026-01-01T00:00:00Z",
        engine="worker",
        target=target,
        geometry={"key": "g.glb", "sha256": "abc", **kw.pop("geometry", {})},
        options={},
        members=tuple(members),
        contacts=tuple(contacts),
        **kw,
    )


def test_members_resolve_through_the_glb_hierarchy_and_node_ids():
    js = {
        "scenes": [
            {
                "extras": {
                    "id_hierarchy": {"0": ["Rack", "*"], "1": ["Level 1", "0"], "2": ["Beam A", "1"]},
                    "node_ids": {"0": "n0", "2": "n2"},
                }
            }
        ]
    }
    file_run = _run({"kind": "file", "source_key": "m.stp"}, [{"id": "2", "name": "Beam A"}])
    [ident] = member_identities(file_run, js)
    assert ident["key"] == "file:m.stp#p:RACK/LEVEL 1/BEAM A"
    assert ident["anc"] == ["file:m.stp", "file:m.stp#p:RACK", "file:m.stp#p:RACK/LEVEL 1"]

    node_run = _run(
        {"kind": "node", "collection": "c", "subject": "s"},
        [{"id": "2", "name": "Beam A"}, {"id": "1", "name": "Level 1"}],
    )
    by_node, by_path = member_identities(node_run, js, {"2": "fp2"})
    assert by_node["key"] == "node:/c/n2" and by_node["fp"] == "fp2"
    assert by_node["anc"] == ["node:/c/s", "node:/c/n0"]
    assert by_path["key"] == "node:/c/s#p:RACK/LEVEL 1"

    # Without the GLB a member is its name.
    [bare] = member_identities(file_run)
    assert bare["key"] == "file:m.stp#p:BEAM A"
    assert resolver_for({"kind": "file", "source_key": "m"}, None).by_id == {}


def test_contact_rows_are_two_per_clash_with_roles():
    from ada.assoc.rows import contact_rows

    rec = ContactRecord(
        id="c1",
        members=(0, 1),
        near_points=((0.0, 0.0, 0.0), (0.0, 0.0, 0.002)),
        normal=(0.0, 0.0, 1.0),
        distance=-0.001,
        contact_area=0.01,
        roles={"incoming": 1, "landing": 0},
        applicable=({"name": "spec_a"},),
    )
    run = _run({"kind": "file", "source_key": "m.stp"}, [{"id": "0", "name": "A"}, {"id": "1", "name": "B"}], [rec])
    rows = contact_rows(run, "_clash/x/r1.json")
    assert [(r.element_key, r.data["role"], r.data["other_key"]) for r in rows] == [
        ("file:m.stp#p:A", "landing", "file:m.stp#p:B"),
        ("file:m.stp#p:B", "incoming", "file:m.stp#p:A"),
    ]
    assert rows[0].group == rows[1].group and rows[0].data["applicable"] == ["spec_a"]
