"""The exact area of a planar advanced face, from its loops, with no CAD kernel in it.

What a plate test asks of a face read back from GeniE -- the outline less its holes, every loop
wound the right way -- is a question about the loops, and both kernels' default area integration
answers it to 1e-3 only where a loop is a rational B-spline (measured, ada-cpp 0.31.1 and
pythonocc 8.0.1: GeniE's round hole puts the plate at 11.48536 against 11.49735). This integrates
the loops instead: Green's theorem, ``A = 1/2 \\oint (u dv - v du)`` in the plane's own (u, v), each
edge from ``t_start`` to ``t_end`` on its curve -- lines exactly, curves by 24-point Gauss-Legendre
on each knot span (on each quarter turn of a circle or ellipse), the derivative by complex step.

Each loop counts with the sign of its winding about the plane's axis, and the size of the sum is
returned: a hole wound against its outline subtracts from it, one wound like it adds.
"""

from __future__ import annotations

import numpy as np

from ada.geom import curves as geo_cu

_GL_X, _GL_W = np.polynomial.legendre.leggauss(24)
_H = 1e-30


def _value(curve, t: complex) -> np.ndarray:
    if isinstance(curve, (geo_cu.Circle, geo_cu.Ellipse)):
        pos = curve.position
        c = np.asarray(pos.location, dtype=float)
        z = np.asarray(pos.axis, dtype=float)
        x = np.asarray(pos.ref_direction, dtype=float)
        x = x - z * (x @ z) / (z @ z)
        x /= np.linalg.norm(x)
        y = np.cross(z / np.linalg.norm(z), x)
        a, b = (
            (curve.radius, curve.radius) if isinstance(curve, geo_cu.Circle) else (curve.semi_axis1, curve.semi_axis2)
        )
        return c + a * np.cos(t) * x + b * np.sin(t) * y
    if isinstance(curve, (geo_cu.BSplineCurveWithKnots, geo_cu.RationalBSplineCurveWithKnots)):
        knots = np.repeat(np.asarray(curve.knots, dtype=float), np.asarray(curve.knot_multiplicities, dtype=int))
        poles = np.asarray([list(p) for p in curve.control_points_list], dtype=float)
        w = np.asarray(getattr(curve, "weights_data", None) or np.ones(len(poles)), dtype=float)
        cp = [np.append(p * wi, wi).astype(complex) for p, wi in zip(poles, w)]
        deg = curve.degree
        k = int(np.searchsorted(knots, t.real, side="right") - 1)
        k = max(deg, min(k, len(cp) - 1))
        d = [cp[j + k - deg].copy() for j in range(deg + 1)]
        for r in range(1, deg + 1):
            for j in range(deg, r - 1, -1):
                lo, hi = knots[j + k - deg], knots[j + 1 + k - r]
                a = 0.0 if hi == lo else (t - lo) / (hi - lo)
                d[j] = (1.0 - a) * d[j - 1] + a * d[j]
        return d[deg][:3] / d[deg][3]
    raise TypeError(f"no area for an edge on a {type(curve).__name__}")


def _breaks(curve, lo: float, hi: float) -> list[float]:
    """Where the integrand may kink: knots inside the range, or quarter turns of a conic."""
    if isinstance(curve, (geo_cu.Circle, geo_cu.Ellipse)):
        inner = [k * np.pi / 2 for k in range(int(np.floor(lo / (np.pi / 2))) + 1, int(np.ceil(hi / (np.pi / 2))))]
    else:
        inner = [float(k) for k in curve.knots]
    return [lo] + [k for k in inner if lo < k < hi] + [hi]


def planar_face_area(face) -> float:
    pos = face.face_surface.position
    origin = np.asarray(pos.location, dtype=float)
    n = np.array(pos.axis, dtype=float)
    n /= np.linalg.norm(n)
    u = np.asarray(pos.ref_direction, dtype=float)
    u = u - n * (u @ n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)

    def uv(p):
        d = np.asarray(p) - origin
        return d @ u, d @ v

    total = 0.0
    for bound in face.bounds:
        for oe in bound.bound.edge_list:
            curve = oe.edge_element.edge_geometry
            if isinstance(curve, geo_cu.Line):
                (u0, v0), (u1, v1) = uv(np.asarray(oe.start, dtype=float)), uv(np.asarray(oe.end, dtype=float))
                total += u0 * v1 - u1 * v0
                continue
            t0, t1 = float(oe.t_start), float(oe.t_end)
            sign = 1.0 if t1 >= t0 else -1.0
            br = _breaks(curve, min(t0, t1), max(t0, t1))
            for a, b in zip(br[:-1], br[1:]):
                for x, wt in zip(_GL_X, _GL_W):
                    t = 0.5 * (b - a) * x + 0.5 * (a + b)
                    p = _value(curve, complex(t, _H))
                    pu, pv = uv(p.real)
                    du, dv = (p.imag / _H) @ u, (p.imag / _H) @ v
                    total += sign * 0.5 * (b - a) * wt * (pu * dv - pv * du)
    return abs(0.5 * total)


def plate_area(pl) -> float:
    """A read-back plate's area, its face first built and checked valid by the active backend.

    The kernel answers "is this a face" (a hole wound like its outline builds invalid on both,
    measured); the area comes from the loops where the face is planar, and from the kernel on a
    cylinder, whose loops the reader gives as lines and arcs -- both kernels integrate those
    exactly (measured on GeniE's two cylindrical shells: 0 and 1.4e-16 relative).
    """
    from ada.cad import active_backend
    from ada.geom import surfaces as geo_su

    be = active_backend()
    shape = be.build(pl.geom)
    assert be.is_valid(shape), f"plate {pl.name!r} builds an invalid face"
    if isinstance(pl.geom.geometry.face_surface, geo_su.Plane):
        return planar_face_area(pl.geom.geometry)
    return be.area(shape)
