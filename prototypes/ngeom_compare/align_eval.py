"""Python reference evaluator for the IFC4x3 alignment directrix (IfcGradientCurve).

Analytic eval of the horizontal IfcCompositeCurve (line / clothoid / circular arc segments) +
the vertical gradient, composed into a sampled 3D directrix with per-station frames. Validated
against the ifcopenshell.geom directrix oracle (align_oracle.py -> directrix_oracle.npy).

This is the SPEC for the adacpp C++ port (ng:: analytic eval). No OCC.

Run: pixi run -e tests-adacpp python prototypes/ngeom_compare/align_eval.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SCRATCH = "/tmp/claude-1000/-home-kristoffer-code-dap/d103bfc1-7e2e-4f7e-ba7c-8e213d35e3df/scratchpad"


def fresnel(t):
    """Normalized Fresnel integrals S(t)=int_0^t sin(pi u^2/2) du, C(t)=int_0^t cos(...).

    Power-series sum (converges fast for |t| <~ 2; this fixture's clothoid has t_max ~ 0.31).
    The adacpp C++ port should swap in a rational/auxiliary-function Fresnel (Boersma) for large
    arguments; documented as such in the spec.
    """
    # C(t) = sum_n (-1)^n (pi/2)^{2n} t^{4n+1} / ((2n)! (4n+1))
    # S(t) = sum_n (-1)^n (pi/2)^{2n+1} t^{4n+3} / ((2n+1)! (4n+3))
    hp = math.pi / 2.0
    C = 0.0
    S = 0.0
    # C terms
    term = t
    n = 0
    while True:
        c = term / (4 * n + 1)
        C += c
        # advance term: multiply by -(pi/2)^2 t^4 / ((2n+1)(2n+2))
        nt = term * (-(hp * hp) * t**4) / ((2 * n + 1) * (2 * n + 2))
        if abs(c) < 1e-18 and n > 2:
            break
        term = nt
        n += 1
        if n > 200:
            break
    # S terms
    term = hp * t**3
    n = 0
    while True:
        s = term / (4 * n + 3)
        S += s
        nt = term * (-(hp * hp) * t**4) / ((2 * n + 2) * (2 * n + 3))
        if abs(s) < 1e-18 and n > 2:
            break
        term = nt
        n += 1
        if n > 200:
            break
    return S, C


def _rot2d(from_dir, to_dir):
    """2x2 rotation taking unit vector from_dir -> unit vector to_dir."""
    fx, fy = from_dir
    tx, ty = to_dir
    # angle of to minus angle of from
    c = fx * tx + fy * ty  # cos
    s = fx * ty - fy * tx  # sin (cross)
    return np.array([[c, -s], [s, c]])


# ---- parent-curve local eval: param p = arc length -> (point2d, unit tangent2d) ----


def _line_eval(p):
    return np.array([p, 0.0]), np.array([1.0, 0.0])


def _circle_eval(p, radius):
    # IfcCircle in its placement frame: center at origin, start at (R,0), CCW.
    # arc length p -> angle th = p / R
    th = p / radius
    pt = np.array([radius * math.cos(th), radius * math.sin(th)])
    tg = np.array([-math.sin(th), math.cos(th)])  # d/dp, already unit
    return pt, tg


def _clothoid_eval(p, A):
    # arc length p along the spiral. x = |A|sqrt(pi) C(t), y = sign(A) |A|sqrt(pi) S(t),
    # t = p / (|A| sqrt(pi)). scipy.fresnel returns (S, C).
    aa = abs(A)
    scale = aa * math.sqrt(math.pi)
    if scale == 0.0:
        return np.array([p, 0.0]), np.array([1.0, 0.0])
    t = p / scale
    S, C = fresnel(t)
    pt = np.array([scale * C, math.copysign(1.0, A) * scale * S])
    # tangent: dx/dp = cos(pi t^2 /2)*... derivative of C is cos(pi t^2/2); chain dt/dp = 1/scale
    ph = math.pi * t * t / 2.0
    tg = np.array([math.cos(ph), math.copysign(1.0, A) * math.sin(ph)])
    n = np.hypot(*tg)
    return pt, tg / n


@dataclass
class Seg:
    kind: str  # 'line' | 'circle' | 'clothoid'
    origin: np.ndarray  # placement origin (2d)
    refdir: np.ndarray  # placement ref direction (2d, unit)
    start: float  # SegmentStart (arc length)
    length: float  # SegmentLength (signed)
    radius: float = 0.0
    A: float = 0.0

    def _parent(self, p):
        if self.kind == "line":
            return _line_eval(p)
        if self.kind == "circle":
            return _circle_eval(p, self.radius)
        if self.kind == "clothoid":
            return _clothoid_eval(p, self.A)
        raise ValueError(self.kind)

    def eval(self, local_len):
        """local_len in [0, abs(length)] -> global (point2d, unit tangent2d).

        Maps the parent's (point,tangent) at SegmentStart to (origin, refdir) by a rigid 2D
        transform; advances arc length in the sign of SegmentLength.
        """
        sgn = 1.0 if self.length >= 0 else -1.0
        p = self.start + sgn * local_len
        P, T = self._parent(p)
        P0, T0 = self._parent(self.start)
        if sgn < 0:
            T = -T  # traversal direction reverses
            T0 = -T0
        R = _rot2d(T0, self.refdir)
        gp = self.origin + R @ (P - P0)
        gt = R @ T
        return gp, gt / np.hypot(*gt)


# ---- the fixture's two composite curves, hand-transcribed from the IFC ----


def horizontal_segs():
    return [
        Seg("line", np.array([0.0, 0.0]), np.array([1.0, 0.0]), 0.0, 400.0),
        Seg("clothoid", np.array([400.0, 0.0]), np.array([1.0, 0.0]), 0.0, 150.0, A=-273.861278752584),
        Seg(
            "circle",
            np.array([549.662851380011, -7.48795505445]),
            np.array([9.88771077936042e-1, -1.49438132473604e-1]),
            0.0,
            -400.0,
            radius=500.000000000002,
        ),
        # #1201 terminator (length 0) omitted
    ]


def vertical_segs():
    return [
        Seg(
            "line", np.array([0.0, 150.0]), np.array([9.99999500000375e-1, -9.99999499995919e-4]), 0.0, 450.000218741065
        ),
        Seg(
            "circle",
            np.array([449.999993741124, 149.550000006261]),
            np.array([9.99999500000375e-1, -9.99999499995919e-4]),
            4.71138898071803,
            100.00001881,
            radius=69230.7996321627,
        ),
        Seg(
            "line",
            np.array([550.0, 149.522222225005]),
            np.array([9.99999901234583e-1, 4.44444400554072e-4]),
            0.0,
            400.000039506171,
        ),
        # #2101 terminator omitted
    ]


def _sample_polyline(segs, n_per):
    """Sample each segment -> stacked global 2D points (no dedup at joins)."""
    pts = []
    for s in segs:
        for i in range(n_per + 1):
            ll = abs(s.length) * i / n_per
            gp, _ = s.eval(ll)
            pts.append(gp)
    return np.array(pts)


def directrix_points(n_per=400):
    """Compose horizontal (x,y)(s) with vertical z(s) -> sampled 3D directrix.

    The vertical gradient is a curve in (distance, height); we evaluate it, build z(distance)
    by interpolation on its first coordinate, then lift the horizontal samples.
    """
    hsegs = horizontal_segs()
    # horizontal cumulative arc length at each sample
    H = []
    s_acc = 0.0
    for seg in hsegs:
        L = abs(seg.length)
        for i in range(n_per + 1):
            ll = L * i / n_per
            gp, _ = seg.eval(ll)
            H.append((s_acc + ll, gp[0], gp[1]))
        s_acc += L
    H = np.array(H)  # (s, x, y)

    # vertical: sample -> (distance, height)
    V = _sample_polyline(vertical_segs(), n_per)  # columns (distance, height)
    # build z(distance) via interp on sorted distance
    order = np.argsort(V[:, 0])
    vd = V[order, 0]
    vz = V[order, 1]
    z = np.interp(H[:, 0], vd, vz)
    return np.column_stack([H[:, 1], H[:, 2], z])


def main():
    oracle = np.load(f"{SCRATCH}/directrix_oracle.npy")
    pts = directrix_points(n_per=600)
    print(f"eval pts: {len(pts)}  oracle pts: {len(oracle)}")
    print(f"eval first {pts[0]}  last {pts[-1]}")
    print(f"oracle first {oracle[0]}  last {oracle[-1]}")
    print(f"eval bbox min {pts.min(0)}  max {pts.max(0)}")
    print(f"oracle bbox min {oracle.min(0)}  max {oracle.max(0)}")

    # nearest-neighbour max deviation: for each oracle pt, distance to nearest eval pt (brute)
    d = np.empty(len(oracle))
    for i, o in enumerate(oracle):
        d[i] = np.sqrt(((pts - o) ** 2).sum(1).min())
    print(f"oracle->eval nearest dist: max {d.max():.4f}  mean {d.mean():.5f}  p95 {np.percentile(d,95):.4f}")


if __name__ == "__main__":
    main()
