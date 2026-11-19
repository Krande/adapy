"""The OCC backend's volume / area are integrated to a stated accuracy, and ``tessellate``'s
``linear_deflection`` is an absolute chordal deflection in the model's units -- as adacpp and the
backend's other mesh verbs read it -- not ShapeTesselator's relative quality."""

from __future__ import annotations

import math

import numpy as np

import ada
from ada.geom.booleans import BoolOpEnum


def _tube_cut_by_tube(be):
    """A 0.15 m tube standing on the outer surface of a 0.3 m one: a saddle of trimmed curved faces."""
    brace = be.boolean(
        BoolOpEnum.DIFFERENCE,
        be.build(ada.PrimCyl("o", (0, 0, 0), (0, 0, 1), 0.075).solid_geom()),
        be.build(ada.PrimCyl("i", (0, 0, 0), (0, 0, 1), 0.065).solid_geom()),
    )
    chord = be.build(ada.PrimCyl("c", (-1, 0, 0), (1, 0, 0), 0.15).solid_geom())
    return be.boolean(BoolOpEnum.DIFFERENCE, brace, chord)


def test_volume_and_area_are_integrated_tightly(occ_backend):
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps

    be = occ_backend
    shape = _tube_cut_by_tube(be)

    def converged(props_fn):
        p = GProp_GProps()
        props_fn(shape, p, 1e-10)
        return p.Mass()

    assert be.volume(shape) == pytest_approx(converged(brepgprop.VolumeProperties), 1e-8)
    assert be.area(shape) == pytest_approx(converged(brepgprop.SurfaceProperties), 1e-8)
    r = 0.4
    sphere = be.build(ada.PrimSphere("s", (0, 0, 0), r).solid_geom())
    assert be.volume(sphere) == pytest_approx(4 / 3 * math.pi * r**3, 1e-8)


def test_a_linear_deflection_is_absolute_and_bounded(occ_backend):
    be = occ_backend
    r = 0.5
    deflection = 0.001  # 1 mm on a metre-sized cylinder: the call that once grew a process to ~100 GB
    mesh = be.tessellate(be.build(ada.PrimCyl("c", (0, 0, 0), (0, 0, 1), r).solid_geom()), deflection)
    pos = np.asarray(mesh.positions, dtype=float).reshape(-1, 3)
    tris = np.asarray(mesh.faces).reshape(-1, 3)
    # A chord of the circle within the deflection: about pi / acos(1 - d/r) segments round it.
    n_round = math.pi / math.acos(1 - deflection / r)
    assert len(tris) < 20 * n_round
    # The rim's vertices lie on the circle, spaced so that each chord between neighbours sags no
    # more than the deflection: sagitta r (1 - cos(gap / 2)) <= d.
    radial = np.linalg.norm(pos[:, :2], axis=1)
    rim = pos[(radial > r * 0.99) & (np.abs(pos[:, 2]) < 1e-9)]
    assert np.allclose(np.linalg.norm(rim[:, :2], axis=1), r, atol=1e-9)
    angles = np.unique(np.round(np.arctan2(rim[:, 1], rim[:, 0]), 12))
    gaps = np.diff(np.concatenate([angles, angles[:1] + 2 * math.pi]))
    assert (r * (1 - np.cos(gaps.max() / 2))) <= deflection * 1.01


def test_a_deflection_far_too_fine_is_raised_to_a_floor(occ_backend):
    from ada.occ.backend import TESS_MIN_DEFLECTION_OF_DIAGONAL

    be = occ_backend
    shape = be.build(ada.PrimCyl("c", (0, 0, 0), (0, 0, 1), 0.5).solid_geom())
    # 1e-12 m on a metre-sized body: a deflection in the wrong units. Meshed at the floor instead.
    fine = be.tessellate(shape, 1e-12)
    fresh = be.build(ada.PrimCyl("c", (0, 0, 0), (0, 0, 1), 0.5).solid_geom())
    floor = be.tessellate(fresh, 1.5 * TESS_MIN_DEFLECTION_OF_DIAGONAL)
    assert len(np.asarray(fine.faces)) <= 2 * len(np.asarray(floor.faces))


def pytest_approx(value, rel):
    import pytest

    return pytest.approx(value, rel=rel)
