# GMSH Node Ordering
# """
#         Hexahedron:             Hexahedron20:          Hexahedron27:
#
#        v
# 3----------2            3----13----2           3----13----2
# |\     ^   |\           |\         |\          |\         |\
# | \    |   | \          | 15       | 14        |15    24  | 14
# |  \   |   |  \         9  \       11 \        9  \ 20    11 \
# |   7------+---6        |   7----19+---6       |   7----19+---6
# |   |  +-- |-- | -> u   |   |      |   |       |22 |  26  | 23|
# 0---+---\--1   |        0---+-8----1   |       0---+-8----1   |
#  \  |    \  \  |         \  17      \  18       \ 17    25 \  18
#   \ |     \  \ |         10 |        12|        10 |  21    12|
#    \|      w  \|           \|         \|          \|         \|
#     4----------5            4----16----5           4----16----5
#         Tetrahedron:                          Tetrahedron10:
#
#                    v
#                  .
#                ,/
#               /
#            2                                     2
#          ,/|`\                                 ,/|`\
#        ,/  |  `\                             ,/  |  `\
#      ,/    '.   `\                         ,6    '.   `5
#    ,/       |     `\                     ,/       8     `\
#  ,/         |       `\                 ,/         |       `\
# 0-----------'.--------1 --> u         0--------4--'.--------1
#  `\.         |      ,/                 `\.         |      ,/
#     `\.      |    ,/                      `\.      |    ,9
#        `\.   '. ,/                           `7.   '. ,/
#           `\. |/                                `\. |/
#              `3                                    `3
#                 `\.
#                    ` w
# """
# tet10 is modified from GMSH to abaqus. See gmsh_to_meshio_ordering for complete overview

from ada.fem.shapes.definitions import SolidShapes

# 12 physical edges of a hex; only corner nodes, so the same list
# works for HEX8 / HEX20 / HEX27. Going through mid-side nodes
# (HEX20's 24-segment trace) drew two visible segments per physical
# edge whenever the midnode bowed out or sat slightly off-midpoint,
# which looked like "extra lines on every O2 element" in the viewer.
# Straight corner-to-corner matches the shell-O2 treatment in
# shells.py.
_HEX_CORNER_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 0),  # bottom face
    (4, 5), (5, 6), (6, 7), (7, 4),  # top face
    (0, 4), (1, 5), (2, 6), (3, 7),  # vertical
]

# 9 physical edges of a wedge — 3 bottom triangle, 3 top triangle,
# 3 vertical. Reused for WEDGE15.
_WEDGE_CORNER_EDGES = [
    (0, 1), (1, 2), (2, 0),  # bottom triangle
    (3, 4), (4, 5), (5, 3),  # top triangle
    (0, 3), (1, 4), (2, 5),  # vertical
]

# 6 physical edges of a tet — reused for TETRA10.
_TETRA_CORNER_EDGES = [(0, 1), (1, 3), (3, 0), (0, 2), (2, 3), (1, 2)]

# 8 physical edges of a square pyramid — reused for PYRAMID13.
_PYRAMID_CORNER_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4)]

solid_edges = {
    SolidShapes.HEX8: _HEX_CORNER_EDGES,
    SolidShapes.HEX20: _HEX_CORNER_EDGES,
    SolidShapes.HEX27: _HEX_CORNER_EDGES,
    SolidShapes.TETRA: _TETRA_CORNER_EDGES,
    SolidShapes.TETRA10: _TETRA_CORNER_EDGES,
    SolidShapes.PYRAMID5: _PYRAMID_CORNER_EDGES,
    SolidShapes.PYRAMID13: _PYRAMID_CORNER_EDGES,
    SolidShapes.WEDGE: _WEDGE_CORNER_EDGES,
    SolidShapes.WEDGE15: _WEDGE_CORNER_EDGES,
}

# Higher-order solids reuse their first-order corner topology for
# faces. Mid-side and centre nodes are ignored for visualization —
# the bake's straight-edge wireframe + corner-triangulated faces
# match what users expect to see (and what the underlying first-
# order viz mesh shows). Without WEDGE15 / PYRAMID13 / PYRAMID5
# entries, any of those elements crashed ``get_faces()`` or were
# silently dropped — one missing wedge at a hex/tet transition
# corner was enough to leave a hole at the top flange in the
# audit-#5256 CalculiX O2 mesh.
_HEX_CORNER_FACES = [
    [0, 1, 2, 3],
    [4, 5, 6, 7],
    [0, 1, 5, 4],
    [1, 2, 6, 5],
    [2, 3, 7, 6],
    [0, 3, 7, 4],
]
_TETRA_CORNER_FACES = [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]
_WEDGE_CORNER_FACES = [(0, 1, 2), (0, 2, 5), (0, 5, 3), (3, 4, 5), (0, 1, 4), (0, 4, 3)]
# Square-base pyramid: one quad base + four triangular sides.
_PYRAMID_CORNER_FACES = [
    [0, 1, 2, 3],
    (0, 1, 4),
    (1, 2, 4),
    (2, 3, 4),
    (3, 0, 4),
]

solid_faces = {
    SolidShapes.HEX8: _HEX_CORNER_FACES,
    SolidShapes.HEX20: _HEX_CORNER_FACES,
    SolidShapes.HEX27: _HEX_CORNER_FACES,
    SolidShapes.TETRA: _TETRA_CORNER_FACES,
    SolidShapes.TETRA10: _TETRA_CORNER_FACES,
    SolidShapes.PYRAMID5: _PYRAMID_CORNER_FACES,
    SolidShapes.PYRAMID13: _PYRAMID_CORNER_FACES,
    SolidShapes.WEDGE: _WEDGE_CORNER_FACES,
    SolidShapes.WEDGE15: _WEDGE_CORNER_FACES,
}
