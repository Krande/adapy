"""Calculix element node ordering.

Calculix takes Abaqus' element definitions, including their node ordering, so it
matches adapy native and permutes nothing.
"""

from __future__ import annotations

from ada.fem.shapes.node_order import NodeOrder

CALCULIX_ORDER = NodeOrder("calculix")
