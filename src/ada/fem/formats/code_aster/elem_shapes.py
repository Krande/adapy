from ada.fem.shapes.definitions import (
    ConnectorTypes,
    LineShapes,
    MassTypes,
    ShellShapes,
    SolidShapes,
    SpringTypes,
)

ada_to_med_format = {
    MassTypes.MASS: "PO1",
    SpringTypes.SPRING1: "PO1",
    SpringTypes.SPRING2: "SE2",
    LineShapes.LINE: "SE2",
    ConnectorTypes.CONNECTOR: "SE2",
    LineShapes.LINE3: "SE3",
    ShellShapes.TRI: "TR3",
    ShellShapes.TRI6: "TR6",
    ShellShapes.TRI7: "TR7",  # Code Aster Specific type
    ShellShapes.QUAD: "QU4",
    ShellShapes.QUAD8: "QU8",
    ShellShapes.QUAD9: "QU9",  # Code Aster Specific type
    SolidShapes.TETRA: "TE4",
    SolidShapes.TETRA10: "T10",
    SolidShapes.HEX8: "HE8",
    SolidShapes.HEX20: "H20",
    SolidShapes.PYRAMID5: "PY5",
    # "pyramid13": "P13",
    SolidShapes.WEDGE: "PE6",
    # "wedge15": "P15",
}

med_reduced_map = {
    "QU4": "QU4S",
    "QU8": "QU8S",
}
