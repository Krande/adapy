"""Code Aster (MED) element node ordering.

MED numbers corners first and mid-side nodes after. For the solids and shells that
is exactly adapy native, including the edge each mid-side node sits on, so they are
not listed here.

``SEG3`` is the exception: MED orders it ``(end, end, mid)`` where native is
``(end, mid, end)``. Without the permutation below a Code Aster 3-node beam reads
back with its mid node treated as an end.

Both facts come from MEDCoupling rather than from assumption: converting each
quadratic cell to linear reports which slots are corners, and exploding it into
edges reports the ``(corner, corner, mid)`` triple per edge.

MED's *storage* is column-major, which the reader handles with
``reshape(..., order="F")`` -- that is a memory layout, not a node ordering, and
does not belong in this table.
"""

from __future__ import annotations

from ada.fem.shapes.definitions import LineShapes
from ada.fem.shapes.node_order import NodeOrder

CODE_ASTER_ORDER = NodeOrder(
    "code_aster",
    {
        # native (end, mid, end) -> MED (end, end, mid)
        LineShapes.LINE3: (0, 2, 1),
    },
)
