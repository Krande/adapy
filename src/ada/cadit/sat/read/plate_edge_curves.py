"""Discretize a flat plate's CURVED boundary edges into extra outline points.

WHY THIS EXISTS (and why it is a stopgap, not the right answer)
--------------------------------------------------------------
``PlateFactory.get_points`` walks a SAT face's coedges and keeps only the edge
ENDPOINTS. Whatever the edge's curve does between its two vertices is discarded,
so a plate whose boundary follows a spline (e.g. a deck plate meeting a curved
hull skin) comes out as a straight chord between its corners. Measured on
``OP1_v1007_hullskin.xml`` face ``FACE00004482``: 4 coedges — 1 straight, 2
``intcurve``, 1 ``ellipse`` — collapsed to a 4-point polygon.

The loss is doubled by the target type: ``CurvePoly2d`` (the ``Plate`` outline) is
documented as *"a closed curve defined by a list of 2d points represented by line
and arc segments"* and its API is ``segments() -> list[LineSegment | ArcSegment]``.
There is no B-spline segment, so even a perfectly-read spline edge has nowhere to
live on a ``Plate``.

So this module does the cheap thing: SAMPLE the curve and emit the samples as
extra straight-segment outline points. The plate then visually follows the curve.

WHAT THIS DOES NOT FIX. The samples are ours, not the neighbouring face's. The
adjacent ``curved_shell`` keeps its real spline (it goes through ``AdvancedFace``),
so the two faces still do not share edge points and the seam is still not
watertight — it just looks right. A real fix is B-spline edge support in ``Plate``
/ ``CurvePoly2d``, or routing curved-boundary flat plates through ``AdvancedFace``
like the curved shells. See dap ``plan/v3/notes_plate_bspline_edges.md``.
"""

from __future__ import annotations

import numpy as np

from ada.config import logger

# Samples per curved edge. A straight-ish edge collapses back to its chord in
# remove_near_collinear_points (tol 1e-8*scale^2), so over-sampling a nearly
# straight edge costs nothing; under-sampling a tight one is visible.
DEFAULT_CURVE_SAMPLES = 24
# Ring resolution for ellipse/circle sampling before clipping to the edge's arc.
_ELLIPSE_RING_SAMPLES = 2048


def _de_boor(x: float, knots, cp, deg: int):
    """Evaluate a B-spline at parameter x (de Boor). numpy only — scipy is NOT a core dep.

    `cp` may carry a 4th homogeneous component (rational weights); the caller divides through.
    """
    # Knot span: last index k with knots[k] <= x < knots[k+1], clamped to the valid range.
    k = int(np.searchsorted(knots, x, side="right") - 1)
    k = max(deg, min(k, len(cp) - 1))
    d = [cp[j + k - deg].copy() for j in range(deg + 1)]
    for r in range(1, deg + 1):
        for j in range(deg, r - 1, -1):
            lo = knots[j + k - deg]
            hi = knots[j + 1 + k - r]
            den = hi - lo
            a = 0.0 if den == 0 else (x - lo) / den
            d[j] = (1.0 - a) * d[j - 1] + a * d[j]
    return d[deg]


def _bspline_points(curve, n: int) -> list[tuple[float, float, float]] | None:
    """Sample a BSplineCurveWithKnots at n params across its knot span."""
    try:
        cp = np.asarray([list(p)[:3] for p in curve.control_points_list], dtype=float)
        # SAT/IFC store knots + multiplicities separately; de Boor wants them expanded.
        knots = np.repeat(np.asarray(curve.knots, dtype=float), np.asarray(curve.knot_multiplicities, dtype=int))
        deg = int(curve.degree)
        if deg < 1 or len(cp) <= deg or len(knots) != len(cp) + deg + 1:
            # Malformed (or a form we don't model) — fall back to the chord.
            return None
        # Rational curves: weight the control points, then divide through after evaluation.
        w = getattr(curve, "weights_data", None) or getattr(curve, "weights", None)
        if w is not None and len(w) == len(cp):
            wa = np.asarray(w, dtype=float).reshape(-1, 1)
            cp = np.hstack([cp * wa, wa])
        lo, hi = float(knots[deg]), float(knots[len(knots) - deg - 1])
        if not np.isfinite([lo, hi]).all() or hi <= lo:
            return None
        out = []
        for x in np.linspace(lo, hi, n):
            p = _de_boor(float(x), knots, cp, deg)
            if p.shape[0] == 4:  # rational: de-homogenize
                if p[3] == 0:
                    return None
                p = p[:3] / p[3]
            out.append(tuple(float(v) for v in p[:3]))
        arr = np.asarray(out)
        if not np.isfinite(arr).all():
            return None
        return out
    except Exception as exc:  # noqa: BLE001 - a curve we can't sample falls back to the chord
        logger.debug(f"bspline sample failed: {exc}")
        return None


