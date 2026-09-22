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
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 0),  # bottom face
    (4, 5),
    (5, 6),
    (6, 7),
    (7, 4),  # top face
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),  # vertical
]

# 9 physical edges of a wedge — 3 bottom triangle, 3 top triangle,
# 3 vertical. Reused for WEDGE15.
_WEDGE_CORNER_EDGES = [
    (0, 1),
    (1, 2),
    (2, 0),  # bottom triangle
    (3, 4),
    (4, 5),
    (5, 3),  # top triangle
    (0, 3),
    (1, 4),
    (2, 5),  # vertical
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


# ---------------------------------------------------------------------------
# Abaqus element faces, mid-side nodes included.
#
# The ``solid_faces`` tables above are *visualization* topology: corner nodes
# only, and for the wedge the quad faces are already split into triangles, so
# the list is longer than the element's face count. Neither is usable for
# resolving ``*Surface, type=ELEMENT`` -- a surface names one physical face
# (``S3``) and needs exactly the nodes on it, mid-side nodes and all.
#
# Ordering authority: Abaqus Analysis User's Guide, "Three-dimensional solid
# element library" -> the "Element faces" list published for each family
# (C3D4/C3D10, C3D8/C3D20, C3D6/C3D15), read against the node numbering in the
# same section. adapy's native node ordering *is* Abaqus' for these shapes
# (see ``node_order.NATIVE_MIDSIDE_EDGES``), so the published 1-based numbers
# translate to these slots by subtracting one. Within each face the nodes are
# listed the way Abaqus lists them -- corners in face-winding order, then the
# mid-side node of each of that face's edges in the same order -- so the tuples
# double as a statement of the mid-side convention.
#
# Indexed 0-based: entry ``i`` is Abaqus face ``S(i+1)``.

# C3D4 / C3D10 faces: S1 = 1-2-3, S2 = 1-4-2, S3 = 2-4-3, S4 = 3-4-1.
_TETRA_ABAQUS_FACES = (
    (0, 1, 2),
    (0, 3, 1),
    (1, 3, 2),
    (2, 3, 0),
)
# C3D10 adds the mid-side node of each face edge: node 5 = edge 1-2, 6 = 2-3,
# 7 = 1-3, 8 = 1-4, 9 = 2-4, 10 = 3-4 (slots 4..9).
_TETRA10_ABAQUS_FACES = (
    (0, 1, 2, 4, 5, 6),  # S1: edges 1-2, 2-3, 3-1
    (0, 3, 1, 7, 8, 4),  # S2: edges 1-4, 4-2, 2-1
    (1, 3, 2, 8, 9, 5),  # S3: edges 2-4, 4-3, 3-2
    (2, 3, 0, 9, 7, 6),  # S4: edges 3-4, 4-1, 1-3
)

# C3D8 / C3D20 faces: S1 = 1-2-3-4, S2 = 5-8-7-6, S3 = 1-5-6-2,
# S4 = 2-6-7-3, S5 = 3-7-8-4, S6 = 4-8-5-1.
_HEX_ABAQUS_FACES = (
    (0, 1, 2, 3),
    (4, 7, 6, 5),
    (0, 4, 5, 1),
    (1, 5, 6, 2),
    (2, 6, 7, 3),
    (3, 7, 4, 0),
)
# C3D20 mid-side nodes: 9..12 on the 1-2-3-4 face edges, 13..16 on the
# 5-6-7-8 face edges, 17..20 on the verticals 1-5, 2-6, 3-7, 4-8
# (slots 8..19).
_HEX20_ABAQUS_FACES = (
    (0, 1, 2, 3, 8, 9, 10, 11),  # S1: edges 1-2, 2-3, 3-4, 4-1
    (4, 7, 6, 5, 15, 14, 13, 12),  # S2: edges 5-8, 8-7, 7-6, 6-5
    (0, 4, 5, 1, 16, 12, 17, 8),  # S3: edges 1-5, 5-6, 6-2, 2-1
    (1, 5, 6, 2, 17, 13, 18, 9),  # S4: edges 2-6, 6-7, 7-3, 3-2
    (2, 6, 7, 3, 18, 14, 19, 10),  # S5: edges 3-7, 7-8, 8-4, 4-3
    (3, 7, 4, 0, 19, 15, 16, 11),  # S6: edges 4-8, 8-5, 5-1, 1-4
)

# C3D6 / C3D15 faces: S1 = 1-2-3, S2 = 4-5-6, S3 = 1-2-5-4,
# S4 = 2-3-6-5, S5 = 3-1-4-6. Note only five faces -- the viz table above
# has six entries because it triangulates the three quads.
_WEDGE_ABAQUS_FACES = (
    (0, 1, 2),
    (3, 4, 5),
    (0, 1, 4, 3),
    (1, 2, 5, 4),
    (2, 0, 3, 5),
)
# C3D15 mid-side nodes: 7..9 on the 1-2-3 face edges, 10..12 on the 4-5-6
# face edges, 13..15 on the verticals 1-4, 2-5, 3-6 (slots 6..14).
_WEDGE15_ABAQUS_FACES = (
    (0, 1, 2, 6, 7, 8),  # S1: edges 1-2, 2-3, 3-1
    (3, 4, 5, 9, 10, 11),  # S2: edges 4-5, 5-6, 6-4
    (0, 1, 4, 3, 6, 13, 9, 12),  # S3: edges 1-2, 2-5, 5-4, 4-1
    (1, 2, 5, 4, 7, 14, 10, 13),  # S4: edges 2-3, 3-6, 6-5, 5-2
    (2, 0, 3, 5, 8, 12, 11, 14),  # S5: edges 3-1, 1-4, 4-6, 6-3
)

#: ``{shape: (face S1 slots, face S2 slots, ...)}`` -- Abaqus face numbering,
#: mid-side nodes included. Deliberately incomplete: PYRAMID5 / PYRAMID13 and
#: HEX27 are absent because Abaqus has no pyramid continuum element and no
#: published C3D27 face-node numbering to copy, so there is no authority to
#: follow for them. A caller reaching for a missing entry is meant to fail
#: loudly rather than be handed a guess (or, worse, every node of the element).
solid_abaqus_faces = {
    SolidShapes.TETRA: _TETRA_ABAQUS_FACES,
    SolidShapes.TETRA10: _TETRA10_ABAQUS_FACES,
    SolidShapes.HEX8: _HEX_ABAQUS_FACES,
    SolidShapes.HEX20: _HEX20_ABAQUS_FACES,
    SolidShapes.WEDGE: _WEDGE_ABAQUS_FACES,
    SolidShapes.WEDGE15: _WEDGE15_ABAQUS_FACES,
}

#: The same faces in the same order, corner slots only. A second-order element's faces
#: are numbered exactly like its first-order counterpart's, so the corner slots of a
#: C3D10 face are simply the C3D4 face of the same number -- no assumption needed about
#: mid-side slots coming last inside a tuple. Used where a face has to be *identified*
#: from a set of nodes (``surfaces.solid_abaqus_face_index``) rather than expanded into
#: its nodes; matching on corners keeps that identification working for a caller that
#: passes corner nodes only.
solid_abaqus_face_corners = {
    SolidShapes.TETRA: _TETRA_ABAQUS_FACES,
    SolidShapes.TETRA10: _TETRA_ABAQUS_FACES,
    SolidShapes.HEX8: _HEX_ABAQUS_FACES,
    SolidShapes.HEX20: _HEX_ABAQUS_FACES,
    SolidShapes.WEDGE: _WEDGE_ABAQUS_FACES,
    SolidShapes.WEDGE15: _WEDGE_ABAQUS_FACES,
}
