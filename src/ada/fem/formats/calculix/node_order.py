"""Calculix element node ordering.

Calculix takes Abaqus' element definitions, including their node ordering, so it
matches adapy native. Its B32 was the element used to establish that native ``LINE3``
is ``(end, mid, end)`` -- see :mod:`ada.fem.formats.abaqus.node_order`.
"""

from __future__ import annotations

from ada.fem.shapes.node_order import NodeOrder

CALCULIX_ORDER = NodeOrder("calculix")
