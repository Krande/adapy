import numpy as np
import trimesh

from ada.core.vector_transforms import rot_matrix


def from_y_to_z_is_up(source_scene: trimesh.Scene, transform_all_geom=True) -> None:
    """In-place transform the scene to make Z up."""
    if transform_all_geom:
        transform_all_geom_to_z_up(source_scene)
        return None

    # make scene Z is up (rvm parser is y up, by default)
    m3x3 = rot_matrix((0, 1, 0))
    m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
    m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
    source_scene.apply_transform(m4x4)

    return None


def transform_all_geom_to_z_up(source_scene: trimesh.Scene) -> None:
    for mesh in source_scene.geometry.values():
        if isinstance(mesh, trimesh.Trimesh):
            # switch Y and Z axes
            mesh.vertices = mesh.vertices @ rot_matrix((0, 0, 1), (0, 1, 0))
        else:
            raise TypeError(f"Unsupported geometry type: {type(mesh)}")
