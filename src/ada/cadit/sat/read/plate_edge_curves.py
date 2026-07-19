"""Discretize a flat plate's CURVED boundary edges into extra outline points.

WHY THIS EXISTS (and why it is a stopgap, not the right answer)
--------------------------------------------------------------
``PlateFactory.get_points`` walks a SAT face's coedges and keeps only the edge
ENDPOINTS. Whatever the edge's curve does between its two vertices is discarded,
so a plate whose boundary follows a spline (e.g. a deck plate meeting a curved
hull skin) comes out as a straight chord between its corners. Measured on
``a hull-skin Genie-XML model`` face ``FACE00004482``: 4 coedges — 1 straight, 2
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
like the curved shells. See the internal design notes.
"""

from __future__ import annotations

import numpy as np

from ada.config import logger

# Samples per curved edge. A straight-ish edge collapses back to its chord in
# remove_near_collinear_points (tol 1e-8*scale^2), so over-sampling a nearly
# straight edge costs nothing; under-sampling a tight one is visible.
DEFAULT_CURVE_SAMPLES = 24


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
    """Sample a ``BSplineCurveWithKnots`` at n params across its knot span, or None if unsamplable.

    Thin wrapper over :meth:`ada.geom.curves.BSplineCurveWithKnots.sample` (the single home of the de
    Boor evaluator) that keeps this module's "unreadable curve => keep the chord" contract by returning
    None instead of raising.
    """
    try:
        return curve.sample(n)
    except Exception as exc:  # noqa: BLE001 - a curve we can't sample falls back to the chord
        logger.debug(f"bspline sample failed: {exc}")
        return None


def _ellipse_arc_points(curve, a, b, n: int) -> list[tuple[float, float, float]] | None:
    """Interior points along the Ellipse/Circle ARC from vertex `a` to vertex `b`.

    Analytic clip: a point on the ellipse is ``p = c + ra*cos(t)*x + rb*sin(t)*y``, so each vertex's
    parameter angle ``t`` is recovered by projecting ``p - c`` onto the (x, y) frame. We then sample
    the SHORT arc between the two angles directly — ~n points on the actual edge — instead of the old
    2048-point full ring that ``_clip_to_endpoints`` immediately threw all but ~n of away (~27s of the
    hull-skin import). Endpoints are dropped: the caller already holds the exact vertices.
    """
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

        def _angle(p) -> float:
            d = np.asarray(p, dtype=float) - c
            return float(np.arctan2(float(d @ y) / rb, float(d @ x) / ra))

        ta = _angle(a)
        # The edge is the short arc: sweep the signed angular delta wrapped into (-pi, pi]. A plate
        # boundary edge is always the shorter way round its ring (measured: ~5 of 96 samples), and for
        # such small sweeps short-angle == short-arc-length for an ellipse too, so this matches the
        # old arclength-based clip without sampling the full ring.
        delta = (_angle(b) - ta + np.pi) % (2.0 * np.pi) - np.pi
        if abs(delta) < 1e-12:
            return []
        ts = ta + delta * np.linspace(0.0, 1.0, n + 2)[1:-1]
        pts = c + np.outer(ra * np.cos(ts), x) + np.outer(rb * np.sin(ts), y)
        return [tuple(p) for p in pts.tolist()]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"ellipse arc sample failed: {exc}")
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


def edge_curve_descriptor(coedge, sat_store, a, b, n: int = DEFAULT_CURVE_SAMPLES):
    """Analytic descriptor for a curved coedge from vertex `a` to vertex `b`, or None.

    - ``("arc", midpoint)`` — circle/ellipse: the point on the arc halfway between `a` and `b`.
    - ``("spline", curve)`` — B-spline: the analytic ``ada.geom.curves.BSplineCurveWithKnots`` itself.
    - ``None`` — straight, unreadable, or unsupported: the caller keeps the chord.

    The caller carries the payload as an :class:`~ada.api.curves.ArcEdge` / ``SplineEdge`` so the plate
    keeps a real analytic segment (arc exact in IFC/STEP; spline analytic in OCC/IFC, discretized in
    NGEOM/STEP) instead of being sampled into straight outline points at read time.
    """
    # Local import: ada.cadit.sat.read.curves imports from this package's siblings.
    from ada.cadit.sat.read.curves import get_edge

    try:
        oe = get_edge(coedge)
    except Exception as exc:  # noqa: BLE001 - unreadable curve => keep the chord
        logger.debug(f"edge_curve_descriptor: get_edge failed: {exc}")
        return None
    curve = getattr(oe, "edge_element", None) or getattr(oe, "edge_geometry", None) or oe
    curve = getattr(curve, "edge_geometry", curve)

    from ada.geom.curves import BSplineCurveWithKnots, Circle, Ellipse, Line

    if isinstance(curve, Line):
        return None
    if isinstance(curve, (Ellipse, Circle)):
        mid = _ellipse_arc_points(curve, a, b, 1)  # n=1 -> the single arc midpoint
        if not mid:
            return None
        return ("arc", mid[0])
    if isinstance(curve, BSplineCurveWithKnots):
        # Sanity-check the spec is samplable before committing to the analytic edge; else keep the chord.
        try:
            curve.sample(2)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"edge_curve_descriptor: unsamplable b-spline ({exc})")
            return None
        return ("spline", curve)
    return None
    return []
