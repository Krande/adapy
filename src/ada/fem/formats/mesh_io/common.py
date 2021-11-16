from ada.fem.shapes import ElemShape

sh = ElemShape.TYPES.shell
so = ElemShape.TYPES.solids
li = ElemShape.TYPES.lines

ada_to_meshio = {
    sh.TRI: "triangle",
    sh.TRI6: "triangle6",
    sh.TRI7: "triangle7",
    sh.QUAD: "quad",
    sh.QUAD8: "quad8",
    so.HEX8: "hexahedron",
    so.HEX20: "hexahedron20",
    so.HEX27: "hexahedron27",
    so.TETRA: "tetra",
    so.TETRA10: "tetra10",
    so.PYRAMID5: "pyramid5",
    so.WEDGE: "wedge",
    so.WEDGE15: "wedge15",
    li.LINE: "line",
    li.LINE3: "line3",
}

meshio_to_ada = {val: key for key, val in ada_to_meshio.items()}
