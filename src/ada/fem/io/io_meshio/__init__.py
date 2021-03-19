from .reader import meshio_read_fem
from .writer import meshio_to_fem

ada_to_meshio_type = {
    "B31": "line",
    "B32R": "line3",
    "S3": "triangle",
    "S3R": "triangle",
    "S4": "quad",
    "S4R": "quad",
    "C3D8": "hexahedron",
    "C3D20": "hexahedron20",
    "C3D4": "tetra",
    "C3D5": "pyramid",
    "C3D10": "tetra10",
}

meshio_to_ada_type = {value: key for key, value in ada_to_meshio_type.items()}

__all__ = ["meshio_to_fem", "meshio_read_fem", "ada_to_meshio_type", "meshio_to_ada_type"]
