from meshio.abaqus._abaqus import abaqus_to_meshio_type as aba_meshio_original

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

aba_to_meshio_types = aba_meshio_original

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

meshio_to_ada_type = {}
