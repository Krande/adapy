"""Abaqus' element node ordering.

Abaqus numbers corners first and mid-side nodes after, in the same edge order adapy
native uses, so C3D10 / C3D20 / C3D15 / S8R and friends land unpermuted -- which is
why this table is empty rather than missing.

``B32`` is the one worth stating explicitly: it is ``(end, mid, end)``, the same as
native. Checked by running a 4 m cantilever of B32 elements through CalculiX both
ways -- ``(end, mid, end)`` gives a tip deflection in line with ``PL^3/3EI``, while
``(end, end, mid)`` comes out ~4.6x too stiff.
"""

from __future__ import annotations

from ada.fem.shapes.node_order import NodeOrder

ABAQUS_ORDER = NodeOrder("abaqus")
