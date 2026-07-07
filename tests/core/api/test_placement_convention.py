"""Placement rotation-convention self-consistency.

Historically ``from_4x4_matrix`` extracted the local axes from the matrix COLUMNS while every
other constructor (``from_axis_angle`` / ``from_quaternion`` / ``rotate`` /
``get_absolute_placement``) and ``rot_matrix`` used ROWS — so ``from_4x4_matrix`` produced the
TRANSPOSE (== inverse for a rotation), and a rotated IFC ObjectPlacement rendered on the wrong
world axis. These tests pin the invariants that keep the class consistent.
"""

import numpy as np

from ada import Placement

# A genuinely asymmetric rotation: 90deg about Z. Standard world matrix M has COLUMNS equal to
# the world direction of each local axis, so M @ (1,0,0,1) sends local +X to world +Y.
_R = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
_ORIGIN = np.array([5.0, 2.0, 0.0])
_M = np.eye(4)
_M[:3, :3] = _R
_M[:3, 3] = _ORIGIN


def _M_at(local):
    """Ground-truth world point: the standard homogeneous transform applied to a local point."""
    return (_M @ np.array([local[0], local[1], local[2], 1.0]))[:3]


def test_from_4x4_matrix_round_trips_through_get_matrix4x4():
    """from_4x4_matrix(M).get_matrix4x4() must equal M (it used to return M.T)."""
    p = Placement.from_4x4_matrix(_M)
    assert np.allclose(p.get_matrix4x4(), _M)


def test_from_4x4_matrix_transforms_local_axis_to_world_direction():
    """local +X must map to M's column-0 world direction (+Y here), not its negation."""
    p = Placement.from_4x4_matrix(_M)
    assert np.allclose(p.transform_local_points_to_global(np.array([[1.0, 0, 0]]))[0], _M_at((1, 0, 0)))
    assert np.allclose(p.transform_local_points_to_global(np.array([[0.0, 1, 0]]))[0], _M_at((0, 1, 0)))


def test_transform_methods_agree_with_get_matrix4x4():
    """The transform_* helpers and the 4x4 matrix must describe the SAME transform."""
    from ada import Placement as _P

    p = Placement.from_4x4_matrix(_M)
    for local in [(1, 0, 0), (0, 1, 0), (0, 0, 1), (0.3, -0.7, 1.2)]:
        truth = _M_at(local)
        via_pts = p.transform_local_points_to_global(np.array([[float(local[0]), local[1], local[2]]]))[0]
        via_other = p.transform_array_from_other_place(
            np.array([[float(local[0]), local[1], local[2]]]), _P()
        )[0]
        via_mat = (p.get_matrix4x4() @ np.array([local[0], local[1], local[2], 1.0]))[:3]
        assert np.allclose(via_pts, truth), f"transform_local_points_to_global {local}"
        assert np.allclose(via_other, truth), f"transform_array_from_other_place {local}"
        assert np.allclose(via_mat, truth), f"get_matrix4x4 {local}"


def test_from_4x4_matrix_matches_from_axis_angle():
    """from_4x4_matrix of a 90deg-about-Z world matrix must equal from_axis_angle(Z, 90) — the
    two construction paths must produce the same rotation, not transposes of each other."""
    p_mat = Placement.from_4x4_matrix(_M)
    p_aa = Placement.from_axis_angle([0, 0, 1], 90, origin=_ORIGIN)
    assert np.allclose(np.array(p_mat.rot_matrix), np.array(p_aa.rot_matrix))
