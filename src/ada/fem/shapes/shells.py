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
}
shell_faces = {ShellShapes.QUAD: [[0, 1, 2], [0, 2, 3]], ShellShapes.TRI: [[0, 1, 2]]}
