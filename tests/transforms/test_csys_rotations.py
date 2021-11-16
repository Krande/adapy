import numpy as np

from ada.core.utils import roundoff
from ada.core.vector_utils import (
    global_2_local_nodes,
    local_2_global_nodes,
    rotation_matrix_csys_rotate,
)


def test_roundtrip_global_coords_2_local():
    # Local Coordinate System
    xvec = (1, 0, 0)
    yvec = (0, 0, 1)
    normal = np.cross(xvec, yvec)
    csys2 = [xvec, yvec]

    origin = (0, 0, 0)
    point = (2, -0.3, 2)

    loc_points = global_2_local_nodes(csys2, origin, [point])
    glob_points = local_2_global_nodes(loc_points, origin, xvec, normal)
    ev1 = tuple([roundoff(x) for x in glob_points[0]])
    ev2 = tuple([float(x) for x in point])
    assert ev1 == ev2


def test_csys_rotation1():
    csys1 = [(1, 0, 0), (0, 1, 0)]

    point = (2, -0.3, 2)
    xvec = np.array([1, 0, 0])
    yvec = np.array([0, 0, 1])
    csys2 = [xvec, yvec]

    rm_to_local = rotation_matrix_csys_rotate(csys1, csys2)
    p_local = np.dot(rm_to_local, point)

    rm_to_global = rotation_matrix_csys_rotate(csys2, csys1, inverse=True)
    p_global = np.dot(rm_to_global, p_local)
    ev1 = tuple([roundoff(x) for x in p_global])
    ev2 = tuple([float(x) for x in point])
    ev = ev1 == ev2

    assert ev is True


def test_rotate_about_Z():
    p_a = (1, 1, 0)
    # Global axis
    globx = (1, 0, 0)
    globy = (0, 1, 0)
    globz = (0, 0, 1)
    csys_a = np.array([np.array(x).astype(float) for x in [globx, globy, globz]])
    origin = (0, 0, 0)
    normal = (0, 0, 1)

    # Start with a 90 degree counter-clockwise rotation (x = pos y)
    xvec = (0, 1, 0)  # Rotate
    yvec = np.cross(normal, xvec).astype(float)
    csys2 = np.array([np.array(xvec).astype(float), yvec, np.array(normal).astype(float)])
    rp2 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csys_a, csys2), p_a)
    assert tuple([roundoff(x) for x in rp2]) == (1.0, -1.0, 0.0)

    # Rotate another 90 degrees counter-clockwise
    xvec = (-1, 0, 0)
    yvec = np.cross(normal, xvec).astype(float)
    csys3 = np.array([np.array(xvec).astype(float), yvec, np.array(normal).astype(float)])

    rp3 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csys_a, csys3), p_a)
    assert tuple([roundoff(x) for x in rp3]) == (-1.0, -1.0, 0)

    rp4 = np.array(origin) + np.dot(rotation_matrix_csys_rotate(csys3, csys_a), rp3)
    assert tuple([roundoff(x) for x in rp4]) == tuple([float(x) for x in p_a])
