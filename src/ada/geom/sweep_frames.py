"""Station frames for sweeping a profile along a path of straight lines and circular arcs.

Pure numpy -- no CAD kernel -- so every consumer of a swept solid places the profile
identically: the NGEOM stream serializer (libtess2), the OCC builder and the adacpp
builder all read the same ``(origins, dir_x, dir_y)`` stations.

Conventions
-----------
A profile point ``(u, v)`` at station ``i`` lands at ``origins[i] + u * dir_x[i] + v * dir_y[i]``,
and ``dir_x[i] x dir_y[i]`` is the path tangent at that station. For a beam this is the
straight-beam convention: profile ``u`` along the beam's ``yvec``, profile ``v`` along ``up``.

The frame is carried along the path WITHOUT TWIST (a rotation-minimising frame, computed with
the double-reflection method of Wang et al., ACM TOG 27(1), 2008). On a planar path that is the
frame whose in-plane axis stays in the plane, i.e. what a Frenet frame gives on the arcs without
its flip on the straight legs.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ada.geom.curves import CURVE_GEOM_TYPES

#: Largest angle one arc station step may turn through. 5 degrees keeps the chord error of a
#: bend below ``R * (1 - cos(2.5 deg)) ~ 1e-3 * R`` -- 5 mm on a 5 m bend radius.
DEFAULT_MAX_ARC_STEP_DEG = 5.0


def _v3(p) -> np.ndarray:
    a = np.asarray(list(p), dtype=float).ravel()
    if a.shape[0] == 3:
        return a
    out = np.zeros(3)
    out[: a.shape[0]] = a
    return out


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-15:
        raise ValueError("zero-length vector")
    return v / n


def arc_center_normal(start, mid, end) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Centre, unit rotation axis, radius and swept angle (radians, in ``(0, 2*pi)``) of the
    circular arc through ``start -> mid -> end``. The arc runs counter-clockwise about the axis."""
    p0, p1, p2 = _v3(start), _v3(mid), _v3(end)
    a, b = p1 - p0, p2 - p0
    n = np.cross(a, b)
    nn = float(np.linalg.norm(n))
    if nn < 1e-14:
        raise ValueError("arc points are collinear")
    center = p0 + np.cross(float(a @ a) * b - float(b @ b) * a, n) / (2.0 * nn * nn)
    # start -> mid -> end turns about cross(p1 - p0, p2 - p1); that is the CCW axis of the arc,
    # also for an arc longer than half a circle.
    axis = _unit(np.cross(p1 - p0, p2 - p1))
    u = p0 - center
    r = float(np.linalg.norm(u))
    v = np.cross(axis, u)
    d = p2 - center
    angle = math.atan2(float(d @ v) / r, float(d @ u) / r)
    if angle <= 0.0:
        angle += 2.0 * math.pi
    return center, axis, r, angle


def _segment_samples(seg, max_step_rad: float) -> tuple[np.ndarray, np.ndarray]:
    """Points and unit tangents of one directrix segment, start to end inclusive."""
    import ada.geom.curves as cu

    if isinstance(seg, cu.ArcLine):
        center, axis, r, angle = arc_center_normal(seg.start, seg.midpoint, seg.end)
        n = max(2, int(math.ceil(angle / max_step_rad - 1e-9)))
        u = (_v3(seg.start) - center) / r
        v = np.cross(axis, u)
        th = np.linspace(0.0, angle, n + 1)
        c, s = np.cos(th)[:, None], np.sin(th)[:, None]
        pts = center + r * (c * u + s * v)
        tan = -s * u + c * v
        pts[-1] = _v3(seg.end)  # exact end point, no round-off drift into the next segment
        return pts, tan
    if isinstance(seg, cu.Edge):
        p0, p1 = _v3(seg.start), _v3(seg.end)
        t = _unit(p1 - p0)
        return np.array([p0, p1]), np.array([t, t])
    raise NotImplementedError(f"sweep path segment {type(seg).__name__} (only lines and circular arcs)")


def directrix_segments(directrix: CURVE_GEOM_TYPES) -> list:
    """The ordered line/arc segments of a sweep path."""
    import ada.geom.curves as cu

    if isinstance(directrix, cu.IndexedPolyCurve):
        return list(directrix.segments)
    if isinstance(directrix, (cu.Edge, cu.ArcLine)):
        return [directrix]
    if isinstance(directrix, cu.PolyLine):
        pts = list(directrix.points)
        return [cu.Edge(a, b) for a, b in zip(pts[:-1], pts[1:])]
    raise NotImplementedError(f"sweep path {type(directrix).__name__} (only lines and circular arcs)")


