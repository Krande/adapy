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

shell_edges = {
    ShellShapes.QUAD: [[0, 1], [1, 2], [2, 3], [3, 0]],
    ShellShapes.TRI: [[0, 1], [1, 2], [2, 0]],
    ShellShapes.TRI6: [[0, 1], [1, 2], [2, 0]],
    # 2nd-order shells: corner-to-corner edges only. Curved mid-edge
    # rendering would need per-element shape-function sampling, which
    # the bake doesn't do; for visualization the straight-edge wireframe
    # matches what the underlying TRI3/QUAD4 viz mesh shows.
    ShellShapes.QUAD8: [[0, 1], [1, 2], [2, 3], [3, 0]],
}
# Triangulation for rendering: 2nd-order shells share the corner-node
# layout of their 1st-order counterparts (TRI6 → 3 corners + 3
# mid-edge nodes, QUAD8 → 4 corners + 4 mid-edge nodes), so we
# triangulate the corners and ignore the mid-edge nodes. Viz only —
# the simulation uses the full higher-order DOFs. Without this entry
# `_compute_topology` skips the cell block and `write_mesh_glb` falls
# back to POINTS-only output, which kept ca_shell_o2 figures blank.
shell_faces = {
    ShellShapes.QUAD: [[0, 1, 2], [0, 2, 3]],
    ShellShapes.TRI: [[0, 1, 2]],
    ShellShapes.TRI6: [[0, 1, 2]],
    ShellShapes.QUAD8: [[0, 1, 2], [0, 2, 3]],
}
