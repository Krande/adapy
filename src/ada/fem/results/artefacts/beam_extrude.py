"""Procedural (CAD-kernel-free) extrusion of a beam's section along its axis.

Baking beam solids for the viewer used to cost ~2.6 ms per beam — 7 minutes on a
162k-beam deck, for an output of ~16 vertices each. Every beam made a round trip
through OpenCascade in a Python loop: wire -> face, ``BRepPrimAPI_MakePrism``,
two deep-copying ``BRepBuilderAPI_Transform``s, a per-beam ``ShapeTesselator``
and a per-beam ``np.unique``. A section shared by 40k beams was rebuilt 40k
times.

Nothing about a straight, boolean-free, constant-section beam needs a kernel.
Its solid is the section outline swept between two points, which is exactly what
a results postprocessor does to draw beam sections instantly. Every input is
already in hand: :func:`ada.api.beams.geom_beams.straight_beam_frame` gives the
placement, and :func:`ada.geom.curve_discretize.discretize_curve` samples the
same ``ArbitraryProfileDef`` OCC is handed. So the outline SOURCE is identical to
OCC's and the frame maths is literally the same function — the two paths cannot
disagree about where a profile sits, only about how finely a circle is faceted.

The sampled outline (and its cap triangulation) is cached per distinct section,
so the deck's dozen profiles are built a dozen times rather than 162k.

OCC remains the fallback for everything this cannot do — tapered, swept and
revolved beams, beams with booleans, and any profile whose curves have no native
sampler (splines) — and remains selectable outright via ``method="occ"``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "SectionOutline",
    "SectionOutlineCache",
    "outline_for",
    "extrude_beam",
    "extrude_outline",
    "unsupported_reason",
]

# Chord sag allowed when sampling a curved outline, and the coarsest angular step
# taken regardless. 2*pi/24 makes every beam-sized radius land on 24 facets per
# full circle, which is within a couple of facets of what OCC's default
# tessellation quality produces for the same tube; the deflection only kicks in
# for outlines large enough that 15 degrees per facet would visibly flatten them.
DEFAULT_DEFLECTION = 0.01
DEFAULT_MAX_ANGLE = 2.0 * np.pi / 24.0

# Outline points closer than this are the same point. Profile builders emit
# butt-joined segments whose shared endpoint appears twice, and a duplicate ring
# point would make a zero-area side quad and a degenerate ear.
_POINT_TOL = 1e-9

# Below this a triangle is degenerate rather than an ear. Profile coordinates are
# metres and the smallest real feature is a few millimetres, so 1e-14 m^2 is far
# under anything meaningful and far over float noise on a shoelace term.
_AREA_EPS = 1e-14


@dataclass(frozen=True)
class SectionOutline:
    """A section's 2D outline, ready to sweep.

    ``rings`` is the outer boundary first, then one ring per void. ``points`` is
    those rings concatenated — the vertex order of ONE extrusion end — and
    ``cap_tris`` indexes into it. Winding is normalised on construction: the
    outer ring counter-clockwise, every void clockwise, so a ring walked in
    order always has the material on its left and the side quads come out with
    outward normals without a per-beam orientation test.
    """

    points: np.ndarray  # (n, 2) float64, all rings concatenated
    ring_slices: tuple[tuple[int, int], ...]  # (start, stop) into points, outer first
    cap_tris: np.ndarray  # (k, 3) int32 into points, counter-clockwise
    triangles: np.ndarray  # (m, 3) uint32 into the 2n extruded vertices
    centroid: tuple[float, float]  # area centroid, voids removed
    area: float  # net area, voids removed

    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])


# ---------------------------------------------------------------------------
# Outline sampling
# ---------------------------------------------------------------------------


def _curve_to_3d(curve):
    """Return a z=0 copy of a profile curve so ``discretize_curve`` accepts it.

    Section profiles carry 2D points (``CurvePoly2d`` works in the profile
    plane) while the discretizer is written for 3D polylines. Promoting here
    rather than teaching the discretizer about 2D keeps the shared sampler --
    and its parity tests against OCC -- untouched.
    """

    import ada.geom.curves as cu

    if type(curve) is cu.Edge:
        return cu.Edge(curve.start.get_3d(), curve.end.get_3d())
    if type(curve) is cu.ArcLine:
        return cu.ArcLine(curve.start.get_3d(), curve.midpoint.get_3d(), curve.end.get_3d())
    if type(curve) is cu.IndexedPolyCurve:
        return cu.IndexedPolyCurve([_curve_to_3d(seg) for seg in curve.segments])
    # Circle already carries a 3D Axis2Placement3D; anything else is handed
    # through unchanged and discretize_curve decides whether it can sample it.
    return curve


def _ring_from_curve(curve, deflection: float, max_angle: float) -> np.ndarray | None:
    """Sample one closed profile curve into an open (n, 2) ring, or None."""

    from ada.geom.curve_discretize import discretize_curve

    pts = discretize_curve(_curve_to_3d(curve), deflection, max_angle)
    if not pts:
        return None

    ring = np.asarray(pts, dtype=np.float64)[:, :2]
    # discretize_curve closes the loop (last point == first) and only drops
    # duplicates against the immediately preceding point, so the wrap-around
    # duplicate survives. Drop every coincident neighbour including that one.
    keep = np.ones(ring.shape[0], dtype=bool)
    prev = 0
    for i in range(1, ring.shape[0]):
        if np.linalg.norm(ring[i] - ring[prev]) <= _POINT_TOL:
            keep[i] = False
        else:
            prev = i
    ring = ring[keep]
    if ring.shape[0] >= 2 and np.linalg.norm(ring[-1] - ring[0]) <= _POINT_TOL:
        ring = ring[:-1]
    if ring.shape[0] < 3:
        return None
    return ring


def _signed_area(ring: np.ndarray) -> float:
    x = ring[:, 0]
    y = ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _ring_centroid(ring: np.ndarray) -> tuple[float, float, float]:
    """``(area, cx, cy)`` of a closed polygon by the shoelace formula."""

    x = ring[:, 0]
    y = ring[:, 1]
    x1 = np.roll(x, -1)
    y1 = np.roll(y, -1)
    cross = x * y1 - x1 * y
    area = 0.5 * float(cross.sum())
    if abs(area) < _AREA_EPS:
        return 0.0, float(x.mean()), float(y.mean())
    cx = float(((x + x1) * cross).sum()) / (6.0 * area)
    cy = float(((y + y1) * cross).sum()) / (6.0 * area)
    return area, cx, cy


# ---------------------------------------------------------------------------
# Cap triangulation: hole bridging + ear clipping, no new dependency
# ---------------------------------------------------------------------------


def _segments_properly_intersect(p1, p2, q1, q2) -> bool:
    """True when segment p1-p2 crosses q1-q2 somewhere other than a shared end."""

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    # Endpoints shared with the bridge are allowed to touch — that is what a
    # bridge IS — so any segment sharing a position with the probe is skipped.
    for a in (p1, p2):
        for b in (q1, q2):
            if abs(a[0] - b[0]) <= _POINT_TOL and abs(a[1] - b[1]) <= _POINT_TOL:
                return False

    d1 = cross(q1, q2, p1)
    d2 = cross(q1, q2, p2)
    d3 = cross(p1, p2, q1)
    d4 = cross(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    # Collinear overlap counts as blocked: a bridge lying along an edge would
    # produce zero-area ears rather than a triangulation.
    if abs(d1) <= _AREA_EPS and abs(d2) <= _AREA_EPS:
        lo = min(q1[0], q2[0]) - _POINT_TOL, min(q1[1], q2[1]) - _POINT_TOL
        hi = max(q1[0], q2[0]) + _POINT_TOL, max(q1[1], q2[1]) + _POINT_TOL
        for a in (p1, p2):
            if lo[0] <= a[0] <= hi[0] and lo[1] <= a[1] <= hi[1]:
                return True
    return False


def _loop_edges(pts: np.ndarray, loop: list[int]):
    n = len(loop)
    for i in range(n):
        yield pts[loop[i]], pts[loop[(i + 1) % n]]


def _bridge_holes(pts: np.ndarray, outer: list[int], holes: list[list[int]]) -> list[int] | None:
    """Splice each void into the outer loop with a zero-width cut.

    The standard trick: take the void's rightmost vertex M, find an outer vertex
    P that M can see, and walk ``... P, M, <the whole void>, M, P ...``. The loop
    is then a single simple polygon an ear clipper handles, at the cost of two
    duplicated vertices per void (they are duplicated in the LOOP, not in the
    vertex buffer, so the extruded mesh keeps one vertex per outline point).

    Voids are merged rightmost-first so a later bridge never has to cross an
    already-spliced one from the wrong side. Returns None when no visible
    partner exists, which is the caller's cue to fall back to OCC.
    """

    loop = list(outer)
    remaining = sorted(holes, key=lambda h: -float(pts[h, 0].max()))

    for hi, hole in enumerate(remaining):
        m_local = int(np.lexsort((pts[hole, 1], pts[hole, 0]))[-1])
        m_idx = hole[m_local]
        m_pt = pts[m_idx]

        # Everything the bridge must not cross: the loop as it stands plus the
        # voids not yet merged (this one included).
        blockers = list(_loop_edges(pts, loop))
        for other in remaining[hi:]:
            blockers.extend(_loop_edges(pts, other))

        order = np.argsort([float(np.linalg.norm(pts[v] - m_pt)) for v in loop])
        chosen = None
        for pos in order:
            cand = loop[int(pos)]
            if _POINT_TOL >= float(np.linalg.norm(pts[cand] - m_pt)):
                continue
            if any(_segments_properly_intersect(m_pt, pts[cand], a, b) for a, b in blockers):
                continue
            chosen = int(pos)
            break
        if chosen is None:
            return None

        rotated = hole[m_local:] + hole[:m_local]
        loop = loop[: chosen + 1] + rotated + [m_idx] + loop[chosen:]

    return loop


def _point_in_triangle(p, a, b, c) -> bool:
    d1 = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    d2 = (c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0])
    d3 = (a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0])
    return d1 >= -_AREA_EPS and d2 >= -_AREA_EPS and d3 >= -_AREA_EPS


def _earclip(pts: np.ndarray, loop: list[int]) -> np.ndarray | None:
    """Triangulate a counter-clockwise simple polygon given as a loop of indices.

    O(n^2) and unashamedly so: it runs once per DISTINCT section, on an outline
    of at most ~70 points after circle sampling, not once per beam.

    Only reflex vertices are tested for containment (the classic optimisation),
    which also keeps a bridge's duplicated vertices from blocking every ear:
    a duplicate sits exactly on a corner of any ear that touches it, and a
    containment test that counted corners as inside would stall the clip.
    """

    idx = list(loop)
    tris: list[tuple[int, int, int]] = []

    while len(idx) > 3:
        m = len(idx)
        ear = None
        for i in range(m):
            ia = idx[(i - 1) % m]
            ib = idx[i]
            ic = idx[(i + 1) % m]
            a, b, c = pts[ia], pts[ib], pts[ic]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= _AREA_EPS:
                continue  # reflex or degenerate corner

            blocked = False
            for k in range(m):
                if k in ((i - 1) % m, i, (i + 1) % m):
                    continue
                kp = pts[idx[k]]
                # Skip vertices coincident with a corner of this ear — a bridge
                # duplicate, or two rings touching.
                if (
                    abs(kp[0] - a[0]) <= _POINT_TOL
                    and abs(kp[1] - a[1]) <= _POINT_TOL
                    or abs(kp[0] - b[0]) <= _POINT_TOL
                    and abs(kp[1] - b[1]) <= _POINT_TOL
                    or abs(kp[0] - c[0]) <= _POINT_TOL
                    and abs(kp[1] - c[1]) <= _POINT_TOL
                ):
                    continue
                kprev = pts[idx[(k - 1) % m]]
                knext = pts[idx[(k + 1) % m]]
                reflex = (kp[0] - kprev[0]) * (knext[1] - kprev[1]) - (kp[1] - kprev[1]) * (knext[0] - kprev[0]) <= 0.0
                if reflex and _point_in_triangle(kp, a, b, c):
                    blocked = True
                    break
            if not blocked:
                ear = i
                tris.append((ia, ib, ic))
                break

        if ear is None:
            return None
        del idx[ear]

    if len(idx) != 3:
        return None
    a, b, c = pts[idx[0]], pts[idx[1]], pts[idx[2]]
    if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= _AREA_EPS:
        # The last three are collinear or wound the wrong way. Report failure
        # rather than shipping a cap with a hole or an inverted facet in it —
        # the caller falls back to OCC, which is always a correct answer.
        return None
    tris.append((idx[0], idx[1], idx[2]))
    return np.asarray(tris, dtype=np.int32)


# ---------------------------------------------------------------------------
# Section -> SectionOutline
# ---------------------------------------------------------------------------


def outline_for(
    section,
    *,
    deflection: float = DEFAULT_DEFLECTION,
    max_angle: float = DEFAULT_MAX_ANGLE,
) -> SectionOutline | None:
    """Sample a section into a sweepable outline, or None if it cannot be.

    Goes through ``section_to_arbitrary_profile_def_with_voids`` — the very
    profile OCC is handed — so a section the kernel draws one way is never
    sampled from a different source here.
    """

    from ada.api.beams.geom_beams import section_to_arbitrary_profile_def_with_voids

    try:
        profile = section_to_arbitrary_profile_def_with_voids(section)
    except Exception:  # noqa: BLE001 — a profile that will not build has no outline
        return None

    outer = _ring_from_curve(profile.outer_curve, deflection, max_angle)
    if outer is None:
        return None
    if _signed_area(outer) < 0:
        outer = outer[::-1]

    holes: list[np.ndarray] = []
    for curve in profile.inner_curves:
        ring = _ring_from_curve(curve, deflection, max_angle)
        if ring is None:
            return None
        if _signed_area(ring) > 0:
            ring = ring[::-1]
        holes.append(ring)

    rings = [outer, *holes]
    points = np.ascontiguousarray(np.concatenate(rings, axis=0), dtype=np.float64)
    slices: list[tuple[int, int]] = []
    cursor = 0
    for ring in rings:
        slices.append((cursor, cursor + ring.shape[0]))
        cursor += ring.shape[0]

    outer_idx = list(range(*slices[0]))
    hole_idx = [list(range(*s)) for s in slices[1:]]
    loop = _bridge_holes(points, outer_idx, hole_idx) if hole_idx else outer_idx
    if loop is None:
        return None
    cap_tris = _earclip(points, loop)
    if cap_tris is None:
        return None

    a_out, cx_out, cy_out = _ring_centroid(outer)
    area = abs(a_out)
    mx = cx_out * area
    my = cy_out * area
    for ring in holes:
        a_h, cx_h, cy_h = _ring_centroid(ring)
        area -= abs(a_h)
        mx -= cx_h * abs(a_h)
        my -= cy_h * abs(a_h)
    if area <= _AREA_EPS:
        return None

    return SectionOutline(
        points=points,
        ring_slices=tuple(slices),
        cap_tris=cap_tris,
        triangles=_extrusion_triangles(slices, cap_tris, points.shape[0]),
        centroid=(mx / area, my / area),
        area=area,
    )


def _extrusion_triangles(slices, cap_tris: np.ndarray, n: int) -> np.ndarray:
    """The whole triangle list of the extrusion, in outline-local indices.

    Connectivity depends on the SECTION, not the beam: vertex ``k`` is outline
    point ``k`` at the near end and ``k + n`` at the far end for every beam that
    shares the profile. Building it once per section rather than once per beam
    leaves the per-beam work at two matrix multiplies.
    """

    tris: list[np.ndarray] = []
    for start, stop in slices:
        i = np.arange(start, stop, dtype=np.int64)
        j = np.roll(i, -1)
        # Each ring is walked with the material on its left, so
        # (edge x extrusion) points out of the solid: outward on the outer
        # ring, into the void on a hole ring.
        tris.append(np.stack([i, j, j + n], axis=1))
        tris.append(np.stack([i, j + n, i + n], axis=1))

    cap = cap_tris.astype(np.int64, copy=False)
    # The far cap keeps the counter-clockwise winding (normal +xvec); the near
    # cap is reversed so its normal is -xvec. Both then face out of the solid.
    tris.append(cap + n)
    tris.append(cap[:, ::-1])

    out = np.concatenate(tris, axis=0).astype(np.uint32)
    out.flags.writeable = False  # shared by every beam on this section
    return out


class SectionOutlineCache:
    """One sampled outline per distinct section, for the life of one bake.

    Keyed by ``id(section)`` like :class:`SectionCentroidCache`, and the section
    itself is kept in the value so that id cannot be recycled onto a different
    object while the cache lives.
    """

    def __init__(
        self,
        *,
        deflection: float = DEFAULT_DEFLECTION,
        max_angle: float = DEFAULT_MAX_ANGLE,
    ) -> None:
        self.deflection = float(deflection)
        self.max_angle = float(max_angle)
        self._cache: dict[int, tuple[object, SectionOutline | None]] = {}

    def get(self, section) -> SectionOutline | None:
        key = id(section)
        hit = self._cache.get(key)
        if hit is not None:
            return hit[1]
        outline = outline_for(section, deflection=self.deflection, max_angle=self.max_angle)
        self._cache[key] = (section, outline)
        return outline


# ---------------------------------------------------------------------------
# Extrusion
# ---------------------------------------------------------------------------


def unsupported_reason(beam) -> str | None:
    """Why this beam needs the kernel, or None when the extruder can take it.

    Geometry only — whether the SECTION samples is answered by
    :func:`outline_for`, which the caller asks separately so the reason string
    distinguishes the two.
    """

    from ada.api.beams import Beam

    if type(beam) is not Beam:
        # BeamTapered / BeamSweep / BeamRevolve sweep a changing or curved path;
        # none of them is a constant-section prism.
        return type(beam).__name__.removeprefix("Beam").lower() or "subclass"
    if getattr(beam, "booleans", None):
        return "booleans"
    if beam.section is None:
        return "no-section"
    return None


def extrude_beam(beam, outline: SectionOutline) -> tuple[np.ndarray, np.ndarray]:
    """Sweep ``outline`` along ``beam``'s frame into ``(verts, tris)``.

    Vertices are one per outline point per end, shared between the side walls
    and the caps — which is the layout the OCC path only reaches after
    ``_dedup_beam_tessellation`` merges its per-face duplicates, so the GLB, the
    AFEM ranges, the AFBV warp and the frontend see the same kind of buffer
    either way.
    """

    from ada.api.beams.geom_beams import straight_beam_frame

    frame = straight_beam_frame(beam)
    return extrude_outline(outline, frame)


def extrude_outline(outline: SectionOutline, frame) -> tuple[np.ndarray, np.ndarray]:
    """The numpy half of :func:`extrude_beam`, given a resolved ``BeamFrame``.

    The returned triangle array is the outline's shared, read-only one — the
    caller offsets it into the combined buffer, which copies. Do not write to
    it in place.
    """

    pts = outline.points
    n = outline.n_points

    # A profile point (u, v) lands at origin + u*yvec + v*up, and its far-end
    # twin one length along xvec. See BeamFrame: this is the same mapping the
    # kernel's Axis2Placement3D applies to the extruded area solid.
    verts = np.empty((2 * n, 3), dtype=np.float64)
    np.matmul(pts, np.stack([frame.yvec, frame.up]), out=verts[:n])
    verts[:n] += frame.origin
    verts[n:] = verts[:n] + frame.length * frame.xvec

    return verts, outline.triangles