def _ellipse_points(curve, n: int) -> list[tuple[float, float, float]] | None:
    """Sample a full Ellipse/Circle. Trimming to the edge happens in _clip_to_endpoints."""
    try:
        c = np.asarray(list(curve.position.location)[:3], dtype=float)
        z = np.asarray(list(curve.position.axis)[:3], dtype=float)
        x = np.asarray(list(curve.position.ref_direction)[:3], dtype=float)
        x = x / np.linalg.norm(x)
        z = z / np.linalg.norm(z)
        y = np.cross(z, x)
        ra = float(getattr(curve, "semi_axis1", getattr(curve, "radius", 0.0)))
        rb = float(getattr(curve, "semi_axis2", getattr(curve, "radius", 0.0)))
        if ra <= 0 or rb <= 0:
            return None
        # Sample the FULL ring densely, not n*4: the edge is usually a small arc of it (measured:
        # a 1.46 m edge spanning ~5 of 96 ring samples), so ring density must be high enough that
        # the clipped arc still carries ~n points. 2048 vectorised points is free.
        t = np.linspace(0.0, 2.0 * np.pi, _ELLIPSE_RING_SAMPLES, endpoint=False)
        pts = c + np.outer(ra * np.cos(t), x) + np.outer(rb * np.sin(t), y)
        return [tuple(float(v) for v in p) for p in pts]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"ellipse sample failed: {exc}")
        return None


def _clip_to_endpoints(pts, a, b, n: int, closed: bool) -> list[tuple[float, float, float]]:
    """Keep the run of `pts` from vertex a to vertex b, thinned to ~n interior points.

    The curve records span the FULL basis curve (a documented ACIS trait: pcurves span the whole
    curve and are trimmed by projecting the vertices), so a sampled curve generally overshoots the
    edge. Snap to the closest sample to each vertex and keep the span between them.

    `closed` matters and is not cosmetic. For an ellipse/circle the sample ring wraps, and the edge
    is the SHORTER of the two arcs. For an open B-spline there is no wrap: indices must be sliced,
    never taken modulo — doing so walks off the end of the curve and back to its start, which
    inserts a jump straight across the plate (measured: a 1.79 m step on a 1.4 m plate, perimeter
    8.86 m instead of ~5 m, and the resulting bow-tie polygon collapsed under the collinear pruner).
    """
    arr = np.asarray(pts, dtype=float)
    ia = int(np.argmin(np.linalg.norm(arr - np.asarray(a, dtype=float), axis=1)))
    ib = int(np.argmin(np.linalg.norm(arr - np.asarray(b, dtype=float), axis=1)))
    if ia == ib:
        return []
    m = len(arr)

    def arclen(idx):
        return float(np.linalg.norm(np.diff(arr[idx], axis=0), axis=1).sum())

    if closed:
        fwd = [(ia + k) % m for k in range((ib - ia) % m + 1)]
        bwd = [(ia - k) % m for k in range((ia - ib) % m + 1)]
        idx = fwd if arclen(fwd) <= arclen(bwd) else bwd
    else:
        idx = list(range(ia, ib + 1)) if ia < ib else list(range(ia, ib - 1, -1))
    if len(idx) <= 2:
        return []
    # Drop the endpoints (the caller already has the exact vertices) and thin to ~n.
    inner = idx[1:-1]
    if len(inner) > n:
        sel = np.linspace(0, len(inner) - 1, n).round().astype(int)
        inner = [inner[i] for i in sorted(set(sel.tolist()))]
    return [tuple(float(v) for v in arr[i]) for i in inner]


def edge_interior_points(coedge, sat_store, a, b, n: int = DEFAULT_CURVE_SAMPLES) -> list[tuple[float, float, float]]:
    """Interior 3D points along `coedge`'s curve, ordered from vertex `a` to vertex `b`.

    Returns [] for straight edges, unreadable curves, or anything we can't sample —
    the caller then keeps today's chord, so this can only add detail, never lose a plate.
    """
    # Local import: ada.cadit.sat.read.curves imports from this package's siblings.
    from ada.cadit.sat.read.curves import get_edge

    try:
        oe = get_edge(coedge)
    except Exception as exc:  # noqa: BLE001 - unreadable curve => keep the chord
        logger.debug(f"edge_interior_points: get_edge failed: {exc}")
        return []
    curve = getattr(oe, "edge_element", None) or getattr(oe, "edge_geometry", None) or oe
    curve = getattr(curve, "edge_geometry", curve)

    from ada.geom.curves import BSplineCurveWithKnots, Circle, Ellipse, Line

    if isinstance(curve, Line):
        return []
    pts = None
    closed = False
    if isinstance(curve, BSplineCurveWithKnots):
        pts = _bspline_points(curve, max(n * 4, 32))  # open: sampled across the knot span
    elif isinstance(curve, (Ellipse, Circle)):
        pts = _ellipse_points(curve, n)
        closed = True  # sampled as a full ring; the edge is an arc of it
    if not pts:
        return []
    return _clip_to_endpoints(pts, a, b, n, closed)
