"""Analytic evaluation of an IFC4x3 alignment directrix into a sampled sweep frame field.

Consumes the native ``ada.geom`` alignment types (FixedReferenceSweptAreaSolid over a GradientCurve
whose base is a CompositeCurve of CurveSegments with Line/Circle/Clothoid parents) and produces, for
the NGEOM ``FIXED_REF_SWEPT_SOLID`` record, a list of per-station frames ``(origin, dir_x, dir_y)``
where a profile point ``(u, v)`` maps to ``origin + u*dir_x + v*dir_y``.

The math (validated to ~1e-7 vs the ifcopenshell oracle in prototypes/ngeom_compare/align_*.py):
horizontal line/clothoid(Fresnel)/arc segments with the CurveSegment rigid transform, composed with
the vertical gradient z(s), then a fixed-reference frame (no Frenet roll). No OCC.

NOTE: the clothoid uses a power-series Fresnel valid for |t| <~ 2 (alignment clothoids stay small);
a wider-range rational Fresnel is only needed if a producer emits very long spirals.
"""

from __future__ import annotations

import math

import numpy as np

from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so


def _fresnel(t: float) -> tuple[float, float]:
    """Normalized Fresnel S(t)=∫₀ᵗ sin(πu²/2)du, C(t)=∫₀ᵗ cos(πu²/2)du via power series."""
    hp = math.pi / 2.0
    t4 = t**4
    C = 0.0
    term = t
    n = 0
    while True:
        c = term / (4 * n + 1)
        C += c
        if abs(c) < 1e-18 and n > 2:
            break
        term *= -(hp * hp) * t4 / ((2 * n + 1) * (2 * n + 2))
        n += 1
        if n > 200:
            break
    S = 0.0
    term = hp * t**3
    n = 0
    while True:
        s = term / (4 * n + 3)
        S += s
        if abs(s) < 1e-18 and n > 2:
            break
        term *= -(hp * hp) * t4 / ((2 * n + 2) * (2 * n + 3))
        n += 1
        if n > 200:
            break
    return S, C


def _parent_eval(curve, p: float) -> tuple[np.ndarray, np.ndarray]:
    """Parent curve at arc length ``p`` (its own frame) -> (point2d, unit tangent2d)."""
    if isinstance(curve, geo_cu.Line):
        # IfcLine parents in alignments are unit-magnitude direction at the origin.
        return np.array([p, 0.0]), np.array([1.0, 0.0])
    if isinstance(curve, geo_cu.Circle):
        r = float(curve.radius)
        th = p / r
        return (np.array([r * math.cos(th), r * math.sin(th)]), np.array([-math.sin(th), math.cos(th)]))
    if isinstance(curve, geo_cu.Clothoid):
        A = float(curve.clothoid_constant)
        scale = abs(A) * math.sqrt(math.pi)
        if scale == 0.0:
            return np.array([p, 0.0]), np.array([1.0, 0.0])
        t = p / scale
        S, C = _fresnel(t)
        sgn = math.copysign(1.0, A)
        ph = math.pi * t * t / 2.0
        tg = np.array([math.cos(ph), sgn * math.sin(ph)])
        return (np.array([scale * C, sgn * scale * S]), tg / np.hypot(*tg))
    raise NotImplementedError(f"alignment parent curve {type(curve).__name__}")


def _rot2d(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """2x2 rotation taking unit vector ``a`` -> unit vector ``b``."""
    c = a[0] * b[0] + a[1] * b[1]
    s = a[0] * b[1] - a[1] * b[0]
    return np.array([[c, -s], [s, c]])


def _seg_eval(seg: geo_cu.CurveSegment, local_len: float) -> tuple[np.ndarray, np.ndarray]:
    """Global (point2d, unit tangent2d) at ``local_len`` ∈ [0, |segment_length|] along ``seg``.

    Maps the parent (point, tangent) at SegmentStart to the segment's placement (location, refdir);
    advances arc length in the sign of SegmentLength.
    """
    length = float(seg.segment_length)
    sgn = 1.0 if length >= 0 else -1.0
    p = float(seg.segment_start) + sgn * local_len
    P, T = _parent_eval(seg.parent_curve, p)
    P0, T0 = _parent_eval(seg.parent_curve, float(seg.segment_start))
    if sgn < 0:
        T, T0 = -T, -T0
    origin = np.asarray(seg.location, dtype=float)[:2]
    refdir = np.asarray(seg.ref_direction, dtype=float)[:2]
    refdir = refdir / np.hypot(*refdir)
    R = _rot2d(T0 / np.hypot(*T0), refdir)
    gp = origin + R @ (P - P0)
    gt = R @ T
    return gp, gt / np.hypot(*gt)


def _curve_segments(curve) -> list[geo_cu.CurveSegment]:
    """The CurveSegment list of a CompositeCurve / GradientCurve base (skip zero-length terminators)."""
    segs = curve.segments if isinstance(curve, (geo_cu.CompositeCurve, geo_cu.GradientCurve)) else curve
    return [s for s in segs if isinstance(s, geo_cu.CurveSegment) and abs(float(s.segment_length)) > 1e-9]


def _sample_planar(segs: list[geo_cu.CurveSegment], n_per: int) -> tuple[np.ndarray, np.ndarray]:
    """Sample each segment -> (cumulative arc length s, 2D global point) stacked over all segments."""
    s_vals, pts = [], []
    s_acc = 0.0
    for seg in segs:
        L = abs(float(seg.segment_length))
        for i in range(n_per + 1):
            ll = L * i / n_per
            gp, _ = _seg_eval(seg, ll)
            s_vals.append(s_acc + ll)
            pts.append(gp)
        s_acc += L
    return np.array(s_vals), np.array(pts)


def directrix_frames(
    solid: geo_so.FixedReferenceSweptAreaSolid, n_per: int = 300
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(origins, dir_x, dir_y) per station for the sweep. ``dir_x`` = profile local-x in 3D (the
    fixed-reference "up"), ``dir_y`` = profile local-y in 3D (lateral). ``n_per`` samples per
    horizontal segment."""
    directrix = solid.directrix
    if not isinstance(directrix, geo_cu.GradientCurve):
        raise NotImplementedError(f"directrix {type(directrix).__name__} (only GradientCurve supported)")

    hsegs = _curve_segments(directrix.base_curve)
    s, hxy = _sample_planar(hsegs, n_per)  # (s,), (s,2)

    # vertical gradient -> z(s) by interpolation on its (distance, height) samples
    vsegs = _curve_segments(directrix)
    _, vdh = _sample_planar(vsegs, max(n_per, 50))
    order = np.argsort(vdh[:, 0])
    z = np.interp(s, vdh[order, 0], vdh[order, 1])

    pts = np.column_stack([hxy[:, 0], hxy[:, 1], z])

    F = np.asarray(solid.fixed_reference, dtype=float)
    F = F / np.linalg.norm(F)
    T = np.gradient(pts, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    lateral = np.cross(T, F)
    lateral /= np.linalg.norm(lateral, axis=1, keepdims=True)
    up = np.cross(lateral, T)
    up /= np.linalg.norm(up, axis=1, keepdims=True)
    return pts, up, lateral