def sample_directrix(
    directrix: CURVE_GEOM_TYPES, max_arc_step_deg: float = DEFAULT_MAX_ARC_STEP_DEG
) -> tuple[np.ndarray, np.ndarray]:
    """Stations along a path of lines and arcs: ``(points (N,3), unit tangents (N,3))``.

    Arcs are sampled at most ``max_arc_step_deg`` apart with their EXACT tangents, so the end
    stations of a bend are normal to the path, not to a chord. Straight legs contribute only their
    end points. Where two segments meet with a tangent discontinuity (a sharp polyline corner, no
    fillet) the station takes the bisecting tangent -- a mitre joint (see :func:`apply_mitres`)."""
    pts, tans, _ = _sample_with_kinks(directrix, max_arc_step_deg)
    return pts, tans


def _sample_with_kinks(directrix, max_arc_step_deg: float):
    """:func:`sample_directrix` plus ``{station: (incoming tangent, outgoing tangent)}`` for every
    sharp corner."""
    step = math.radians(max_arc_step_deg)
    pts: list[np.ndarray] = []
    tans: list[np.ndarray] = []
    kinks: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for seg in directrix_segments(directrix):
        p, t = _segment_samples(seg, step)
        if pts and float(np.linalg.norm(pts[-1] - p[0])) < 1e-9:
            # Shared junction point: keep one station. A kink gets the mitre (bisector) tangent.
            if float(tans[-1] @ t[0]) < 1.0 - 1e-9:
                bis = tans[-1] + t[0]
                if float(np.linalg.norm(bis)) > 1e-9:
                    kinks[len(pts) - 1] = (tans[-1].copy(), t[0].copy())
                    tans[-1] = _unit(bis)
                else:  # the path doubles back on itself; nothing sensible to mitre
                    tans[-1] = t[0]
            p, t = p[1:], t[1:]
        pts.extend(p)
        tans.extend(t)
    return np.asarray(pts, dtype=float), np.asarray(tans, dtype=float), kinks


def apply_mitres(dir_x: np.ndarray, dir_y: np.ndarray, kinks) -> None:
    """Stretch the section at each sharp corner into the mitre plane, in place.

    The corner station's section lies in the bisector plane; placed there unchanged it would be
    narrower than the legs across the bend (by ``cos(turn / 2)``). Scaling the in-plane bend
    component of the frame by ``1 / cos(turn / 2)`` makes it the exact intersection of the two
    legs' prisms -- a true mitre. The frame vectors are then no longer unit length, which the
    stream kernel and the polygon lofts take as they are (``origin + u * dir_x + v * dir_y``)."""
    for i, (t_in, t_out) in kinks.items():
        b = _unit(t_in + t_out)
        k = _unit(t_out - t_in)
        c = float(t_in @ b)
        if c < 1e-6:
            continue
        s = 1.0 / c - 1.0
        dir_x[i] = dir_x[i] + s * float(dir_x[i] @ k) * k
        dir_y[i] = dir_y[i] + s * float(dir_y[i] @ k) * k


def rotation_minimising_frames(points: np.ndarray, tangents: np.ndarray, x0) -> tuple[np.ndarray, np.ndarray]:
    """Carry the start reference ``x0`` (profile local +x) along the stations without twist.

    Double reflection (Wang et al. 2008): exact for circular arcs sampled at any density, and on a
    straight leg the frame is carried unchanged. Returns ``(dir_x, dir_y)`` with
    ``dir_x x dir_y == tangent`` at every station."""
    n = len(points)
    dir_x = np.zeros((n, 3))
    t0 = _unit(np.asarray(tangents[0], dtype=float))
    r = _v3(x0)
    r = r - float(r @ t0) * t0
    dir_x[0] = _unit(r)
    for i in range(n - 1):
        ri, ti, tj = dir_x[i], tangents[i], tangents[i + 1]
        v1 = points[i + 1] - points[i]
        c1 = float(v1 @ v1)
        if c1 < 1e-24:
            rl, tl = ri, ti
        else:
            rl = ri - (2.0 / c1) * float(v1 @ ri) * v1
            tl = ti - (2.0 / c1) * float(v1 @ ti) * v1
        v2 = tj - tl
        c2 = float(v2 @ v2)
        rj = rl if c2 < 1e-24 else rl - (2.0 / c2) * float(v2 @ rl) * v2
        rj = rj - float(rj @ tj) * tj  # re-orthogonalise (mitre stations, round-off)
        dir_x[i + 1] = _unit(rj)
    dir_y = np.cross(tangents, dir_x)
    dir_y /= np.linalg.norm(dir_y, axis=1, keepdims=True)
    return dir_x, dir_y


