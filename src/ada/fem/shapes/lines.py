# GMSH Node numbering
# """
#         Line:                 Line3:          Line4:
#
#       v
#       ^
#       |
#       |
# 0-----+-----1 --> u   0----2----1     0---2---3---1
#
# """
# Note: Line3 is changed to 0-1-2 to comply with Abaqus node ordering
from ada.fem.shapes.definitions import ConnectorTypes, LineShapes, SpringTypes

line_edges = {
    LineShapes.LINE: [[0, 1]],
    LineShapes.LINE3: [[0, 2]],
    SpringTypes.SPRING2: [[0, 1]],
    ConnectorTypes.CONNECTOR: [[0, 1]],
}
