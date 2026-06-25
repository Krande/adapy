"""OCC-free discretization of ada.geom curves into 3D polylines.

A straight segment is exactly its endpoints; an arc/circle is sampled by chord deflection
(``angle_step`` — the same curvature-driven density OCC's ``GCPnts_*Deflection`` and the NGEOM
tessellator use). Lets line geometry (sectionless wire bodies, open wireframes) render without
pythonocc — important for wasm — and avoids a per-curve OCC build just to discretize.
"""

from __future__ import annotations

import numpy as np

import ada.geom.curves as cu

_MIN_ANGLE = 2.0 * np.pi / 180.0  # 2 deg floor, matches NGEOM angle_step


def _circumcenter3d(a, b, c):
    """3D circumcenter + radius of the circle through three points (None if collinear)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    c = np.asarray(c, dtype=float)
    ab = b - a
    ac = c - a
    abxac = np.cross(ab, ac)
    n2 = float(np.dot(abxac, abxac))
    if n2 < 1e-20:
        return None, None
    to_center = (np.cross(abxac, ab) * float(np.dot(ac, ac)) + np.cross(ac, abxac) * float(np.dot(ab, ab))) / (2.0 * n2)
    return a + to_center, float(np.linalg.norm(to_center))


def angle_step(radius: float, deflection: float, max_angle: float = 0.35) -> float:
    """Largest angular step (rad) keeping the chord within ``deflection`` of an arc of ``radius``,
    clamped to [2deg, max_angle]. Mirrors adacpp ngeom_surfaces angle_step / OCC deflection."""
    if radius < 1e-12:
        return max_angle
    ratio = float(np.clip(1.0 - deflection / radius, -1.0, 1.0))
    a = 2.0 * np.arccos(ratio)
    return float(np.clip(a, _MIN_ANGLE, max(max_angle, _MIN_ANGLE)))


def _arc_points(start, midpoint, end, deflection: float, max_angle: float) -> list[tuple]:
    """Sample the circular arc through (start → midpoint → end) by chord deflection."""
    p0 = np.asarray(start, dtype=float)
    pm = np.asarray(midpoint, dtype=float)
    p1 = np.asarray(end, dtype=float)
    center, radius = _circumcenter3d(p0, pm, p1)
    if center is None:  # collinear → straight chord
        return [tuple(p0), tuple(p1)]

    x = p0 - center
    rx = np.linalg.norm(x)
    normal = np.cross(pm - p0, p1 - p0)
    rn = np.linalg.norm(normal)
    if rx < 1e-12 or rn < 1e-12 or radius < 1e-12:
        return [tuple(p0), tuple(p1)]
    x = x / rx
    normal = normal / rn
    y = np.cross(normal, x)

    def _ang(p):
        d = np.asarray(p, dtype=float) - center
        return np.arctan2(float(np.dot(d, y)), float(np.dot(d, x)))

    def _wrap(a):  # → [0, 2pi)
        return a % (2.0 * np.pi)

    am = _wrap(_ang(pm))
    a1 = _wrap(_ang(p1))
    # Sweep from start (angle 0 in this basis) through mid to end; pick the direction that
    # passes the midpoint. CCW unless mid would be skipped, then CW (negative sweep).
    sweep = a1 if am < a1 or abs(a1) < 1e-12 else a1 - 2.0 * np.pi

    n = max(2, int(np.ceil(abs(sweep) / angle_step(radius, deflection, max_angle))))
    pts = []
    for i in range(n + 1):
        a = sweep * i / n
        pts.append(tuple(center + radius * (np.cos(a) * x + np.sin(a) * y)))
    return pts


def _circle_points(circle: cu.Circle, deflection: float, max_angle: float) -> list[tuple]:
    pos = circle.position
    center = np.asarray(pos.location, dtype=float)
    axis = np.asarray(pos.axis, dtype=float)
    ref = np.asarray(pos.ref_direction, dtype=float)
    radius = float(circle.radius)
    x = ref / (np.linalg.norm(ref) or 1.0)
    z = axis / (np.linalg.norm(axis) or 1.0)
    y = np.cross(z, x)
    n = max(8, int(np.ceil(2.0 * np.pi / angle_step(radius, deflection, max_angle))))
    return [
        tuple(center + radius * (np.cos(2.0 * np.pi * i / n) * x + np.sin(2.0 * np.pi * i / n) * y))
        for i in range(n + 1)  # closed loop (last == first)
    ]


def _append(out: list, pts: list) -> None:
    for p in pts:
        p = (float(p[0]), float(p[1]), float(p[2]))
        if not out or np.linalg.norm(np.subtract(out[-1], p)) > 1e-9:
            out.append(p)


def discretize_curve(curve, deflection: float = 0.1, max_angle: float = 0.35) -> list[tuple]:
    """Discretize an ada.geom curve to an ordered list of ``(x, y, z)`` points, OCC-free.

    Supported: Edge / PolyLine (straight), ArcLine + Circle (sampled), and IndexedPolyCurve
    (per-segment, straight or arc). Returns ``None`` for curve kinds without a native sampler
    (e.g. B-spline) so the caller can fall back to OCC discretization.
    """
    if type(curve) is cu.Edge:
        return [tuple(np.asarray(curve.start, float)), tuple(np.asarray(curve.end, float))]
    if type(curve) is cu.PolyLine:
        return [tuple(np.asarray(p, float)) for p in curve.points]
    if type(curve) is cu.ArcLine:
        return _arc_points(curve.start, curve.midpoint, curve.end, deflection, max_angle)
    if type(curve) is cu.Circle:
        return _circle_points(curve, deflection, max_angle)
    if type(curve) is cu.IndexedPolyCurve:
        out: list = []
        for seg in curve.segments:
            seg_pts = discretize_curve(seg, deflection, max_angle)
            if seg_pts is None:
                return None
            _append(out, seg_pts)
        return out
    return None
