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

The cap triangulation and the extrusion's connectivity come from
``adacpp.cad.build_extruded_section``: the same libtess2 path the kernel-free
tessellator already runs for every other face, including its shrunk-hole retry
for a void that touches the outer boundary. The viewer expands the compact
artefact through the same C++ (built to wasm), so the triangle list a beam gets
at bake time and the one the browser rebuilds come from one implementation
rather than two that have to be kept agreeing.

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

    ``points`` is the outer boundary first then one ring per void, concatenated
    — the vertex order of ONE extrusion end — and ``ring_slices`` says where
    each ring starts and stops. ``triangles`` indexes the 2n vertices a sweep
    produces: [0, n) is the near end, [n, 2n) the far one.

    Winding is normalised (outer counter-clockwise, every void clockwise), so a
    ring walked in order always has the material on its left and the side quads
    come out with outward normals without a per-beam orientation test.
    """

    points: np.ndarray  # (n, 2) float64, all rings concatenated
    ring_slices: tuple[tuple[int, int], ...]  # (start, stop) into points, outer first
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
    slices: list[tuple[int, int]] = []
    cursor = 0
    for ring in rings:
        slices.append((cursor, cursor + ring.shape[0]))
        cursor += ring.shape[0]

    section_mesh = _section_mesh(rings)
    if section_mesh is None:
        return None
    points, triangles = section_mesh
    if points.shape[0] != cursor:
        # The rings handed over are already open and already wound outer-CCW /
        # void-CW, so the builder's own normalisation is a no-op and the point
        # order it returns is the one we sent. If that ever stops being true the
        # slices below would silently mis-name which ring is which, so fail here
        # instead and let the caller fall back to OCC.
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
        triangles=triangles,
        centroid=(mx / area, my / area),
        area=area,
    )


def _section_mesh(rings) -> tuple[np.ndarray, np.ndarray] | None:
    """Cap triangulation plus the whole extrusion connectivity, from adacpp.

    Connectivity depends on the SECTION, not the beam: vertex ``k`` is outline
    point ``k`` at the near end and ``k + n`` at the far end for every beam
    sharing the profile. Building it once per section rather than once per beam
    leaves the per-beam work at two matrix multiplies.

    ``None`` when the section cannot be meshed — a cap whose triangulation
    introduces vertices that are not outline points cannot be indexed against
    the two rings this layout is built on, and adacpp declines rather than
    return caps that do not meet their own walls. The caller falls back to OCC.
    """

    try:
        from adacpp.cad import build_extruded_section
    except ImportError:
        # unsupported_reason() gates this for the bake, but outline_for is
        # public: answer None rather than raise, which is what every other
        # 'cannot sample this section' path here does.
        return None

    sec = build_extruded_section([[(float(x), float(y)) for x, y in ring] for ring in rings])
    if not sec.ok:
        return None
    points = np.ascontiguousarray(sec.points, dtype=np.float64)
    triangles = np.ascontiguousarray(sec.triangles, dtype=np.uint32)
    triangles.flags.writeable = False  # shared by every beam on this section
    return points, triangles


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


def adacpp_available() -> bool:
    """Whether the section mesher is installed. Cached: asked once per beam.

    ada-py does not depend on ada-cpp, so this is a normal state rather than a
    broken install — it costs the fast path, not the output.
    """

    global _ADACPP_AVAILABLE
    if _ADACPP_AVAILABLE is None:
        try:
            import adacpp.cad  # noqa: F401
        except ImportError:
            _ADACPP_AVAILABLE = False
        else:
            _ADACPP_AVAILABLE = True
    return _ADACPP_AVAILABLE


_ADACPP_AVAILABLE: bool | None = None


def unsupported_reason(beam) -> str | None:
    """Why this beam needs the kernel, or None when the extruder can take it.

    Geometry mostly — whether the SECTION samples is answered by
    :func:`outline_for`, which the caller asks separately so the reason string
    distinguishes the two.

    The exception is a missing adacpp, which is not about this beam at all:
    it is reported here because this is the gate both callers already consult,
    so the bake counts ``occ-fallback[adacpp-unavailable]`` and says plainly
    why every beam took the slow path — rather than each section quietly
    failing to sample for no stated reason.
    """

    from ada.api.beams import Beam

    if not adacpp_available():
        return "adacpp-unavailable"

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
