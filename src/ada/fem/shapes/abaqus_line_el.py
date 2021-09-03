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
line_edges = dict(line=[[0, 1]], line3=[[0, 2, 1]], SPRING2=[[0, 1]], CONNECTOR=[[0, 1]])
