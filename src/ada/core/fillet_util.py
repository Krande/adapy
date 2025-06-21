import math

import numpy as np

import ada


def fillet_polyline(points: list[ada.Point], radii: dict[int, float], discretization: int) -> list[ada.Point]:
    """
    Fillet each corner at index i in `radii` by replacing it with
    `discretization` samples along a circular arc of radius r.

    - Automatically handles closed loops (first==last): it drops the
      duplicate last point before computing, then re-closes at the end.
    - Open-curve endpoints are never filleted (they’ll just be copied).

    Args:
        points:          List of ada.Point (each a length-3 ndarray).
        radii:           Mapping from point-index to fillet radius.
        discretization:  Number of points per fillet arc (>=1).

    Returns:
        New list of ada.Point, with specified fillets applied.
    """
    if discretization < 1:
        raise ValueError("discretization must be at least 1")

    # detect+strip duplicate endpoint for closed loop
    if np.array_equal(points[0], points[-1]):
        closed = True
        points = points[:-1]
    else:
        closed = False

    n = len(points)
    if n < 2:
        return points.copy()

    def vec(a: ada.Point, b: ada.Point) -> tuple[float, float, float]:
        d = b - a
        return (float(d[0]), float(d[1]), float(d[2]))

    def dot(u: tuple[float, float, float], v: tuple[float, float, float]) -> float:
        return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]

    def cross(u: tuple[float, float, float], v: tuple[float, float, float]) -> tuple[float, float, float]:
        return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])

    def norm(u: tuple[float, float, float]) -> float:
        return math.sqrt(dot(u, u))

    def normalize(u: tuple[float, float, float]) -> tuple[float, float, float]:
        length = norm(u)
        if length == 0:
            raise ValueError("Zero-length vector")
        return (u[0] / length, u[1] / length, u[2] / length)

    def add(u: tuple[float, float, float], v: tuple[float, float, float]) -> tuple[float, float, float]:
        return (u[0] + v[0], u[1] + v[1], u[2] + v[2])

    def scale(u: tuple[float, float, float], s: float) -> tuple[float, float, float]:
        return (u[0] * s, u[1] * s, u[2] * s)

    def rotate_around_axis(v, axis, angle):
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        return add(add(scale(v, cos_a), scale(cross(axis, v), sin_a)), scale(axis, dot(axis, v) * (1 - cos_a)))

    # helper to wrap indices if closed
    def idx(i: int) -> int:
        return i % n if closed else i

    new_pts: list[ada.Point] = []
    for i in range(n):
        P1 = points[i]

        # skip fillet if not requested here
        if i not in radii:
            new_pts.append(P1)
            continue

        # open-curve endpoints get copied
        if not closed and (i == 0 or i == n - 1):
            new_pts.append(P1)
            continue

        r = radii[i]
        P0, P2 = points[idx(i - 1)], points[idx(i + 1)]

        in_dir = normalize(vec(P1, P0))
        out_dir = normalize(vec(P1, P2))
        cosθ = dot(in_dir, out_dir)
        θ = math.acos(max(-1.0, min(1.0, cosθ)))

        # if straight or reversed, just keep the point
        if abs(θ) < 1e-6 or abs(math.pi - θ) < 1e-6:
            new_pts.append(P1)
            continue

        t = r * math.tan(θ / 2)
        T1 = ada.Point(np.array([P1[0] + in_dir[0] * t, P1[1] + in_dir[1] * t, P1[2] + in_dir[2] * t]))
        T2 = ada.Point(np.array([P1[0] + out_dir[0] * t, P1[1] + out_dir[1] * t, P1[2] + out_dir[2] * t]))

        bis = normalize(add(in_dir, out_dir))
        center_dist = r / math.sin(θ / 2)
        C = ada.Point(
            np.array([P1[0] + bis[0] * center_dist, P1[1] + bis[1] * center_dist, P1[2] + bis[2] * center_dist])
        )

        plane_n = normalize(cross(in_dir, out_dir))
        v_start, v_end = vec(C, T1), vec(C, T2)
        cross_se = cross(v_start, v_end)
        sign = math.copysign(1.0, dot(plane_n, cross_se))
        sweep = sign * math.acos(max(-1.0, min(1.0, dot(normalize(v_start), normalize(v_end)))))

        for k in range(discretization):
            α = sweep * (k / (discretization - 1))
            vr = rotate_around_axis(v_start, plane_n, α)
            new_pts.append(ada.Point(np.array([C[0] + vr[0], C[1] + vr[1], C[2] + vr[2]])))

    # re-close if it was closed
    if closed:
        new_pts.append(new_pts[0])

    return new_pts
