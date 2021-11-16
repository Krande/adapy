from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes

sesam_el_map = {
    15: LineShapes.LINE,
    2: LineShapes.LINE,
    23: LineShapes.LINE3,
    24: ShellShapes.QUAD,
    25: ShellShapes.TRI,
    26: ShellShapes.TRI6,
    28: ShellShapes.QUAD8,
    31: SolidShapes.TETRA10,
    40: "SPRING2",
    18: "SPRING1",
    11: "MASS",
}

sesam_reverse = {value: key for key, value in sesam_el_map.items()}
