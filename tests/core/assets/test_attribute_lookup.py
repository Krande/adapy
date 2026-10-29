"""The attribute lookup a concepts read is handed: its answer, and how core passes it on."""

from types import SimpleNamespace

import pytest

from ada.assets.attribute_lookup import (
    CollectionAttributes,
    NodeAttributes,
    StaticAttributeLookup,
    accepts_attributes,
    merge_provider_rows,
)
from ada.assets.concepts import (
    clear_asset_concepts,
    part_for_manifest,
    register_asset_concepts,
)

ROWS = [
    {"node_id": "s", "full_name": "/S", "kind": "SITE", "attrs": {"TYPE": "SITE"}},
    {"node_id": "o", "parent_id": "s", "full_name": "/O", "kind": "OWNER", "attrs": {"TYPE": "OWNER", "X": 1}},
    {"node_id": "b", "parent_id": "o", "full_name": "/O/B", "kind": "RUN", "attrs": {"TYPE": "RUN"}},
    {"node_id": "c1", "parent_id": "b", "full_name": "C 1 of /O/B", "kind": "ELEM", "attrs": {"TYPE": "ELEM", "X": 2}},
    {"node_id": "c2", "parent_id": "b", "full_name": "C 2 of /O/B", "kind": "ELEM", "attrs": {"TYPE": "ELEM"}},
]


def test_one_lookup_answers_names_ids_keys_and_ancestor_chains():
    lookup = StaticAttributeLookup({"Col": ROWS})
    answer = lookup.lookup("col", node_ids=["c2"], full_names=["C 1 of /O/B", "missing"], keys=["TYPE"], ancestors=2)
    assert set(answer.nodes) == {"c1", "c2"}
    assert answer.nodes["c1"].attrs == {"TYPE": "ELEM"}
    assert set(answer.ancestors) == {"b", "o"}, "two levels, each ancestor once"
    c1 = answer.by_full_name()["C 1 of /O/B"]
    assert [a.attrs["TYPE"] for a in answer.chain(c1)] == ["RUN", "OWNER"]
    assert len(lookup.calls) == 1
    assert lookup.lookup("other", node_ids=["c1"]) == NodeAttributes()


def test_answers_merge_and_read_from_the_wire_shape():
    a = NodeAttributes.from_dict({"indexed": True, "nodes": [ROWS[3]], "ancestors": {"b": ROWS[2]}})
    b = NodeAttributes.from_dict({"indexed": False, "nodes": [ROWS[4]], "ancestors": [ROWS[1]]})
    merged = a.merged(b)
    assert merged.indexed and set(merged.nodes) == {"c1", "c2"} and set(merged.ancestors) == {"b", "o"}


class _Reader:
    def __init__(self):
        self.kwargs = None

    def concepts(self, options, *, storage, scope=None, node=None, attributes=None):
        self.kwargs = {"attributes": attributes}
        return object()


class _OldReader:
    def concepts(self, options, *, storage, scope=None, node=None):
        return object()


@pytest.fixture
def registry():
    clear_asset_concepts()
    yield
    clear_asset_concepts()


def _manifest(provider):
    return SimpleNamespace(
        provider=provider, collection="col", subject="s", node=None, build=SimpleNamespace(options={})
    )


def test_core_hands_the_lookup_bound_to_the_collection_to_a_reader_that_takes_it(registry):
    reader = _Reader()
    register_asset_concepts("new", lambda: reader)
    register_asset_concepts("old", lambda: _OldReader())
    lookup = StaticAttributeLookup({"col": ROWS})

    part_for_manifest(_manifest("new"), storage=None, attributes=lookup)
    bound = reader.kwargs["attributes"]
    assert isinstance(bound, CollectionAttributes) and bound.collection == "col"
    assert set(bound.lookup(node_ids=["c1"]).nodes) == {"c1"}

    # A reader that does not take it is called exactly as before.
    assert part_for_manifest(_manifest("old"), storage=None, attributes=lookup) is not None
    reader.kwargs = None
    part_for_manifest(_manifest("new"), storage=None)
    assert reader.kwargs == {"attributes": None}


