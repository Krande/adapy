ada_to_meshio_type = {
    "B31": "line",
    "B32": "line3",
    "S3": "triangle",
    "S3R": "triangle",
    "STRI65": "triangle6",
    "S4": "quad",
    "S4R": "quad",
    "C3D8": "hexahedron",
    "C3D20": "hexahedron20",
    "C3D4": "tetra",
    "C3D5": "pyramid",
    "C3D10": "tetra10",
}

meshio_to_ada_type = {value: key for key, value in ada_to_meshio_type.items()}

# From https://github.com/nschloe/meshio/blob/main/src/meshio/gmsh/common.py
gmsh_to_meshio_ordering = {
    "tetra10": [0, 1, 2, 3, 4, 5, 6, 7, 9, 8],
    "hexahedron20": [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15],
    "hexahedron27": [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15, 22, 23, 21, 24, 20, 25, 26],
    "wedge15": [0, 1, 2, 3, 4, 5, 6, 9, 7, 12, 14, 13, 8, 10, 11],
    "pyramid13": [0, 1, 2, 3, 4, 5, 8, 10, 6, 7, 9, 11, 12],
}
