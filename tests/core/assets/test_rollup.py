"""``ada.assets.rollup`` -- the pure half of the geometry roll-up (the route is in
tests/comms/rest/test_asset_geometry_rollup.py)."""

import itertools

from ada.assets.index import fold_listing
from ada.assets.rollup import TreeDocument, TreePlacement, plan_rollup, rollup_body

C = "coll"


def _keys(*triples):
    return [f"assets/{C}/{s}/{r}/{f}" for s, r, files in triples for f in files]


def test_plan_picks_newest_content_per_provider_and_the_tree_documents():
    subjects = fold_listing(
        _keys(
            (C, "20260101T000000Z", ["asset.json", "hierarchy.json"]),
            (C, "20260102T000000Z", ["asset.json", "hierarchy.json"]),
            (C, "20260103T000000Z", ["hierarchy.json"]),  # half-written: no manifest
            ("s1", "20260101T000000Z", ["asset.json", "hierarchy.json"]),
            ("s1", "20260102T000000Z", ["asset.json"]),
            ("s2", "20260101T000000Z", ["asset.json"]),
            ("s2", "20260102T000000Z", ["asset.json"]),
        )
    ).subjects(C)
    manifests = {
        (C, "20260101T000000Z"): ("tree-maker", "none"),
        (C, "20260102T000000Z"): ("tree-maker", "none"),
        ("s1", "20260101T000000Z"): ("mesh-only", "mesh"),
        ("s1", "20260102T000000Z"): ("member-reader", "none"),
        # s2: the provider's newest content revision is a build; an older mesh does not matter.
        ("s2", "20260101T000000Z"): ("mesh-only", "mesh"),
        ("s2", "20260102T000000Z"): ("mesh-only", "build"),
    }
    plan = plan_rollup(C, subjects, manifests)
    assert plan.here == {"mesh-only": frozenset({"s1", "s2"})}
    assert [d.revision for d in plan.index_documents] == ["20260101T000000Z", "20260102T000000Z"]
    # s1's newest complete revision carries no spine, so the browser opens none for it.
    assert plan.spines == {}


def test_placement_is_independent_of_reading_order():
    docs = [
        (TreeDocument(C, "20260101T000000Z"), [("top", None), ("site", "top")]),
        (TreeDocument("site", "20260102T000000Z"), [("site", None), ("zone", "site"), ("loose", None)]),
        (TreeDocument("zone", "20260103T000000Z"), [("zone", None), ("member", "zone")]),
    ]
    answers = set()
    for order in itertools.permutations(docs):
        p = TreePlacement(C)
        for doc, edges in order:
            p.add(doc, edges)
        answers.add((tuple(p.ancestors("member")), tuple(p.ancestors("loose"))))
    # A spine's other parentless rows sit under its subject.
    assert answers == {(("zone", "site", "top"), ("site", "top"))}


def test_unplaced_dangling_and_cycles():
    p = TreePlacement(C)
    p.add(TreeDocument(C, "20260101T000000Z"), [("a", "not-a-row"), ("b", None)])
    p.add(TreeDocument("float", "20260102T000000Z"), [("float", None), ("child", "float")])
    p.add(TreeDocument("x", "20260102T000000Z"), [("x", "y"), ("y", "x")])
    assert p.ancestors("a") == []  # the index's dangling parent: drawn as a root, so placed
    assert p.ancestors("b") == []
    assert p.ancestors("float") is None
    assert p.ancestors("child") is None
    assert p.ancestors("x") is None
    assert p.ancestors("nowhere") is None


def test_body_lists_unplaced_and_skips_the_collection_itself():
    subjects = fold_listing(
        _keys((C, "20260101T000000Z", ["asset.json"]), ("s", "20260101T000000Z", ["asset.json"]))
    ).subjects(C)
    plan = plan_rollup(C, subjects, {(C, "20260101T000000Z"): ("p", "mesh"), ("s", "20260101T000000Z"): ("p", "build")})
    body = rollup_body(plan, TreePlacement(C), index_token="t")
    assert body["providers"]["p"] == {"here": [C, "s"], "below": [], "unplaced": ["s"]}
    assert body["any"]["unplaced"] == ["s"]


def test_an_empty_build_is_the_providers_newest_word_not_a_step_aside():
    """A build whose manifest counts no leaf can only fail. It must not mark geometry -- and must not
    let an OLDER build of the same provider mark it either, the way a tree-only "none" does."""
    from types import SimpleNamespace

    from ada.assets.rollup import EMPTY_DELIVERY, rollup_delivery

    def m(delivery, **counts):
        return SimpleNamespace(delivery=delivery, counts=counts)

    assert rollup_delivery(m("build", leaves=0)) == EMPTY_DELIVERY
    assert rollup_delivery(m("build", leaves=3)) == "build"
    assert rollup_delivery(m("build")) == "build", "no count is no claim about leaves"
    assert rollup_delivery(m("none", leaves=0)) == "none"

    subjects = fold_listing(
        _keys(
            ("s1", "20260101T000000Z", ["asset.json"]),
            ("s1", "20260102T000000Z", ["asset.json"]),
        )
    ).subjects(C)
    manifests = {
        ("s1", "20260101T000000Z"): ("reader", "build"),
        ("s1", "20260102T000000Z"): ("reader", EMPTY_DELIVERY),
    }
    assert plan_rollup(C, subjects, manifests).here == {}
