"""Pure-numpy geometry helpers for the topology layer (no CAD kernel).

These are domain-neutral utilities used by the cell graph: a deterministic
in-plane x-direction for a planar face, and a face/box overlap test.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

_global_axes = {
    "x": np.array([1, 0, 0], dtype=float),
    "y": np.array([0, 1, 0], dtype=float),
    "z": np.array([0, 0, 1], dtype=float),
}


@lru_cache
def calculate_plate_xdir(normal: tuple[float, ...]):
    """
    Calculate a consistent x-direction vector for the plate by aligning it with a global axis,
    based on the plate's normal vector, and ensuring it lies in the plate's plane.

    Parameters:
    - normal: Array-like, the normal vector of the plate.

    Returns:
    - A normalized x-direction vector in the plane of the plate, logically aligned with a global axis.
    """
    # Normalize the plate normal vector
    normal = np.asarray(normal, dtype=float)
    normal /= np.linalg.norm(normal)

    # Find the global axis most perpendicular to the plate normal
    # (we use this to determine the plane in which the plate roughly lies)
    main_plane_axis = min(_global_axes.keys(), key=lambda axis: np.abs(np.dot(normal, _global_axes[axis])))
    initial_xdir = _global_axes[main_plane_axis]

    # Project the chosen global axis onto the plate plane
    xdir_in_plane = initial_xdir - np.dot(initial_xdir, normal) * normal
    xdir_in_plane /= np.linalg.norm(xdir_in_plane)

    return xdir_in_plane


def is_face_inside_box(face_points, p1, p2, tolerance=1e-3):
    """
    Checks if the given face (defined by a list of points) lies within:
    - A flat "box plate" (if p1 and p2 define a 2D region in 3D space).
    - A full 3D bounding box (if p1 and p2 define a volumetric region).
    - Considers a face fully inside if it shares a full surface of the bounding box.

    :param face_points: List or numpy array of 3D points defining the face.
    :param p1: A tuple (x1, y1, z1) representing one corner of the box plate or bounding box.
    :param p2: A tuple (x2, y2, z2) representing the opposite corner.
    :param tolerance: Small numerical tolerance for floating point comparisons.
    :return: True if the face is within or fully coincides with one of the box surfaces, False otherwise.
    """

    # Convert inputs to numpy arrays (avoid copies where possible)
    p1 = np.asarray(p1, dtype=float)
    p2 = np.asarray(p2, dtype=float)
    face_points = np.asarray(face_points, dtype=float)

    # If you rely on exact rounding, keep this; otherwise you can remove for speed.
    # Rounding small inputs is relatively cheap, so we keep it as-is for behavior parity.
    p1 = np.round(p1, 6)
    p2 = np.round(p2, 6)

    # Compute bounding box limits
    min_bounds = np.minimum(p1, p2)
    max_bounds = np.maximum(p1, p2)

    # Determine if p1 and p2 define a "box plate" (one coordinate nearly constant)
    diff = np.abs(p2 - p1)
    fixed_index = int(np.argmin(diff))  # The axis where coordinates are nearly constant
    is_box_plate = diff[fixed_index] <= tolerance  # True if it's a flat plate

    if is_box_plate:
        # Box Plate Logic
        constant_value = p1[fixed_index]

        # Step 1: Ensure the face is in the same plane as the box plate
        # Faster alternative to np.allclose(face_points[:, fixed_index], constant_value, atol=tolerance)
        if np.max(np.abs(face_points[:, fixed_index] - constant_value)) > tolerance:
            return False  # Face is not in the correct plane

        # Step 2: Check if the face significantly overlaps within the rectangular region
        # The two varying coordinates
        if fixed_index == 0:
            moving_axes = (1, 2)
        elif fixed_index == 1:
            moving_axes = (0, 2)
        else:
            moving_axes = (0, 1)

        # Min/max on the two moving axes
        fp_mv = face_points[:, moving_axes]
        face_min = np.min(fp_mv, axis=0)
        face_max = np.max(fp_mv, axis=0)

        # Overlap checks (strict inequalities, per original behavior)
        overlap_x = (face_max[0] > min_bounds[moving_axes[0]]) and (face_min[0] < max_bounds[moving_axes[0]])
        overlap_y = (face_max[1] > min_bounds[moving_axes[1]]) and (face_min[1] < max_bounds[moving_axes[1]])

        return overlap_x and overlap_y

    else:
        # General 3D Bounding Box Logic
        face_min = np.min(face_points, axis=0)
        face_max = np.max(face_points, axis=0)

        # Overlap checks (strict inequalities, per original behavior)
        overlap_x = (face_max[0] > min_bounds[0]) and (face_min[0] < max_bounds[0])
        overlap_y = (face_max[1] > min_bounds[1]) and (face_min[1] < max_bounds[1])
        overlap_z = (face_max[2] > min_bounds[2]) and (face_min[2] < max_bounds[2])

        # Check if the face fully coincides with any of the bounding box's surfaces
        # Replace six np.allclose calls with faster reductions
        fp_x = face_points[:, 0]
        fp_y = face_points[:, 1]
        fp_z = face_points[:, 2]

        coincides_xmin = (np.max(np.abs(fp_x - min_bounds[0])) <= tolerance) and overlap_y and overlap_z
        coincides_xmax = (np.max(np.abs(fp_x - max_bounds[0])) <= tolerance) and overlap_y and overlap_z

        coincides_ymin = (np.max(np.abs(fp_y - min_bounds[1])) <= tolerance) and overlap_x and overlap_z
        coincides_ymax = (np.max(np.abs(fp_y - max_bounds[1])) <= tolerance) and overlap_x and overlap_z

        coincides_zmin = (np.max(np.abs(fp_z - min_bounds[2])) <= tolerance) and overlap_x and overlap_y
        coincides_zmax = (np.max(np.abs(fp_z - max_bounds[2])) <= tolerance) and overlap_x and overlap_y

        coincides_with_surface = (
            coincides_xmin or coincides_xmax or coincides_ymin or coincides_ymax or coincides_zmin or coincides_zmax
        )

        return (overlap_x and overlap_y and overlap_z) or coincides_with_surface
