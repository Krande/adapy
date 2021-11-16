from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes

gmsh_map = {
    "Triangle 3": ShellShapes.TRI,
    "Triangle 6": ShellShapes.TRI6,
    "Quadrilateral 4": ShellShapes.QUAD,
    "Quadrilateral 8": ShellShapes.QUAD8,
    "Line 2": LineShapes.LINE,
    "Line 3": LineShapes.LINE3,
    "Tetrahedron 4": SolidShapes.TETRA,
    "Tetrahedron 10": SolidShapes.TETRA10,
    "Hexahedron 8": SolidShapes.HEX8,
    "Hexahedron 20": SolidShapes.HEX20,
}
