"""A node removed from ``Nodes`` is not handed back by the next ``add`` at its position.

``add`` finds a coincident node through a spatial grid that ``remove`` did not update, so it returned the removed node
and added nothing. Meshing the user's GeniE model at 0.325 m, ``remove_standalones`` took out the geometry vertex at
the centre of each support pad, and the rigid-link master then placed there was that removed node: BNBCD and BLDEP
named node 16397, which GNODE and GCOORD (ids 1..11787) did not hold.
"""

from __future__ import annotations

import ada
from ada.api.containers import Nodes


def test_add_after_remove_puts_a_node_in_the_container():
    kept, gone = ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)
    nodes = Nodes([kept, gone])
    nodes.remove(gone)
    again = nodes.add(ada.Node((1, 0, 0)))
    assert again is not gone
    assert again in nodes.nodes
    assert nodes.get_by_volume((1, 0, 0)) == [again]


def test_remove_takes_only_the_removed_nodes_out_of_the_grid():
    """``remove`` takes each node out of its own grid cell rather than rebuilding the grid (which made a single
    remove of 20 000 nodes 72.5 ms against 16.9 ms before the fix; now 17.1 ms): the kept nodes are still found."""
    ns = [ada.Node((0.1 * i, 0, 0), i + 1) for i in range(10)]
    nodes = Nodes(ns)
    grid = nodes._grid
    nodes.remove([ns[3], ns[7]])
    assert nodes._grid is grid, "not rebuilt"
    for n in ns[:3] + ns[4:7] + ns[8:]:
        assert nodes.add(ada.Node(n.p)) is n
    assert sum(len(cell) for cell in nodes._grid.values()) == 8


def test_a_node_moved_since_it_was_added_is_still_taken_out():
    """``move`` re-sorts but leaves the grid where the nodes were; a removed node not in the cell of its position
    rebuilds the grid, so the kept nodes are found where they are now."""
    ns = [ada.Node((float(i), 0, 0), i + 1) for i in range(3)]
    nodes = Nodes(ns)
    nodes.move((0, 0, 5))
    nodes.remove(ns[1])
    assert nodes.add(ada.Node(ns[0].p)) is ns[0]
    again = nodes.add(ada.Node(ns[1].p))
    assert again is not ns[1] and again in nodes.nodes
