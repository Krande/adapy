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