def transport_vectors(points: np.ndarray, tangents: np.ndarray, vectors) -> list[np.ndarray]:
    """Carry each of ``vectors`` rigidly along the stations: the same pair of reflections per
    step as :func:`rotation_minimising_frames`, applied to every vector, so their mutual angles
    (and their angles to the tangent) are kept. Returns one ``(N,3)`` array per vector."""
    n = len(points)
    out = [np.zeros((n, 3)) for _ in vectors]
    for k, v in enumerate(vectors):
        out[k][0] = _v3(v)
    for i in range(n - 1):
        ti, tj = tangents[i], tangents[i + 1]
        v1 = points[i + 1] - points[i]
        c1 = float(v1 @ v1)
        tl = ti if c1 < 1e-24 else ti - (2.0 / c1) * float(v1 @ ti) * v1
        v2 = tj - tl
        c2 = float(v2 @ v2)
        for arr in out:
            r = arr[i]
            if c1 >= 1e-24:
                r = r - (2.0 / c1) * float(v1 @ r) * v1
            if c2 >= 1e-24:
                r = r - (2.0 / c2) * float(v2 @ r) * v2
            arr[i + 1] = r
    return out


def placed_profile_to_planar(profile, directrix, max_arc_step_deg: float = DEFAULT_MAX_ARC_STEP_DEG):
    """Turn a profile already PLACED in 3D at the path start (``PrimSweep``) into the flat
    section + stations form every other sweep uses.

    Returns ``(profile_2d, origins, dir_x, dir_y)``: the profile's loops expressed in an
    orthonormal basis ``(ex, ey)`` of their own plane (origin = path start), and that basis carried
    rigidly along the path. At the start station the section is exactly the authored 3D profile,
    wherever its plane points. Loops are wound so the swept surfaces face outward with these
    stations (see :func:`orient_profile_ccw`). Raises ``NotImplementedError`` for a path that is
    not lines and arcs."""
    import ada.geom.curves as cu
    import ada.geom.surfaces as su
    from ada.cadit.ngeom.serialize import _Encoder

    pts, tans, kinks = _sample_with_kinks(directrix, max_arc_step_deg)
    o = pts[0]
    curves = [profile.outer_curve] + list(profile.inner_curves or [])
    loops = [np.asarray(_Encoder._loop_points_3d(c), dtype=float) for c in curves]
    # Plane normal of the outer loop (Newell), then any in-plane basis.
    p = loops[0]
    q = np.roll(p, -1, axis=0)
    normal = np.array(
        [
            np.sum((p[:, 1] - q[:, 1]) * (p[:, 2] + q[:, 2])),
            np.sum((p[:, 2] - q[:, 2]) * (p[:, 0] + q[:, 0])),
            np.sum((p[:, 0] - q[:, 0]) * (p[:, 1] + q[:, 1])),
        ]
    )
    normal = _unit(normal)
    if float(normal @ tans[0]) < 0.0:
        normal = -normal
    ex = start_reference(normal)
    ey = np.cross(normal, ex)
    loops2d = [np.column_stack([(lp - o) @ ex, (lp - o) @ ey]) for lp in loops]

    def poly(lp2d, ccw: bool):
        sa = 0.5 * float(np.dot(lp2d[:, 0], np.roll(lp2d[:, 1], -1)) - np.dot(lp2d[:, 1], np.roll(lp2d[:, 0], -1)))
        if (sa > 0.0) != ccw:
            lp2d = lp2d[::-1]
        ring = [tuple(map(float, x)) for x in lp2d] + [tuple(map(float, lp2d[0]))]
        return cu.IndexedPolyCurve([cu.Edge(ring[i], ring[i + 1]) for i in range(len(ring) - 1)])

    flat = su.ArbitraryProfileDef(
        profile.profile_type,
        poly(loops2d[0], True),
        [poly(lp, True) for lp in loops2d[1:]],
        profile_name=getattr(profile, "profile_name", None),
    )
    dir_x, dir_y = transport_vectors(pts, tans, [ex, ey])
    apply_mitres(dir_x, dir_y, kinks)
    return flat, pts, dir_x, dir_y


