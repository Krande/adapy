"""``ada.fem.frames``: a support in a rotated frame, in global axes where the frame allows, and Code_Aster's angles."""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet
from ada.fem.common import Csys
from ada.fem.frames import in_global_axes, nautical_angles, nautical_matrix


def _bc(dofs, coords, magnitudes=None):
    fs = FemSet("s", [ada.Node((0, 0, 0), 1)], "nset")
    return Bc("b", fs, dofs, magnitudes=magnitudes, csys=Csys("c", coords=coords))


X_IS_Y = [(0, 1, 0), (-1, 0, 0)]  # x' = y, y' = -x, z' = z
SLOPE = [(0.6, 0, 0.8), (0, 1, 0)]  # x' = (0.6, 0, 0.8), y' = y, z' = (-0.8, 0, 0.6)
DIAGONAL = [(np.sqrt(0.5), np.sqrt(0.5), 0), (-np.sqrt(0.5), np.sqrt(0.5), 0)]  # 45 degrees about z


@pytest.mark.parametrize(
    "dofs, coords, want",
    [
        ([1], X_IS_Y, [2]),  # dx' = dy
        ([1, 2], X_IS_Y, [1, 2]),
        ([4], X_IS_Y, [5]),  # rx' = ry
        ([3], DIAGONAL, [3]),  # dz alone under a turn about z
        ([1, 2, 6], DIAGONAL, [1, 2, 6]),  # the whole x-y plane, and rz
        ([2], SLOPE, [2]),  # y' = y
        ([1, 3], SLOPE, [1, 3]),
        ([1, 2, 3, 4, 5, 6], SLOPE, [1, 2, 3, 4, 5, 6]),
    ],
)
def test_a_frame_that_maps_onto_global_axes_becomes_a_global_support(dofs, coords, want):
    bc = _bc(dofs, coords)
    g = in_global_axes(bc)
    assert g is not bc and g.csys is None and list(g.dofs) == want
    assert bc.csys is not None and list(bc.dofs) == dofs  # the model's own Bc is left alone


@pytest.mark.parametrize("dofs, coords", [([1], DIAGONAL), ([3], SLOPE), ([1, 6], SLOPE), ([4], DIAGONAL)])
def test_a_frame_that_mixes_the_global_axes_stays_a_frame(dofs, coords):
    assert in_global_axes(_bc(dofs, coords)) is None


def test_a_prescribed_value_maps_with_its_sign():
    g = in_global_axes(_bc([2], X_IS_Y, [0.01]))  # along y' = -x
    assert list(g.dofs) == [1] and g.magnitudes == [pytest.approx(-0.01)]
    assert in_global_axes(_bc([1], DIAGONAL, [0.01])) is None


def test_a_global_or_identity_frame_is_the_bc_itself():
    fs = FemSet("s", [ada.Node((0, 0, 0), 1)], "nset")
    bc = Bc("b", fs, [1, 2])
    assert in_global_axes(bc) is bc
    ident = _bc([1], [(1, 0, 0), (0, 1, 0)])
    assert in_global_axes(ident) is ident


@pytest.mark.parametrize("coords", [SLOPE, DIAGONAL, X_IS_Y, [(0, 0, 1), (1, 0, 0)], [(0, 0, -1), (0, 1, 0)]])
def test_nautical_angles_rebuild_the_frame(coords):
    axes = Csys("c", coords=coords).axes()
    assert np.allclose(nautical_matrix(*nautical_angles(axes)), axes)


def test_nautical_angles_of_a_turn_about_z_is_alpha():
    a, b, g = nautical_angles(Csys("c", coords=DIAGONAL).axes())
    assert (a, b, g) == pytest.approx((45.0, 0.0, 0.0))
