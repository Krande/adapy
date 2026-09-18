"""Abaqus' element node ordering.

Abaqus numbers corners first and mid-side nodes after, in the same edge order
adapy native uses -- C3D10, C3D20, C3D15, S8R and friends all land unpermuted,
which is why this table is empty rather than missing.

Known gap: the 3-node beam (B32) is ``(end1, end2, mid)`` in Abaqus while native
LINE3 is ``(end1, mid, end2)``. That one is deliberately left out until there is
a fixture to pin it -- adding it here would silently change how existing beam
decks read, and the Sesam SIF results reader already normalises LINE3 on its own
path.
"""

from __future__ import annotations

from ada.fem.shapes.node_order import NodeOrder

ABAQUS_ORDER = NodeOrder("abaqus")
