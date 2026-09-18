"""Usfos element node ordering.

Usfos' writer only emits first-order shells and 2-node beams, whose node ordering
is the corner cycle adapy native already uses, so nothing is permuted.
"""

from __future__ import annotations

from ada.fem.shapes.node_order import NodeOrder

USFOS_ORDER = NodeOrder("usfos")