def start_reference(tangent, up=None, fallback=None) -> np.ndarray:
    """Profile local +x at the path start: ``up x tangent`` (the straight-beam ``yvec``), falling
    back to ``fallback`` and then to any perpendicular when ``up`` is parallel to the tangent."""
    t = _unit(_v3(tangent))
    if up is not None:
        c = np.cross(_v3(up), t)
        if float(np.linalg.norm(c)) > 1e-9:
            return _unit(c)
    if fallback is not None:
        c = _v3(fallback)
        c = c - float(c @ t) * t
        if float(np.linalg.norm(c)) > 1e-9:
            return _unit(c)
    a = np.array([0.0, 0.0, 1.0]) if abs(t[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    return _unit(np.cross(a, t))


def sweep_frames(
    directrix: CURVE_GEOM_TYPES, x0, max_arc_step_deg: float = DEFAULT_MAX_ARC_STEP_DEG
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(origins, dir_x, dir_y)`` stations for sweeping a profile along ``directrix`` with the
    profile local +x starting along ``x0`` (projected normal to the start tangent): sampled
    (:func:`sample_directrix`), framed without twist (:func:`rotation_minimising_frames`) and
    mitred at sharp corners (:func:`apply_mitres`)."""
    pts, tans, kinks = _sample_with_kinks(directrix, max_arc_step_deg)
    dir_x, dir_y = rotation_minimising_frames(pts, tans, x0)
    apply_mitres(dir_x, dir_y, kinks)
    return pts, dir_x, dir_y


def extend_stations(origins, dir_x, dir_y, distance: float):
    """The stations with one more at each end, ``distance`` beyond the path ends along the end
    directions -- for a void cutter, so its end caps are not coplanar with the solid's (a
    boolean of two coplanar caps can keep the cavity closed)."""
    origins = np.asarray(origins, dtype=float)
    t0 = _unit(origins[1] - origins[0])
    t1 = _unit(origins[-1] - origins[-2])
    o = np.vstack([origins[0] - distance * t0, origins, origins[-1] + distance * t1])
    dx = np.vstack([dir_x[:1], dir_x, dir_x[-1:]])
    dy = np.vstack([dir_y[:1], dir_y, dir_y[-1:]])
    return o, dx, dy


def decimate_stations(origins, dir_x, dir_y, profile_char: float, max_turn_deg: float = 8.0) -> list[int]:
    """Indices of the stations worth lofting through: drops near-coincident samples (closer than
    a small fraction of the profile size, where a ruled band between two sections degenerates)
    while keeping every station where the tangent has turned past ``max_turn_deg`` since the last
    kept one, so bends stay curved. The end stations are always kept."""
    n = len(origins)
    if n <= 2:
        return list(range(n))
    eps = max(1e-6, 0.05 * profile_char)
    cos_tol = math.cos(math.radians(max_turn_deg))
    tang = np.cross(dir_x, dir_y)
    tn = np.linalg.norm(tang, axis=1, keepdims=True)
    tang = tang / np.where(tn < 1e-12, 1.0, tn)

    keep = [0]
    for i in range(1, n - 1):
        far = float(np.linalg.norm(origins[i] - origins[keep[-1]])) > eps
        turned = float(np.dot(tang[i], tang[keep[-1]])) < cos_tol
        if far or turned:
            keep.append(i)
    keep.append(n - 1)
    return keep


def profile_loops_2d(profile, circle_segments: int = 48) -> list[np.ndarray]:
    """The boundary loops of a planar profile as 2D polygons ``[(M,2), ...]`` -- outer first,
    then the voids. Circles become ``circle_segments``-gons; arcs in a polycurve outline are
    sampled the way the NGEOM serializer samples them."""
    import ada.geom.curves as cu

    def loop(curve) -> np.ndarray:
        if isinstance(curve, cu.Circle):
            c = _v3(curve.position.location)
            th = np.linspace(0.0, 2.0 * math.pi, circle_segments, endpoint=False)
            r = float(curve.radius)
            return np.column_stack([c[0] + r * np.cos(th), c[1] + r * np.sin(th)])
        from ada.cadit.ngeom.serialize import _Encoder

        pts = np.asarray(_Encoder._loop_points_3d(curve), dtype=float)
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        return pts[:, :2]

    if getattr(profile, "outer_curve", None) is None:
        from ada.api.beams.geom_beams import parametric_profile_to_arbitrary

        profile = parametric_profile_to_arbitrary(profile)
    return [loop(profile.outer_curve)] + [loop(c) for c in (profile.inner_curves or [])]


def loop_signed_area(curve) -> float:
    """Signed area of a closed profile loop in its local XY plane (> 0: counter-clockwise).
    A ``Circle``/``Ellipse`` counts as counter-clockwise -- the sense its edge is emitted in."""
    import ada.geom.curves as cu

    if isinstance(curve, cu.Circle):
        return math.pi * float(curve.radius) ** 2
    if isinstance(curve, cu.Ellipse):
        return math.pi * float(curve.semi_axis1) * float(curve.semi_axis2)
    from ada.cadit.ngeom.serialize import _Encoder

    p = np.asarray(_Encoder._loop_points_3d(curve), dtype=float)[:, :2]
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def reversed_loop(curve):
    """The same closed loop traversed the other way (line/arc polycurves and polylines); any
    other curve is returned unchanged."""
    import ada.geom.curves as cu

    if isinstance(curve, cu.IndexedPolyCurve):
        segs = []
        for s in reversed(curve.segments):
            if isinstance(s, cu.ArcLine):
                segs.append(cu.ArcLine(s.end, s.midpoint, s.start))
            elif isinstance(s, cu.Edge):
                segs.append(cu.Edge(s.end, s.start))
            else:
                return curve
        return cu.IndexedPolyCurve(segs, curve.self_intersect)
    if isinstance(curve, cu.PolyLine):
        return cu.PolyLine(list(reversed(list(curve.points))))
    return curve


def orient_profile_ccw(profile):
    """``profile`` with every boundary loop counter-clockwise in its local XY plane.

    The NGEOM sweep kernel orients the swept surface from the profile winding and the station
    handedness together; with right-handed stations (``dir_x x dir_y`` along the path) a
    counter-clockwise profile gives outward normals. Sections are not all authored that way
    (an I-section outline runs clockwise), so normalise rather than ship an inside-out mesh."""
    import ada.geom.surfaces as su

    def ccw(c):
        return reversed_loop(c) if loop_signed_area(c) < 0.0 else c

    return su.ArbitraryProfileDef(
        profile.profile_type,
        ccw(profile.outer_curve),
        [ccw(c) for c in (profile.inner_curves or [])],
        profile_name=getattr(profile, "profile_name", None),
    )


def swept_area_is_planar_2d(profile) -> bool:
    """True when a swept profile is a genuine 2D section in its own local XY plane (2D boundary
    points, or an analytic circle), i.e. it must be PLACED at each station of the path. A profile
    whose boundary carries 3D points is already placed in space (``PrimSweep``)."""
    import ada.geom.curves as cu

    outer = getattr(profile, "outer_curve", None)
    if outer is None:
        return False
    if isinstance(outer, cu.Circle):
        loc = list(outer.position.location)
        axis = outer.position.axis
        flat_axis = axis is None or np.allclose(_v3(axis), (0.0, 0.0, 1.0))
        return flat_axis and (len(loc) == 2 or abs(float(loc[2])) < 1e-12)
    segs = getattr(outer, "segments", None)
    if segs:
        return all(len(getattr(s, "start", ())) == 2 for s in segs)
    pts = getattr(outer, "points", None)
    if pts:
        return all(len(p) == 2 for p in pts)
    return False


def frames_for_solid(frs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The stations a ``FixedReferenceSweptAreaSolid`` with a planar 2D profile is built from:
    its ``precomputed_frames`` when set, else the shared fixed-reference framing of the
    directrix (``general_directrix_frames``)."""
    pre = getattr(frs, "precomputed_frames", None)
    if pre is not None:
        return tuple(np.asarray(a, dtype=float) for a in pre)
    from ada.cadit.ngeom.serialize import general_directrix_frames

    return general_directrix_frames(frs.directrix, frs.fixed_reference)
