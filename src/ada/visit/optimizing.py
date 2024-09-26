import numpy as np
import trimesh


def optimize_positions(positions: np.ndarray, indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Remove duplicate positions and update object index accordingly"""

    if len(positions.shape) == 1:
        positions = positions.copy().reshape(len(positions) // 3, 3)
    if len(indices.shape) == 1:
        indices = indices.copy().reshape(len(indices) // 3, 3)

    mesh = trimesh.Trimesh(vertices=positions, faces=indices)
    mesh.merge_vertices()

    return np.array(mesh.vertices).flatten(), np.array(mesh.faces).flatten()
