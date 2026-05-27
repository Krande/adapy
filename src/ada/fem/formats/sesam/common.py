from __future__ import annotations

from ada.fem.shapes.definitions import (
    LineShapes,
    MassTypes,
    ShellShapes,
    SolidShapes,
    SpringTypes,
)

# ELTYP code → ada-side shape mapping. Codes follow Table 5.1/5.2 of
# the DNV-GL "SESAM Input Interface File Description" (file version
# 10.0). Adding a new element type means landing one entry here; the
# writer (``eltype_2_sesam`` in write_elements.py) reverse-iterates
# this dict so a one-line extension lights up both directions.
#
# Solid 3-D elements:
#   20  IHEX  Iso-parametric hexahedron      (20 nodes, HEX20)
#   21  LHEX  Linear hexahedron              ( 8 nodes, HEX8)
#   30  IPRI  Iso-parametric prism           (15 nodes, WEDGE15)
#   31  ITET  Iso-parametric tetrahedron     (10 nodes, TETRA10)
#   32  TPRI  Triangular prism               ( 6 nodes, WEDGE)
#   33  TETR  Tetrahedron                    ( 4 nodes, TETRA)
#
# Node-ordering caveat: Sesam's IHEX uses an interleaved
# corner/mid-edge local numbering scheme (per Figure 5-15 in the
# spec) — corners at local indices 1, 3, 5, 7 / 13, 15, 17, 19 with
# mid-edges interleaved. MED and Abaqus use the simpler "8 corners
# then 12 mid-edges" scheme. The mapping below only fixes the
# element-type CODE; cross-format HEX20 decks may still need a node
# permutation before they're geometrically correct in Sestra. Same
# concern applies to IPRI / WEDGE15 to a lesser degree (corners
# then mid-edges vs. interleaved); LHEX / TETR / TPRI are
# simpler-shape elements where the orderings agree.
sesam_el_map = {
    15: LineShapes.LINE,
    2: LineShapes.LINE,
    23: LineShapes.LINE3,
    24: ShellShapes.QUAD,
    25: ShellShapes.TRI,
    26: ShellShapes.TRI6,
    28: ShellShapes.QUAD8,
    20: SolidShapes.HEX20,
    21: SolidShapes.HEX8,
    30: SolidShapes.WEDGE15,
    31: SolidShapes.TETRA10,
    32: SolidShapes.WEDGE,
    33: SolidShapes.TETRA,
    40: SpringTypes.SPRING2,
    18: SpringTypes.SPRING1,
    11: MassTypes.MASS,
}

sesam_reverse = {value: key for key, value in sesam_el_map.items()}


def sesam_eltype_2_general(eltyp: int) -> LineShapes | ShellShapes | SolidShapes | SpringTypes | MassTypes:
    """Converts the numeric definition of elements in Sesam to a generalized element type form (ie. B31, S4, etc..)"""
    res = sesam_el_map.get(eltyp, None)
    if res is None:
        raise Exception("Currently unsupported eltype", eltyp)
    return res
