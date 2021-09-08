from meshio.abaqus._abaqus import abaqus_to_meshio_type as aba_meshio_original

gmsh_to_meshio_ordering = {
    "tetra10": [0, 1, 2, 3, 4, 5, 6, 7, 9, 8],
    "hexahedron20": [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15],
    "hexahedron27": [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15, 22, 23, 21, 24, 20, 25, 26],
    "wedge15": [0, 1, 2, 3, 4, 5, 6, 9, 7, 12, 14, 13, 8, 10, 11],
    "pyramid13": [0, 1, 2, 3, 4, 5, 8, 10, 6, 7, 9, 11, 12],
    "line3": [0, 2, 1],
}

abaqus_to_meshio_type = aba_meshio_original

default = dict(hexahedron="C3D8", triangle="S3", tetra10="C3D10", line="B31")

meshio_to_abaqus_type = {v: k if v not in default.keys() else default[v] for k, v in abaqus_to_meshio_type.items()}
