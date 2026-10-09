"""`Nodes.merge_coincident` / `ArrayNodes.merge_coincident` join two meshes at their shared nodes.

Both imported `replace_node` from a module that does not define it, so the first duplicate raised
ImportError; nothing exercised them until mesh overrides merged separately-meshed objects.
"""

import pytest

import ada


def _two_strips(array_backed: bool):
    """Two 1 x 1 plates sharing the edge x=1, each meshed alone (25 nodes each), added together."""
    left = ada.Plate("left", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    right = ada.Plate("right", [(1, 0), (2, 0), (2, 1), (1, 1)], 0.01)
    p = ada.Part("p") / [left, right]

    fem = left.to_fem_obj(0.25, "shell", use_quads=True)
    fem.parent = p
    other = right.to_fem_obj(0.25, "shell", use_quads=True)
    other.parent = p
    fem += other
    if array_backed:
        from ada.api.mesh.containers import to_array_backed

        to_array_backed(fem)
    return fem, left, right


@pytest.mark.parametrize("array_backed", [False, True], ids=["object", "array"])
def test_merge_coincident_joins_the_shared_edge(array_backed):
    fem, _, _ = _two_strips(array_backed)
    assert len(fem.nodes) == 50

    fem.nodes.merge_coincident()

    assert len(fem.nodes) == 45
    assert len(fem.elements) == 32


@pytest.mark.parametrize("array_backed", [False, True], ids=["object", "array"])
def test_after_merging_the_shared_nodes_belong_to_both_plates(array_backed):
    fem, left, right = _two_strips(array_backed)
    fem.nodes.merge_coincident()

    node_ids = {n.id for n in fem.nodes}
    edge = [n for n in fem.nodes if abs(n.p[0] - 1.0) < 1e-6]
    assert len(edge) == 5
    for n in edge:
        # Used by elements on both sides of the shared edge, i.e. by both plates' meshes.
        sides = {
            "left" if sum(m.p[0] for m in el.nodes) / len(el.nodes) < 1.0 else "right"
            for el in fem.elements
            if n.id in {m.id for m in el.nodes}
        }
        assert sides == {"left", "right"}
    # No element points at a node that was merged away.
    assert all(m.id in node_ids for el in fem.elements for m in el.nodes)
