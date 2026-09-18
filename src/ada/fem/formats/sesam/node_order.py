"""Sesam's element node ordering.

The iso-parametric solids (IHEX, IPRI, ITET) and the quadrilateral shell (SCQS)
number their nodes **interleaved** -- a corner, then the mid-side node following
it, all the way round -- where adapy native puts every corner first and the
mid-side nodes after. Figures 5-15 (IHEX), 5-27 (SCQS), 5-29 (IPRI) and 5-31
(ITET) of the Sesam file description are the source for the tables below.

Writing an Abaqus-ordered tetrahedron straight into a GELMNT1 record without
this is what makes a converted deck's solids come out visibly distorted: the
corner and mid-side nodes land in each other's slots.

Note the triangular shell does **not** interleave -- SCTS (figure 5-25) is
corners 1-3 then mid-sides 4-6, matching native -- so it is deliberately absent
here rather than forgotten.
"""

from __future__ import annotations

from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes
from ada.fem.shapes.node_order import NodeOrder

# Read as "native index for each Sesam slot". Native ordering is corners first
# then one mid-side per edge, in NATIVE_MIDSIDE_EDGES order.
SESAM_ORDER = NodeOrder(
    "sesam",
    {
        # BTSS (23), the 3-node curved beam: Sesam writes (end, end, mid) where
        # native is (end, mid, end). The SIF results reader already normalised this
        # on its own path; declaring it here makes the input-deck reader and writer
        # agree with it instead of contradicting it.
        LineShapes.LINE3: (0, 2, 1),
        # ITET (31), figure 5-31: corners at 1, 3, 5 and the apex at 10;
        # mid-sides 2, 4, 6 round the base, then 7, 8, 9 up to the apex.
        SolidShapes.TETRA10: (0, 4, 1, 5, 2, 6, 7, 8, 9, 3),
        # IPRI (30), figure 5-29: corners 1, 3, 5 / 10, 12, 14 with the base and
        # top mid-sides interleaved, and the three verticals at 7, 8, 9.
        SolidShapes.WEDGE15: (0, 6, 1, 7, 2, 8, 12, 13, 14, 3, 9, 4, 10, 5, 11),
        # IHEX (20), figure 5-15: corners 1, 3, 5, 7 / 13, 15, 17, 19 with the
        # face mid-sides interleaved, and the four verticals at 9-12.
        SolidShapes.HEX20: (0, 8, 1, 9, 2, 10, 3, 11, 16, 17, 18, 19, 4, 12, 5, 13, 6, 14, 7, 15),
        # SCQS (28), figure 5-27: the eight nodes run round the perimeter, so
        # corners land on 1, 3, 5, 7 and mid-sides on 2, 4, 6, 8.
        ShellShapes.QUAD8: (0, 4, 1, 5, 2, 6, 3, 7),
    },
)
