"""Consistent nodal forces of a uniform traction on an element face: ``f_i = t * integral(N_i dA)``.

For a writer with no distributed traction in global components (CalculiX's ``*DLOAD`` has a normal pressure only), a
traction on a shell or a solid face is the nodal forces its shape functions give -- exact for the element, as the
solver's own distributed load would be. A face is its corner nodes in winding order, then the mid-side node of each
corner-to-next edge (Abaqus's order, and :func:`ada.fem.surfaces.side_node_indices`'s): TRI3, QUAD4, TRI6, QUAD8.

The weights sum to the face's area; on a flat parallelogram QUAD4 each is a quarter of it, and on a TRI6 or QUAD8 the
corners' are zero or negative (``-A/12`` on a flat QUAD8), as they are in the solver.
"""

from __future__ import annotations

import numpy as np

# Gauss points on [-1, 1]: three, exact to degree 5
_G3 = (np.array([-np.sqrt(0.6), 0.0, np.sqrt(0.6)]), np.array([5.0, 8.0, 5.0]) / 9.0)

# Dunavant's 6-point rule on the unit triangle (area coordinates, weights summing to 1), exact to degree 4
_A, _B = 0.445948490915965, 0.091576213509771
_WA, _WB = 0.223381589678011, 0.109951743655322
_TRI6_POINTS = np.array([(_A, _A), (1 - 2 * _A, _A), (_A, 1 - 2 * _A), (_B, _B), (1 - 2 * _B, _B), (_B, 1 - 2 * _B)])
_TRI6_WEIGHTS = np.array([_WA, _WA, _WA, _WB, _WB, _WB])

_QUAD_CORNERS = np.array([(-1, -1), (1, -1), (1, 1), (-1, 1)], dtype=float)
_QUAD_MIDS = np.array([(0, -1), (1, 0), (0, 1), (-1, 0)], dtype=float)


def _shape(n: int, r: float, s: float) -> np.ndarray:
    if n == 3:
        return np.array([1 - r - s, r, s])
    if n == 6:
        l1, l2, l3 = 1 - r - s, r, s
        return np.array(
            [(2 * l1 - 1) * l1, (2 * l2 - 1) * l2, (2 * l3 - 1) * l3, 4 * l1 * l2, 4 * l2 * l3, 4 * l3 * l1]
        )
    if n == 4:
        return 0.25 * (1 + _QUAD_CORNERS[:, 0] * r) * (1 + _QUAD_CORNERS[:, 1] * s)
    if n == 8:
        xi, eta = _QUAD_CORNERS[:, 0], _QUAD_CORNERS[:, 1]
        corners = 0.25 * (1 + xi * r) * (1 + eta * s) * (xi * r + eta * s - 1)
        mx, my = _QUAD_MIDS[:, 0], _QUAD_MIDS[:, 1]
        mids = np.where(mx == 0, 0.5 * (1 - r * r) * (1 + my * s), 0.5 * (1 + mx * r) * (1 - s * s))
        return np.concatenate([corners, mids])
    raise ValueError(f"a face of {n} nodes is none of TRI3, QUAD4, TRI6, QUAD8")


def _points(n: int):
    if n in (3, 6):
        return _TRI6_POINTS, 0.5 * _TRI6_WEIGHTS  # the unit triangle's area is 1/2
    g, w = _G3
    pts = np.array([(a, b) for a in g for b in g])
    return pts, np.array([wa * wb for wa in w for wb in w])


def face_weights(coords) -> np.ndarray:
    """``integral(N_i dA)`` for each node of a face with these node coordinates (``(n, 3)``, the order above)."""
    x = np.asarray(coords, dtype=float)
    n = len(x)
    pts, wts = _points(n)
    h = 1e-2  # central differences are exact for the at most quadratic map of these faces; a wide step rounds least
    out = np.zeros(n)
    for (r, s), w in zip(pts, wts):
        dr = (_shape(n, r + h, s) - _shape(n, r - h, s)) @ x / (2 * h)
        ds = (_shape(n, r, s + h) - _shape(n, r, s - h)) @ x / (2 * h)
        out += w * float(np.linalg.norm(np.cross(dr, ds))) * _shape(n, r, s)
    return out
