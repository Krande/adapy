"""``Load.forces_global``: a point load given in a local system, in global components.

The local system is Abaqus' ``*Transform`` pair (a point on local x, one in the local x-y plane), whose rows
(:meth:`ada.fem.common.Csys.axes`) turn local into global as ``f_global = axes.T @ f_local``. The method used to apply
the inverse rotation, so a force along a local x that is global y came out along minus global y, and Code_Aster and
CalculiX were handed it that way.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import FemSet, LoadPoint
from ada.fem.common import Csys


def _load(dof, coords, magnitude=1.0):
    fs = FemSet("s", [ada.Node((0, 0, 0), 1)], "nset")
    return LoadPoint("l", magnitude, fs, dof, csys=Csys("c", coords=coords))


@pytest.mark.parametrize(
    "coords, dof, want",
    [
        ([(0, 1, 0), (-1, 0, 0)], 1, (0, 1, 0, 0, 0, 0)),  # x' = y
        ([(0, 1, 0), (-1, 0, 0)], 2, (-1, 0, 0, 0, 0, 0)),  # y' = -x
        ([(0, 0, 1), (1, 0, 0)], 1, (0, 0, 1, 0, 0, 0)),  # x' = z
        ([(0, 0, 1), (1, 0, 0)], 3, (0, 1, 0, 0, 0, 0)),  # z' = x' x y' = z x x = y
        ([(0.6, 0, 0.8), (0, 1, 0)], 1, (0.6, 0, 0.8, 0, 0, 0)),  # a 3-4-5 slope
        ([(0.6, 0, 0.8), (0, 1, 0)], 6, (0, 0, 0, -0.8, 0, 0.6)),  # a moment about z' = (-0.8, 0, 0.6)
    ],
)
def test_a_local_component_lands_on_its_global_axis(coords, dof, want):
    assert np.allclose(_load(dof, coords).forces_global, want)


def test_the_magnitude_scales_and_global_is_unchanged_without_a_system():
    assert np.allclose(_load(1, [(0, 1, 0), (-1, 0, 0)], 250.0).forces_global, (0, 250, 0, 0, 0, 0))
    fs = FemSet("s", [ada.Node((0, 0, 0), 1)], "nset")
    assert LoadPoint("l", 3.0, fs, 2).forces_global == [0.0, 3.0, 0.0, 0.0, 0.0, 0.0]


def test_csys_axes_are_an_orthonormal_right_handed_frame():
    axes = Csys("c", coords=[(2.0, 0, 0), (1.0, 5.0, 0)]).axes()
    assert np.allclose(axes, np.eye(3))
    axes = Csys("c", coords=[(0.6, 0, 0.8), (0.3, 1.0, 0.4)]).axes()
    assert np.allclose(axes @ axes.T, np.eye(3)) and np.linalg.det(axes) == pytest.approx(1.0)
    assert Csys("c", definition="NODES", nodes=None).axes() is None
