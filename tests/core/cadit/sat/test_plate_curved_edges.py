"""A flat plate's curved boundary edges must not collapse to their chords.

``PlateFactory.get_points`` keeps only edge ENDPOINTS, so a plate whose boundary follows a curve
(a deck plate meeting a curved hull skin) rendered as a straight chord between its corners while the
skin beside it curved. ``Config().sat_plate_curved_edges`` samples the curve into extra outline
points. See dap plan/v3/notes_plate_bspline_edges.md for why this is a stopgap.

Synthetic fixtures: the real reproducer (OP1_v1007_hullskin.xml, face FACE00004482 — a 0.072 m bulge
flattened out of a 1.4 m plate) is a client model and is not committed.
"""

from __future__ import annotations

import numpy as np
import pytest

from ada.cadit.sat.read.plate_edge_curves import _clip_to_endpoints, _de_boor


def _circle_ring(n=2048, r=1.0):
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return [(float(r * np.cos(x)), float(r * np.sin(x)), 0.0) for x in t]


def test_clip_closed_takes_the_short_arc():
    """A plate boundary edge is the short way round a ring, never the long way."""
    ring = _circle_ring()
    a, b = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)  # a quarter turn apart
    inner = _clip_to_endpoints(ring, a, b, n=24, closed=True)
    assert inner, "expected interior points along the arc"
    # Every point must be on the r=1 circle and inside the FIRST quadrant (the short arc).
    arr = np.array(inner)
    assert np.allclose(np.linalg.norm(arr[:, :2], axis=1), 1.0, atol=1e-6)
    assert (arr[:, 0] >= -1e-9).all() and (arr[:, 1] >= -1e-9).all()
    # ...and the walk must be monotone from a to b, i.e. short arc length ~ pi/2, not 3*pi/2.
    walk = np.vstack([[a], arr, [b]])
    assert np.linalg.norm(np.diff(walk, axis=0), axis=1).sum() == pytest.approx(np.pi / 2, rel=0.02)


def test_clip_open_never_wraps():
    """Regression: treating an OPEN curve's samples as a ring wraps from its end back to its start.

    That inserted a jump straight across the plate — measured on the real model as a 1.79 m step on
    a 1.4 m plate (perimeter 8.86 m instead of ~5 m), producing a bow-tie the collinear pruner then
    collapsed. An open curve must be SLICED, never taken modulo.
    """
    # An open polyline: a straight run in x. a/b are interior, so a wrapping impl would jump.
    pts = [(float(x), 0.0, 0.0) for x in np.linspace(0.0, 10.0, 101)]
    a, b = (2.0, 0.0, 0.0), (4.0, 0.0, 0.0)
    inner = _clip_to_endpoints(pts, a, b, n=24, closed=False)
    arr = np.array(inner)
    assert len(arr), "expected interior points"
    # Strictly between a and b: nothing from outside [2, 4] may appear.
    assert arr[:, 0].min() > 2.0 - 1e-9 and arr[:, 0].max() < 4.0 + 1e-9
    # Monotone increasing => no wrap-around jump.
    assert (np.diff(arr[:, 0]) > 0).all()
    walk = np.vstack([[a], arr, [b]])
    steps = np.linalg.norm(np.diff(walk, axis=0), axis=1)
    assert steps.sum() == pytest.approx(2.0, rel=1e-6)  # the true span, not a trip via the ends


def test_clip_open_handles_reversed_direction():
    pts = [(float(x), 0.0, 0.0) for x in np.linspace(0.0, 10.0, 101)]
    inner = _clip_to_endpoints(pts, (4.0, 0.0, 0.0), (2.0, 0.0, 0.0), n=24, closed=False)
    arr = np.array(inner)
    assert (np.diff(arr[:, 0]) < 0).all(), "must run near->far, i.e. descending here"


def test_de_boor_matches_a_known_bezier():
    """degree-3 with clamped knots and 4 control points IS a cubic Bezier — check against it."""
    cp = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [1.0, 0.0, 0.0]])
    knots = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=float)
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        got = _de_boor(float(t), knots, cp, 3)
        m = 1.0 - t
        want = m**3 * cp[0] + 3 * m**2 * t * cp[1] + 3 * m * t**2 * cp[2] + t**3 * cp[3]
        assert np.allclose(got, want, atol=1e-12), f"t={t}: {got} != {want}"


def test_de_boor_straight_spline_is_straight():
    """A spline whose control points are collinear must sample to a line — this is why the two
    'intcurve' edges on the real plate correctly contribute nothing (sagitta 0.0)."""
    cp = np.array([[float(i), 0.0, 0.0] for i in range(5)])
    knots = np.array([0, 0, 0, 0, 1, 2, 2, 2, 2], dtype=float)
    pts = np.array([_de_boor(float(t), knots, cp, 3) for t in np.linspace(0, 2, 20)])
    assert np.allclose(pts[:, 1:], 0.0, atol=1e-12)
