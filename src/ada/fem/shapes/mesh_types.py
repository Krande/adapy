from ada.fem.shapes.definitions import ElemShapeTypes

solids = ElemShapeTypes.solids
shells = ElemShapeTypes.shell
lines = ElemShapeTypes.lines

gmsh_to_meshio_ordering = {
    solids.TETRA10: [0, 1, 2, 3, 4, 5, 6, 7, 9, 8],
    solids.HEX20: [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15],
    solids.HEX27: [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15, 22, 23, 21, 24, 20, 25, 26],
    solids.WEDGE15: [0, 1, 2, 3, 4, 5, 6, 9, 7, 12, 14, 13, 8, 10, 11],
    solids.PYRAMID13: [0, 1, 2, 3, 4, 5, 8, 10, 6, 7, 9, 11, 12],
    lines.LINE3: [0, 2, 1],
}

# Vendored from meshio's abaqus._abaqus module so we don't pull
# meshio for what is just a translation table. Keep the meshio
# names ('triangle', 'hexahedron', etc.) on the value side — they
# remain the canonical type names used across adapy.
aba_to_meshio_types = {
    "B21": "line",
    "B21H": "line",
    "B22": "line3",
    "B22H": "line3",
    "B31": "line",
    "B31H": "line",
    "B32": "line3",
    "B32H": "line3",
    "B33": "line3",
    "B33H": "line3",
    "C3D10": "tetra10",
    "C3D10H": "tetra10",
    "C3D10I": "tetra10",
    "C3D10M": "tetra10",
    "C3D10MH": "tetra10",
    "C3D15": "wedge15",
    "C3D20": "hexahedron20",
    "C3D20H": "hexahedron20",
    "C3D20R": "hexahedron20",
    "C3D20RH": "hexahedron20",
    "C3D4": "tetra",
    "C3D4H": "tetra4",
    "C3D6": "wedge",
    "C3D8": "hexahedron",
    "C3D8H": "hexahedron",
    "C3D8I": "hexahedron",
    "C3D8IH": "hexahedron",
    "C3D8R": "hexahedron",
    "C3D8RH": "hexahedron",
    "CAX4P": "quad",
    "CPE6": "triangle6",
    "CPS3": "triangle",
    "CPS4": "quad",
    "CPS4R": "quad",
    "R3D3": "triangle",
    "S3": "triangle",
    "S3R": "triangle",
    "S3RS": "triangle",
    "S4": "quad",
    "S4R": "quad",
    "S4R5": "quad",
    "S4RS": "quad",
    "S4RSW": "quad",
    "S8R": "quad8",
    "S8R5": "quad8",
    "S9R5": "quad9",
    "STRI3": "triangle",
    "STRI65": "triangle6",
    "T2D2": "line",
    "T2D2H": "line",
    "T2D3": "line3",
    "T2D3H": "line3",
    "T3D2": "line",
    "T3D2H": "line",
    "T3D3": "line3",
    "T3D3H": "line3",
}

meshio_convert_default = dict(
    hexahedron=solids.HEX8,
    hexahedron20=solids.HEX20,
    triangle=shells.TRI,
    tetra10=solids.TETRA10,
    line=lines.LINE,
)

meshio_to_abaqus_type = {
    v: k if v not in meshio_convert_default.keys() else meshio_convert_default[v]
    for k, v in aba_to_meshio_types.items()
}

# Canonical string-name → adapy element-type. The string names are
# shared with meshio for legacy continuity (and some bridge code
# still consumes them) but this dict has no meshio dependency.
ada_to_str_type = {
    shells.TRI: "triangle",
    shells.TRI6: "triangle6",
    shells.TRI7: "triangle7",
    shells.QUAD: "quad",
    shells.QUAD8: "quad8",
    solids.HEX8: "hexahedron",
    solids.HEX20: "hexahedron20",
    solids.HEX27: "hexahedron27",
    solids.TETRA: "tetra",
    solids.TETRA10: "tetra10",
    solids.PYRAMID5: "pyramid5",
    solids.WEDGE: "wedge",
    solids.WEDGE15: "wedge15",
    lines.LINE: "line",
    lines.LINE3: "line3",
}
str_to_ada_type = {v: k for k, v in ada_to_str_type.items()}

meshio_to_ada_type = {}
