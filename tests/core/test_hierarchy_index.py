"""HierarchyIndex: identity and ancestry over a flat node list, with no I/O and no
knowledge of who produced the nodes."""

import pytest

from ada.core.hierarchy import (
    ROOT_SENTINEL,
    AmbiguousKeyError,
    HierarchyIndex,
    HierarchyNode,
)

DOMAIN = "example-domain"


def _key(name: str) -> str:
    return f"{DOMAIN}:{name}"


def _sample_nodes():
    """A four-level tree: root -> container -> sub-container -> element."""
    return [
        HierarchyNode(id=0, key=_key("/ROOT-CONTAINER"), name="/ROOT-CONTAINER", parent=ROOT_SENTINEL),
        HierarchyNode(id=1, key=_key("/SOME-CONTAINER"), name="/SOME-CONTAINER", parent=0),
        HierarchyNode(id=2, key=_key("/SOME-SUB-CONTAINER"), name="/SOME-SUB-CONTAINER", parent=1),
        HierarchyNode(id=3, key=_key("/SOME-ELEMENT-NAME"), name="/SOME-ELEMENT-NAME", parent=2, has_geometry=True),
        HierarchyNode(id=4, key=_key("/OTHER-ELEMENT-NAME"), name="/OTHER-ELEMENT-NAME", parent=2, has_geometry=True),
    ]


def _index():
    return HierarchyIndex(_sample_nodes(), key_domain=DOMAIN)


def test_find_returns_the_node_and_none_for_an_unknown_key():
    index = _index()

    assert index.find(_key("/SOME-ELEMENT-NAME")).id == 3
    assert index.find(_key("/NOT-IN-THIS-MODEL")) is None
    assert _key("/SOME-ELEMENT-NAME") in index
    assert len(index) == 5


def test_ancestors_walk_all_the_way_to_the_root_sentinel():
    index = _index()

    chain = index.ancestors(_key("/SOME-ELEMENT-NAME"))

    assert [n.name for n in chain] == ["/SOME-SUB-CONTAINER", "/SOME-CONTAINER", "/ROOT-CONTAINER"]
    assert chain[-1].is_root
    assert chain[-1].parent == ROOT_SENTINEL
    # a root has no ancestors, and neither does something absent
    assert index.ancestors(_key("/ROOT-CONTAINER")) == ()
    assert index.ancestors(_key("/NOT-IN-THIS-MODEL")) == ()


def test_a_none_parent_is_a_root_too():
    index = HierarchyIndex(
        [HierarchyNode(id=0, key=_key("/R"), parent=None), HierarchyNode(id=1, key=_key("/C"), parent=0)]
    )

    assert index.ancestors(_key("/C"))[-1].id == 0
    assert [n.id for n in index.roots()] == [0]


def test_children_are_direct_only_and_in_insertion_order():
    index = _index()

    assert [n.id for n in index.children(_key("/SOME-SUB-CONTAINER"))] == [3, 4]
    assert index.children(_key("/SOME-ELEMENT-NAME")) == ()
    assert index.children(_key("/NOT-IN-THIS-MODEL")) == ()


def test_ambiguity_is_reported_never_resolved():
    """Key uniqueness is a property of the producer, not something this structure can
    assume. Two nodes with one key must surface as ambiguous rather than silently
    binding a caller to whichever happened to be indexed first."""
    nodes = _sample_nodes()
    nodes.append(HierarchyNode(id=5, key=_key("/SOME-ELEMENT-NAME"), name="/SOME-ELEMENT-NAME", parent=1))
    index = HierarchyIndex(nodes, key_domain=DOMAIN)

    assert index.is_ambiguous(_key("/SOME-ELEMENT-NAME")) is True
    assert index.is_ambiguous(_key("/OTHER-ELEMENT-NAME")) is False
    assert index.ambiguous_keys() == (_key("/SOME-ELEMENT-NAME"),)
    assert [n.id for n in index.find_all(_key("/SOME-ELEMENT-NAME"))] == [3, 5]

    with pytest.raises(AmbiguousKeyError) as err:
        index.find(_key("/SOME-ELEMENT-NAME"))
    assert err.value.node_ids == ("3", "5")


