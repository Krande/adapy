"""Code Aster (MED) element node ordering.

MED numbers corners first and mid-side nodes after, matching adapy native, so
nothing is permuted here. MED's *storage* is column-major, which the reader
already handles with ``reshape(..., order="F")`` -- that is a memory layout, not
a node ordering, and does not belong in this table.
"""

from __future__ import annotations

from ada.fem.shapes.node_order import NodeOrder

CODE_ASTER_ORDER = NodeOrder("code_aster")
