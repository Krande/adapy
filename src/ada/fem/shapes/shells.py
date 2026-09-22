# GMSH Node ordering
# """Quadrangle:            Quadrangle8:            Quadrangle9:
#
#       v
#       ^
#       |
# 3-----------2          3-----6-----2           3-----6-----2
# |     |     |          |           |           |           |
# |     |     |          |           |           |           |
# |     +---- | --> u    7           5           7     8     5
# |           |          |           |           |           |
# |           |          |           |           |           |
# 0-----------1          0-----4-----1           0-----4-----1
#
# Triangle:               Triangle6:          Triangle9/10:          Triangle12/15:
#
# v
# ^                                                                   2
# |                                                                   | \
# 2                       2                    2                      9   8
# |`\                     |`\                  | \                    |     \
# |  `\                   |  `\                7   6                 10 (14)  7
# |    `\                 5    `4              |     \                |         \
# |      `\               |      `\            8  (9)  5             11 (12) (13) 6
# |        `\             |        `\          |         \            |             \
# 0----------1 --> u      0-----3----1         0---3---4---1          0---3---4---5---1
# """
from ada.fem.shapes.definitions import ShellShapes

_TRI_CORNER_EDGES = [[0, 1], [1, 2], [2, 0]]
_QUAD_CORNER_EDGES = [[0, 1], [1, 2], [2, 3], [3, 0]]

shell_edges = {
    ShellShapes.QUAD: _QUAD_CORNER_EDGES,
    ShellShapes.TRI: _TRI_CORNER_EDGES,
    # 2nd-order shells: corner-to-corner edges only. Curved mid-edge
    # rendering would need per-element shape-function sampling, which
    # the bake doesn't do; for visualization the straight-edge wireframe
    # matches what the underlying TRI3/QUAD4 viz mesh shows. TRI7 +
    # QUAD9 are the central-node variants Code Aster emits for some
    # shell-O2 stacks — same corner layout as TRI6 / QUAD8, just one
    # extra central node we ignore for visualization.
    ShellShapes.TRI6: _TRI_CORNER_EDGES,
    ShellShapes.TRI7: _TRI_CORNER_EDGES,
    ShellShapes.QUAD8: _QUAD_CORNER_EDGES,
    ShellShapes.QUAD9: _QUAD_CORNER_EDGES,
}
# Triangulation for rendering: 2nd-order shells share the corner-node
# layout of their 1st-order counterparts (TRI6 → 3 corners + 3
# mid-edge nodes, QUAD8 → 4 corners + 4 mid-edge nodes), so we
# triangulate the corners and ignore the mid-edge nodes. Viz only —
# the simulation uses the full higher-order DOFs. Without these
# entries ``_compute_topology`` skipped the cell block and
# ``write_mesh_glb`` fell back to POINTS-only output, leaving Code
# Aster shell-O2 results blank in the viewer.
_TRI_CORNER_FACES = [[0, 1, 2]]
_QUAD_CORNER_FACES = [[0, 1, 2], [0, 2, 3]]

shell_faces = {
    ShellShapes.QUAD: _QUAD_CORNER_FACES,
    ShellShapes.TRI: _TRI_CORNER_FACES,
    ShellShapes.TRI6: _TRI_CORNER_FACES,
    ShellShapes.TRI7: _TRI_CORNER_FACES,
    ShellShapes.QUAD8: _QUAD_CORNER_FACES,
    ShellShapes.QUAD9: _QUAD_CORNER_FACES,
}


# ---------------------------------------------------------------------------
# Abaqus shell edges, mid-side nodes included.
#
# ``shell_edges`` above is visualization topology (corner-to-corner only, and
# the same list reused for the second-order shapes). A ``*Surface,
# type=ELEMENT`` row naming ``E2`` needs exactly the nodes lying on that edge,
# which for a second-order shell includes the edge's mid-side node.
#
# Ordering authority: Abaqus Analysis User's Guide, "Shell elements" ->
# "Defining edge loads and surfaces on shells", where edge ``En`` runs between
# corner nodes ``n`` and ``n+1`` (wrapping), read against the shell node
# numbering in the same library section: the mid-side node of edge ``En`` is
# node ``3+n`` on a 6-node triangle and ``4+n`` on an 8-node quadrilateral.
# adapy's native ordering is Abaqus' here (see
# ``node_order.NATIVE_MIDSIDE_EDGES``), so the 1-based numbers translate by
# subtracting one.
#
# Indexed 0-based: entry ``i`` is Abaqus edge ``E(i+1)``.
#
# Note that ``SPOS`` / ``SNEG`` are *not* in these tables: they name a face of
# the shell, i.e. the whole element, and every node of the element lies on it.
# Callers handle those separately.

_TRI_ABAQUS_EDGES = ((0, 1), (1, 2), (2, 0))
_QUAD_ABAQUS_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0))
# 6-node triangle: node 4 on edge 1-2, 5 on 2-3, 6 on 3-1 (slots 3..5).
_TRI6_ABAQUS_EDGES = ((0, 1, 3), (1, 2, 4), (2, 0, 5))
# 8-node quadrilateral: node 5 on edge 1-2, 6 on 2-3, 7 on 3-4, 8 on 4-1
# (slots 4..7). TRI7 / QUAD9 carry an extra *centre* node, which lies on no
# edge, so they reuse the TRI6 / QUAD8 tables unchanged.
_QUAD8_ABAQUS_EDGES = ((0, 1, 4), (1, 2, 5), (2, 3, 6), (3, 0, 7))

#: ``{shape: (edge E1 slots, edge E2 slots, ...)}`` -- Abaqus shell edge
#: numbering, mid-side nodes included.
shell_abaqus_edges = {
    ShellShapes.TRI: _TRI_ABAQUS_EDGES,
    ShellShapes.TRI6: _TRI6_ABAQUS_EDGES,
    ShellShapes.TRI7: _TRI6_ABAQUS_EDGES,
    ShellShapes.QUAD: _QUAD_ABAQUS_EDGES,
    ShellShapes.QUAD8: _QUAD8_ABAQUS_EDGES,
    ShellShapes.QUAD9: _QUAD8_ABAQUS_EDGES,
}
