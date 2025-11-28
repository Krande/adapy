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

# MED Geometry Type Codes (GEO attribute in HDF5)
# These codes are defined by the MED library specification
med_geometry_type = {
    "PO1": 1,  # Point with 1 node
    "SE2": 102,  # Segment with 2 nodes
    "SE3": 103,  # Segment with 3 nodes
    "TR3": 203,  # Triangle with 3 nodes
    "TR6": 206,  # Triangle with 6 nodes
    "TR7": 207,  # Triangle with 7 nodes
    "QU4": 204,  # Quadrangle with 4 nodes
    "QU8": 208,  # Quadrangle with 8 nodes
    "QU9": 209,  # Quadrangle with 9 nodes
    "TE4": 304,  # Tetrahedron with 4 nodes
    "T10": 310,  # Tetrahedron with 10 nodes
    "HE8": 308,  # Hexahedron with 8 nodes
    "H20": 320,  # Hexahedron with 20 nodes
    "PY5": 305,  # Pyramid with 5 nodes
    "P13": 313,  # Pyramid with 13 nodes
    "PE6": 306,  # Pentahedron/Wedge with 6 nodes
    "P15": 315,  # Pentahedron with 15 nodes
}

med_reduced_map = {
    "QU4": "QU4S",
    "QU8": "QU8S",
}