def test_unkeyed_nodes_are_still_indexed_and_still_walkable():
    """A node with no stable identity is a normal state, not an error — it just cannot
    be looked up by key."""
    nodes = _sample_nodes()
    nodes.append(HierarchyNode(id=5, name="unnamed child", parent=3))
    index = HierarchyIndex(nodes, key_domain=DOMAIN)

    assert len(index) == 6
    assert len(index.keys()) == 5
    assert [n.id for n in index.children(_key("/SOME-ELEMENT-NAME"))] == [5]
    assert [n.id for n in index.ancestors_of_id(5)] == [3, 2, 1, 0]


def test_ids_index_the_same_whether_written_as_int_or_string():
    index = HierarchyIndex(
        [
            HierarchyNode(id="0", key=_key("/R"), parent=ROOT_SENTINEL),
            HierarchyNode(id=1, key=_key("/C"), parent="0"),
        ]
    )

    assert [n.id for n in index.ancestors(_key("/C"))] == ["0"]
    assert index.node_by_id(0).key == _key("/R")


def test_a_broken_tree_degrades_instead_of_hanging_or_raising():
    dangling = HierarchyIndex([HierarchyNode(id=1, key=_key("/C"), parent=99)])
    assert dangling.ancestors(_key("/C")) == ()

    cyclic = HierarchyIndex(
        [HierarchyNode(id=1, key=_key("/A"), parent=2), HierarchyNode(id=2, key=_key("/B"), parent=1)]
    )
    assert [n.id for n in cyclic.ancestors(_key("/A"))] == [2]


def test_duplicate_ids_are_refused():
    with pytest.raises(ValueError, match="duplicate node id"):
        HierarchyIndex([HierarchyNode(id=1, key=_key("/A")), HierarchyNode(id=1, key=_key("/B"))])


def test_serialises_to_the_sidecar_shape_and_back():
    source = {"kind": "example-producer", "content_hash": "abc123"}
    index = HierarchyIndex(_sample_nodes(), key_domain=DOMAIN, source=source)

    data = index.to_dict()

    assert data["schema_version"] == 1
    assert data["key_domain"] == DOMAIN
    assert data["source"] == source
    assert data["nodes"][3] == {
        "id": 3,
        "key": _key("/SOME-ELEMENT-NAME"),
        "name": "/SOME-ELEMENT-NAME",
        "parent": 2,
        "has_geometry": True,
    }
    assert data["key_index"][_key("/SOME-ELEMENT-NAME")] == 3

    back = HierarchyIndex.from_dict(data)

    assert back.to_dict() == data
    assert back.nodes == index.nodes
    assert [n.name for n in back.ancestors(_key("/SOME-ELEMENT-NAME"))] == [
        "/SOME-SUB-CONTAINER",
        "/SOME-CONTAINER",
        "/ROOT-CONTAINER",
    ]


def test_the_key_index_omits_ambiguous_keys_rather_than_picking_one():
    nodes = _sample_nodes()
    nodes.append(HierarchyNode(id=5, key=_key("/SOME-ELEMENT-NAME"), parent=1))
    data = HierarchyIndex(nodes, key_domain=DOMAIN).to_dict()

    assert _key("/SOME-ELEMENT-NAME") not in data["key_index"]
    assert _key("/OTHER-ELEMENT-NAME") in data["key_index"]
    # nodes remains the full truth, so the round-trip still sees both
    assert len(HierarchyIndex.from_dict(data).find_all(_key("/SOME-ELEMENT-NAME"))) == 2


def test_producer_specific_node_fields_survive_a_round_trip():
    index = HierarchyIndex([{"id": 7, "key": _key("/E"), "parent": "*", "producer_field": {"a": 1}}])

    node = index.find(_key("/E"))
    assert node.extra == {"producer_field": {"a": 1}}
    assert index.to_dict()["nodes"][0]["producer_field"] == {"a": 1}


def test_an_empty_index_is_usable():
    index = HierarchyIndex()

    assert len(index) == 0
    assert index.find(_key("/E")) is None
    assert index.is_ambiguous(_key("/E")) is False
    assert index.to_dict()["nodes"] == []
