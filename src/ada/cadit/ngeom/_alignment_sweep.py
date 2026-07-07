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


def _cosine_spiral_theta(curve: geo_cu.CosineSpiral, L: float, s: np.ndarray) -> np.ndarray:
    """Heading angle theta(s) = s/A0 + (L/(pi*A1))*sin(pi*s/L) for an IfcCosineSpiral (A1 =
    CosineTerm, A0 = ConstantTerm, L = the containing segment length). Validated to ~1e-7 vs the
    ifcopenshell oracle on the segmented-reference-curve fixture."""
    A1 = float(curve.cosine_term)
    A0 = float(curve.constant_term) if curve.constant_term else None
    th = np.zeros_like(s, dtype=float)
    if A0:
        th = th + s / A0
    if L > 0.0 and A1 != 0.0:
        th = th + (L / (math.pi * A1)) * np.sin(math.pi * s / L)
    return th


def _parent_eval(curve, p: float, seg_length: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Parent curve at arc length ``p`` (its own frame) -> (point2d, unit tangent2d).

    ``seg_length`` is the length of the containing CurveSegment — required by transcendental
    spirals (IfcCosineSpiral) whose heading uses the total length L; ignored by the closed-form
    parents (line/arc/clothoid)."""
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
    if isinstance(curve, geo_cu.CosineSpiral):
        # No closed form: integrate (cos theta, sin theta) from 0 to p (theta closed-form).
        L = abs(float(seg_length)) if seg_length else 0.0
        n = max(32, int(abs(p) / 0.1) + 1)
        xs = np.linspace(0.0, p, n)
        th = _cosine_spiral_theta(curve, L, xs)
        dx = np.diff(xs)
        cx, cy = np.cos(th), np.sin(th)
        px = float(np.sum((cx[:-1] + cx[1:]) * 0.5 * dx))
        py = float(np.sum((cy[:-1] + cy[1:]) * 0.5 * dx))
        thp = float(_cosine_spiral_theta(curve, L, np.array([p]))[0])
        return np.array([px, py]), np.array([math.cos(thp), math.sin(thp)])
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
    P, T = _parent_eval(seg.parent_curve, p, seg_length=length)
    P0, T0 = _parent_eval(seg.parent_curve, float(seg.segment_start), seg_length=length)
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
    segs = (
        curve.segments
        if isinstance(curve, (geo_cu.CompositeCurve, geo_cu.GradientCurve, geo_cu.SegmentedReferenceCurve))
        else curve
    )
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


def gradient_curve_points_with_s(directrix: geo_cu.GradientCurve, n_per: int = 300) -> tuple[np.ndarray, np.ndarray]:
    """(arc length s, 3D polyline) of a GradientCurve: horizontal alignment (base_curve, sampled
    per segment) with the vertical gradient's height interpolated over arc length."""
    hsegs = _curve_segments(directrix.base_curve)
    s, hxy = _sample_planar(hsegs, n_per)  # (s,), (s,2)

    # vertical gradient -> z(s) by interpolation on its (distance, height) samples
    vsegs = _curve_segments(directrix)
    if vsegs:
        _, vdh = _sample_planar(vsegs, max(n_per, 50))
        order = np.argsort(vdh[:, 0])
        z = np.interp(s, vdh[order, 0], vdh[order, 1])
    else:
        z = np.zeros_like(s)

    return s, np.column_stack([hxy[:, 0], hxy[:, 1], z])


def gradient_curve_points(directrix: geo_cu.GradientCurve, n_per: int = 300) -> np.ndarray:
    """Sampled 3D polyline of a GradientCurve (see gradient_curve_points_with_s). Clothoid
    segments have no analytic B-rep form, so exports/sweeps consume this sampling."""
    return gradient_curve_points_with_s(directrix, n_per)[1]


def composite_curve_points(curve: geo_cu.CompositeCurve, n_per: int = 300) -> np.ndarray:
    """Sampled 3D polyline of a horizontal CompositeCurve (z = 0)."""
    _, xy = _sample_planar(_curve_segments(curve), n_per)
    return np.column_stack([xy[:, 0], xy[:, 1], np.zeros(len(xy))])


def _cant_offset(seg: geo_cu.CurveSegment, s_rel: np.ndarray) -> np.ndarray | None:
    """Vertical superelevation offset of one IfcSegmentedReferenceCurve cant segment at base-curve
    arc lengths ``s_rel`` measured from the segment start. For a cosine-spiral cant this is the
    closed form ``e0 + (L^2/CosineTerm)*(cos(pi*s/L) - 1)`` where e0 is the vertical component of
    the 3D segment placement (validated to machine precision vs the ifcopenshell oracle). Returns
    ``None`` for parent kinds not handled analytically (caller keeps the base-curve z)."""
    L = abs(float(seg.segment_length))
    loc = np.asarray(seg.location, dtype=float)
    e0 = float(loc[1]) if loc.size >= 2 else 0.0  # local-y of the 3D placement -> vertical offset
    parent = seg.parent_curve
    if isinstance(parent, geo_cu.CosineSpiral) and L > 0.0 and float(parent.cosine_term) != 0.0:
        amp = L * L / float(parent.cosine_term)
        return e0 + amp * (np.cos(math.pi * s_rel / L) - 1.0)
    if isinstance(parent, geo_cu.Line):
        # A line-parent cant segment is a constant superelevation (no transition).
        return np.full_like(s_rel, e0)
    return None


def segmented_reference_curve_points(curve: geo_cu.SegmentedReferenceCurve, n_per: int = 300) -> np.ndarray:
    """Sampled 3D polyline of an IfcSegmentedReferenceCurve: the base GradientCurve's (x,y,z) with
    the cant segments' vertical offset added. The horizontal geometry is the base curve's; only z
    is displaced by the superelevation."""
    base = curve.base_curve
    if isinstance(base, geo_cu.GradientCurve):
        s, pts = gradient_curve_points_with_s(base, n_per)
    elif isinstance(base, geo_cu.CompositeCurve):
        pts = composite_curve_points(base, n_per)
        s, _ = _sample_planar(_curve_segments(base), n_per)
    else:
        raise NotImplementedError(f"segmented-reference base {type(base).__name__}")

    dz = np.zeros(len(s))
    for seg in _curve_segments(curve):
        s0 = float(seg.segment_start)
        L = abs(float(seg.segment_length))
        mask = (s >= s0 - 1e-9) & (s <= s0 + L + 1e-9)
        off = _cant_offset(seg, s[mask] - s0)
        if off is not None:
            dz[mask] = off
    pts = pts.copy()
    pts[:, 2] += dz
    return pts


def curve_to_polyline(curve, n_per: int = 300) -> np.ndarray:
    """Sample any supported alignment curve container to a 3D polyline (N,3). Dispatches over
    SegmentedReferenceCurve / GradientCurve / CompositeCurve / a bare CurveSegment."""
    if isinstance(curve, geo_cu.SegmentedReferenceCurve):
        return segmented_reference_curve_points(curve, n_per)
    if isinstance(curve, geo_cu.GradientCurve):
        return gradient_curve_points(curve, n_per)
    if isinstance(curve, geo_cu.CompositeCurve):
        return composite_curve_points(curve, n_per)
    if isinstance(curve, geo_cu.CurveSegment):
        _, xy = _sample_planar([curve], n_per)
        return np.column_stack([xy[:, 0], xy[:, 1], np.zeros(len(xy))])
    raise NotImplementedError(f"alignment curve container {type(curve).__name__}")


def directrix_frames(
    solid: geo_so.FixedReferenceSweptAreaSolid, n_per: int = 300
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(origins, dir_x, dir_y) per station for the sweep. ``dir_x`` = profile local-x in 3D (the
    fixed-reference "up"), ``dir_y`` = profile local-y in 3D (lateral). ``n_per`` samples per
    horizontal segment."""
    directrix = solid.directrix
    if not isinstance(directrix, geo_cu.GradientCurve):
        raise NotImplementedError(f"directrix {type(directrix).__name__} (only GradientCurve supported)")

    pts = gradient_curve_points(directrix, n_per)

    F = np.asarray(solid.fixed_reference, dtype=float)
    F = F / np.linalg.norm(F)
    T = np.gradient(pts, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    lateral = np.cross(T, F)
    lateral /= np.linalg.norm(lateral, axis=1, keepdims=True)
    up = np.cross(lateral, T)
    up /= np.linalg.norm(up, axis=1, keepdims=True)
    return pts, up, lateral
