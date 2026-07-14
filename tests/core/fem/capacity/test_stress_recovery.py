"""Membrane stress recovery from nodal displacements (plane-stress, closed form)."""

from __future__ import annotations

import numpy as np

from ada.fem.capacity import extract, stress_recovery

E = 210e9
NU = 0.3


def _patch_element(monkeypatch, node_ids, coords):
    monkeypatch.setattr(extract, "element_node_ids", lambda mesh, eid: node_ids)
    monkeypatch.setattr(extract, "element_node_coords", lambda mesh, eid: np.asarray(coords, float))


def test_quad_uniaxial_strain_matches_plane_stress(monkeypatch):
    # Unit square in the global xy-plane; displacement u_x = a*x → eps_xx = a.
    coords = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    _patch_element(monkeypatch, [1, 2, 3, 4], coords)
    a = 1e-3
    disp = {nid: np.array([a * coords[i][0], 0.0, 0.0]) for i, nid in enumerate([1, 2, 3, 4])}

    pts = stress_recovery.recover_membrane_corner_points(None, 99, disp, E, NU)
    assert len(pts) == 4

    f = E / (1 - NU * NU)
    expected = np.array([f * a, NU * f * a, 0.0])
    for _xyz, tensor in pts:  # uniform strain → identical at every corner
        np.testing.assert_allclose(tensor, expected, rtol=1e-9, atol=1.0)


def test_quad_pure_shear(monkeypatch):
    coords = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    _patch_element(monkeypatch, [1, 2, 3, 4], coords)
    g = 2e-3  # u_x = g*y → gamma_xy = g, eps_xx = eps_yy = 0
    disp = {nid: np.array([g * coords[i][1], 0.0, 0.0]) for i, nid in enumerate([1, 2, 3, 4])}

    pts = stress_recovery.recover_membrane_corner_points(None, 1, disp, E, NU)
    G = E / (2 * (1 + NU))
    for _xyz, tensor in pts:
        np.testing.assert_allclose(tensor, [0.0, 0.0, G * g], rtol=1e-9, atol=1.0)


def test_tri_constant_strain(monkeypatch):
    coords = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    _patch_element(monkeypatch, [1, 2, 3], coords)
    a = 1e-3
    disp = {nid: np.array([a * coords[i][0], 0.0, 0.0]) for i, nid in enumerate([1, 2, 3])}

    pts = stress_recovery.recover_membrane_corner_points(None, 7, disp, E, NU)
    assert len(pts) == 3
    f = E / (1 - NU * NU)
    for _xyz, tensor in pts:
        np.testing.assert_allclose(tensor, [f * a, NU * f * a, 0.0], rtol=1e-9, atol=1.0)


def test_local_frame_rotation_invariance(monkeypatch):
    # Same uniaxial state, but the element lies in a tilted plane and the load is
    # applied along its first edge: recovered local-frame stress is unchanged.
    coords = [(0, 0, 0), (1, 1, 1), (0, 2, 2), (-1, 1, 1)]
    _patch_element(monkeypatch, [1, 2, 3, 4], coords)
    ex = np.array(coords[1]) - np.array(coords[0])
    ex = ex / np.linalg.norm(ex)
    a = 1e-3
    disp = {nid: a * float(np.dot(np.array(coords[i]), ex)) * ex for i, nid in enumerate([1, 2, 3, 4])}

    pts = stress_recovery.recover_membrane_corner_points(None, 3, disp, E, NU)
    f = E / (1 - NU * NU)
    expected = np.array([f * a, NU * f * a, 0.0])
    for _xyz, tensor in pts:
        np.testing.assert_allclose(tensor, expected, rtol=1e-6, atol=10.0)


def test_unsupported_shape_returns_empty(monkeypatch):
    _patch_element(monkeypatch, [1, 2], [(0, 0, 0), (1, 0, 0)])
    assert stress_recovery.recover_membrane_corner_points(None, 1, {}, E, NU) == []