def test_one_nodes_rows_from_two_providers_merge_the_most_complete_leading():
    rich = {"node_id": "n", "parent_id": "p", "full_name": "/N", "kind": "ELEM", "provider": "zeta",
            "attrs": {"TYPE": "ELEM", "A": "rich", "B": "1", "C": "2"}}  # fmt: skip
    thin = {"node_id": "n", "parent_id": None, "full_name": "/N", "kind": None, "provider": "alpha",
            "attrs": {"A": "thin", "D": "only-here"}}  # fmt: skip
    merged = merge_provider_rows([thin, rich])
    assert merged["provider"] == "zeta" and merged["providers"] == ["zeta", "alpha"]
    assert merged["attrs"] == {"TYPE": "ELEM", "A": "rich", "B": "1", "C": "2", "D": "only-here"}
    assert merged["parent_id"] == "p" and merged["kind"] == "ELEM"

    # The count given wins over the keys carried (a key-filtered row), and a tie goes by provider name.
    assert merge_provider_rows([thin, rich], sizes=[9, 4])["attrs"]["A"] == "thin"
    tie = merge_provider_rows([{**rich, "provider": "b"}, {**thin, "provider": "a"}], sizes=[3, 3])
    assert tie["provider"] == "a" and tie["attrs"]["A"] == "thin"
    with pytest.raises(ValueError):
        merge_provider_rows([])


def test_the_static_lookup_answers_a_node_two_providers_indexed_once_merged():
    other = [
        # The same node with a key the first provider lacks, and a node the first provider lacks.
        {"node_id": "c2", "parent_id": "b", "full_name": "C 2 of /O/B", "provider": "q", "attrs": {"SPEC": "/X"}},
        {"node_id": "c3", "parent_id": "b", "full_name": "C 3 of /O/B", "provider": "q", "attrs": {"TYPE": "ELEM"}},
    ]
    lookup = StaticAttributeLookup({"col": [{**r, "provider": "p"} for r in ROWS] + other})
    answer = lookup.lookup("col", full_names=["C 2 of /O/B", "C 3 of /O/B"], ancestors=1)
    assert answer.nodes["c2"].attrs == {"TYPE": "ELEM", "SPEC": "/X"}
    assert answer.nodes["c3"].provider == "q"
    assert [a.attrs["TYPE"] for a in answer.chain(answer.nodes["c3"])] == ["RUN"]


def test_accepts_attributes():
    assert accepts_attributes(_Reader().concepts)
    assert not accepts_attributes(_OldReader().concepts)
    assert accepts_attributes(lambda **kw: None)


def test_the_api_lookup_chunks_one_request_per_chunk(monkeypatch):
    # The worker package imports the database layer; the core test env has no server deps.
    pytest.importorskip("asyncpg")
    from ada.comms.rest.worker.attribute_lookup import RestAttributeLookup

    sent = []

    class _Scope:
        def wire(self):
            return "shared"

    lookup = RestAttributeLookup("http://api", "token", _Scope(), chunk=2)

    def request(url, body):
        sent.append((url, body))
        ids = set(body["node_ids"]) | set(body["full_names"])
        return {"indexed": True, "nodes": [r for r in ROWS if r["node_id"] in ids or r["full_name"] in ids]}

    monkeypatch.setattr(lookup._http, "_request", request)
    answer = lookup.lookup("col", node_ids=["s", "o", "b"], full_names=["C 1 of /O/B"], keys=["TYPE"], ancestors=1)
    assert len(sent) == 2 and all(u == "http://api/api/scopes/shared/assets/search/nodes" for u, _ in sent)
    assert all(b["keys"] == ["TYPE"] and b["ancestors"] == 1 for _, b in sent)
    assert set(answer.nodes) == {"s", "o", "b", "c1"}
