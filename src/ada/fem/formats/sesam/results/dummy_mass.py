"""GeniE's dummy-mass beams, found by their shape.

GeniE models an equipment mass as a ``BNMASS`` nodal mass at the equipment's centre
of gravity, and hangs that node off the deck with a beam from each footprint node.
Each beam is a short vertical stub (an eccentricity at the COG end moves its element
end from the COG node to just above or below its footprint node: SIF 7.3.10, the
vector points from the node to the element end), and the offset is a rigid link. The
beams carry a section and a material like any other (``Dummy_EQbox``, a density of
~0), so nothing on the deck says what they are -- only the arrangement does.

The arrangement is distinctive: a node that

* every element touching it is a beam, each with an eccentricity at that end, and
* those eccentric ends all land in DIFFERENT places.

A real eccentric joint is the opposite case: its members are offset to a common
point (the centre line of the member they frame into), so their ends coincide. And
the COG node is touched by nothing but its dummy beams, so a shell or an
un-offset beam at the node rules it out.

At least three beams, because two beams offset to two different points from one
node is also a beam bent round a corner on eccentric ends; GeniE's dummies come in
footprints of three and up. On the deck this was written for (19 983 dummies in 292
footprints over three superelements) the rule picked no other beam, and missed six
dummies on footprints of fewer than three.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

__all__ = ["dummy_mass_elements"]

#: Fewest beams a footprint has before its node is taken for a dummy-mass COG.
MIN_FOOTPRINT = 3

# Two offset ends closer than this (model length units) are the same point; a
# vanishing offset is no offset.
_TOL = 1e-3


def dummy_mass_elements(
    elements: list[tuple[int, bool, list[int]]],
    coords: dict[int, np.ndarray],
    eccentricities: dict[int, list] | None,
) -> list[int]:
    """Ids of the beams that hang a nodal mass off the deck, in ``elements`` order.

    ``elements``: ``(id, is_line, node ids)`` for every element. ``coords``: node id ->
    position. ``eccentricities``: ``{id: [per-node global offset | None]}``, the mesh's
    own (see :mod:`.eccentricity`).
    """
    if not eccentricities:
        return []

    touched = defaultdict(int)
    ends: dict[int, list[tuple[int, np.ndarray]]] = defaultdict(list)
    for elem_id, is_line, nodes in elements:
        for node in nodes:
            touched[node] += 1
        ecc = eccentricities.get(elem_id) if is_line else None
        if not ecc:
            continue
        for at, offset in ((0, ecc[0]), (-1, ecc[-1])):
            if offset is None:
                continue
            offset = np.asarray(offset, dtype=float)
            if float(np.linalg.norm(offset)) < _TOL:
                continue
            node = nodes[at]
            if node in coords:
                ends[node].append((elem_id, coords[node] + offset))

    members: set[int] = set()
    for node, hung in ends.items():
        if len(hung) < MIN_FOOTPRINT or len(hung) != touched[node]:
            continue
        points = {tuple(np.round(p / _TOL).astype(np.int64)) for _, p in hung}
        if len(points) == len(hung):
            members.update(elem_id for elem_id, _ in hung)

    return [elem_id for elem_id, _, _ in elements if elem_id in members]
