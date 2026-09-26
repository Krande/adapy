"""What topology a set of wires *should* build in CAE, computed on the adapy side.

This exists because the writer's original guard 1 ("every edge ends with exactly one
section assignment") cannot see connectivity at all. Measured on Abaqus 2025 with the
writer's own wire-building calls::

    T-joint, IMPRINT (correct)        edges=3 vertices=4     guard 1 passes
    T-joint, SEPARATE (disconnected)  edges=2 vertices=4     guard 1 PASSES
    brace 1e-6 off the girder line    edges=2 vertices=4     guard 1 PASSES
    collinear pair, gap 0             edges=2 vertices=3     guard 1 passes
    collinear pair, gap 1e-6          edges=2 vertices=4     guard 1 PASSES

A disconnected frame yields the same "every edge is sectioned" verdict as a connected
one, and two collinear members that failed to join do not even change the *edge* count --
only the vertex count moves. So the only way to tell a connected frame from a pile of
loose sticks is to state the expected topology independently, from the adapy model, and
make the emitted script compare the kernel against it.

Measured merge behaviour, which is what the numbers here are calibrated against
(``D:\\temp\\cae_t1\\probe.py``, Abaqus 2025):

* CAE merges two wire points when they are closer than :data:`CAE_MERGE_TOL` = 1e-6, and
  not at 1e-6 exactly. 9e-7 merges; 1e-6 does not.
* That threshold is **absolute in model units and independent of the part's size**: the
  same cut-off held for a 0.004-long member, a 4-long member and a 4000-long member.
  This is ACIS' ``SPAresabs``, a kernel constant, not a function of the model. It is the
  one tolerance in this writer that must *not* be a fraction of a member's length -- the
  cylinder fractions in ``writer.py`` scale because they are about telling neighbouring
  members apart, which is a question about the model; this one is not.

The comparison tolerance itself is deliberately **looser** than the kernel's -- see
:func:`expected_topology` -- and is adapy's own ``Config().general_point_tol``.

Everything below is plain float arithmetic rather than numpy, deliberately: the pair
scan is the only part of the writer whose cost grows with the square of the model, and
per-call numpy overhead on 3-vectors dominated it (780 members took 7.9 s, and the same
work in floats takes 0.2 s). numpy is kept for the line/line solve, which runs only on
the few pairs that survive the box filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Measured: Abaqus 2025 merges two wire points iff they are strictly closer than this,
#: in model units, at every part size probed (0.004, 4 and 4000 units long). Used here
#: only to *explain* a mismatch in the emitted script's failure message -- nothing
#: branches on it, because a guard that assumed the kernel's tolerance would go quiet the
#: day the kernel changed it.
CAE_MERGE_TOL = 1e-06

#: Two members count as parallel when ``sin(angle)`` between them is below 1e-06, i.e.
#: ``sin**2`` below this. Below that the line/line closest-approach solve is
#: ill-conditioned and the pair is handled as a collinear-overlap question instead.
_PARALLEL_SIN_SQUARED = 1e-12


@dataclass(frozen=True)
class Segment:
    """One member, in the coordinates the script will draw it at.

    ``points`` is ``None`` for a straight member, whose whole geometry is ``p1 -> p2``. A
    **curved** member carries the polyline CAE's ``WireSpline`` interpolates, whose first
    and last entries are ``p1`` and ``p2``. Everything in this module that asks "where does
    this member go" has to read :attr:`path` rather than the two ends, because a curve's
    ends say nothing about what it passes near -- which is the whole difficulty a curved
    member adds to the topology guard.
    """

    name: str
    p1: tuple[float, float, float]
    p2: tuple[float, float, float]
    points: tuple[tuple[float, float, float], ...] | None = None

    @property
    def is_curved(self) -> bool:
        return self.points is not None

    @property
    def path(self) -> tuple[tuple[float, float, float], ...]:
        """Every point of the member's geometry: two for a straight one, the samples for a curve."""
        if self.points is None:
            return (self.p1, self.p2)
        return self.points


@dataclass(frozen=True)
class Crossing:
    """Two members that meet somewhere other than at an end of at least one of them."""

    #: ``"crossing"`` -- they pass through each other at a point interior to both;
    #: ``"overlap"`` -- they are collinear and share a length, not just a point;
    #: ``"on_curve"`` -- one of them touches the *interior* of a curved member.
    kind: str
    first: str
    second: str
    point: tuple[float, float, float]

    def describe(self) -> str:
        where = "(" + ", ".join("{0:.12g}".format(c) for c in self.point) + ")"
        if self.kind == "overlap":
            return "{0!r} and {1!r} are collinear and overlap along their length, from about {2}".format(
                self.first, self.second, where
            )
        if self.kind == "on_curve":
            return "{0!r} meets the curved member {1!r} at about {2}, away from that curve's ends".format(
                self.first, self.second, where
            )
        return "{0!r} and {1!r} cross at about {2}, which is interior to both".format(self.first, self.second, where)


@dataclass(frozen=True)
class PartTopology:
    """The geometry one CAE part must end up holding, if it built what adapy described."""

    #: ``{set name: number of sub-edges the member is expected to be split into}``.
    edges_per_member: dict[str, int]
    #: Distinct vertices in the part. The count that catches a *joint* that did not
    #: merge: two collinear members that failed to join keep the same edge count and gain
    #: a vertex, so nothing else sees them.
    vertices: int
    #: ``{set name: the points that split it}``, for the sake of a message that can say
    #: where a missing split was expected.
    splits: dict[str, tuple[tuple[float, float, float], ...]] = field(default_factory=dict)

    @property
    def edges(self) -> int:
        total = 0
        for name in sorted(self.edges_per_member):
            total += self.edges_per_member[name]
        return total


def _distance(a, b) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _unit_axis(segment: Segment):
    """``(start, unit direction, length)`` for one member."""
    start = segment.p1
    end = segment.p2
    direction = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
    length = (direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2) ** 0.5
    return start, (direction[0] / length, direction[1] / length, direction[2] / length), length


def _point_inside(point, segment: Segment, tol: float):
    """Where ``point`` lands on ``segment``, or ``None`` if it is not strictly inside it.

    "Strictly inside" means further than ``tol`` from *both* ends: a member that lands on
    another's endpoint shares a vertex with it and splits nothing, which is the ordinary
    corner of a frame and must not be mistaken for a T-joint.
    """
    start, unit, length = _unit_axis(segment)
    rx = point[0] - start[0]
    ry = point[1] - start[1]
    rz = point[2] - start[2]
    along = rx * unit[0] + ry * unit[1] + rz * unit[2]
    if along <= tol or along >= length - tol:
        return None
    ox = rx - along * unit[0]
    oy = ry - along * unit[1]
    oz = rz - along * unit[2]
    if (ox * ox + oy * oy + oz * oz) ** 0.5 > tol:
        return None
    return along


def _box(segment: Segment, tol: float):
    """The member's axis-aligned bounding box, grown by ``tol``.

    Over the member's whole :attr:`~Segment.path`, not its two ends: an arc bulges outside
    the box of its chord (a quarter circle of radius 2 reaches 0.59 beyond it), so a box
    taken from the endpoints would filter out the very pair a brace-on-the-arc crossing
    lives in and the refusal would go quiet.
    """
    path = segment.path
    low = []
    high = []
    for axis in range(3):
        values = [point[axis] for point in path]
        low.append(min(values) - tol)
        high.append(max(values) + tol)
    return tuple(low), tuple(high)


def _candidate_pairs(ordered, tol: float) -> list[tuple[int, int]]:
    """Index pairs whose bounding boxes overlap, as ``(lower name, higher name)``.

    Found by sweeping in x rather than by comparing every pair: a structural model spreads
    its members out, so the sweep touches a small window of each. The worst case -- every
    member sharing one x range -- is still quadratic, and that is inherent: whether two
    members meet is a question about pairs.

    Returned in name order so the crossings reported from it, and therefore the refusal
    message, are a function of the model and not of the sweep.
    """
    boxes = [_box(segment, tol) for segment in ordered]
    sweep = sorted(range(len(ordered)), key=lambda index: (boxes[index][0][0], ordered[index].name))
    pairs = []
    for position, index in enumerate(sweep):
        reach = boxes[index][1][0]
        for other in sweep[position + 1 :]:
            if boxes[other][0][0] > reach:
                # sweep is sorted by the low x edge, so nothing later can reach back either.
                break
            low_a, high_a = boxes[index]
            low_b, high_b = boxes[other]
            if (
                low_a[1] > high_b[1]
                or low_b[1] > high_a[1]
                or low_a[2] > high_b[2]
                or low_b[2] > high_a[2]
                or low_a[0] > high_b[0]
                or low_b[0] > high_a[0]
            ):
                continue
            pairs.append((index, other) if ordered[index].name <= ordered[other].name else (other, index))
    return sorted(pairs, key=lambda pair: (ordered[pair[0]].name, ordered[pair[1]].name))


def distinct_points(points, tol: float) -> list[tuple[float, float, float]]:
    """One representative per distinct position in ``points``, at ``tol``.

    A greedy clustering, over an explicitly sorted sequence so the answer is a function
    of the positions and not of the order they were collected in. Greedy clustering can
    chain (a close to b, b close to c, a not close to c) and then the count depends on the
    order after all -- which cannot arise in the models this is used on, because adapy
    shares one ``Node`` object between coincident endpoints, so a real joint is *exactly*
    coincident rather than merely within tolerance.

    The representatives themselves, and not merely how many there are, because a boundary
    condition or a load has to be matched against the *positions* CAE will hold a vertex at
    -- see :func:`ada.cadit.cae.analysis.plan_analysis`. Counting and listing come from one
    function so the two answers cannot drift apart.
    """
    ordered = sorted(tuple(float(c) for c in point) for point in points)
    representatives: list[tuple[float, float, float]] = []
    first = 0
    for point in ordered:
        # `representatives` is appended in the same x order as `ordered`, so everything
        # before `first` is already more than tol behind in x and can never match again.
        while first < len(representatives) and representatives[first][0] < point[0] - tol:
            first += 1
        matched = False
        for rep in representatives[first:]:
            if _distance(rep, point) <= tol:
                matched = True
                break
        if not matched:
            representatives.append(point)
    return representatives


def count_distinct_points(points, tol: float) -> int:
    """How many distinct positions are in ``points``, at ``tol``. See :func:`distinct_points`."""
    return len(distinct_points(points, tol))


def find_crossings(segments, tol: float) -> list[Crossing]:
    """Pairs of members that meet away from both of their ends.

    CAE imprints such a meeting into a shared vertex exactly as it does a real T-joint --
    measured, an X of two 4-long members built ``edges=4 vertices=5`` -- which silently
    welds two members the adapy model does not join. The writer refuses those rather than
    asserting them; see :func:`expected_topology` for why.
    """
    ordered = sorted(segments, key=lambda s: s.name)
    crossings: list[Crossing] = []
    for first, second in _candidate_pairs(ordered, tol):
        crossing = _pair_crossing(ordered[first], ordered[second], tol)
        if crossing is not None:
            crossings.append(crossing)
    return crossings


def _segment_to_polyline(start, direction, others_start, others_direction):
    """Closest approach of one segment to each of ``others``, clamped to both ends.

    Returns ``(distance, s, t)`` arrays, with ``s`` and ``t`` in [0, 1] along the first
    segment and along each of the others. The standard clamped segment/segment solve, done
    branchlessly with ``np.where`` so it runs over a whole polyline at once.

    The textbook form of this solve has a hole, and a brute-force check over random and
    awkward pairs found it: for a **zero-length** other segment its parallel branch pins
    ``s = 0`` and then neither ``t`` clamp fires, so it reports the distance from this
    segment's *start* to the point rather than from the segment. A duplicated sample point in
    a curve's polyline is exactly that case, and the consequence would be a contact the
    refusal silently misses -- so a degenerate other segment is solved as point-to-segment at
    the end instead.
    """
    u = np.asarray(direction, dtype=float)
    w = np.asarray(start, dtype=float) - others_start
    v = others_direction
    a = float(np.dot(u, u))
    b = v @ u
    c = np.einsum("ij,ij->i", v, v)
    d = w @ u
    e = np.einsum("ij,ij->i", w, v)
    denominator = a * c - b * b
    tiny = np.finfo(float).eps * (1.0 + a) * (1.0 + c)
    parallel = denominator <= tiny

    s_num = np.where(parallel, 0.0, b * e - c * d)
    s_den = np.where(parallel, 1.0, denominator)
    t_num = np.where(parallel, e, a * e - b * d)
    t_den = np.where(parallel, np.where(c > 0.0, c, 1.0), denominator)

    # s below its low end / above its high end: pin s and re-solve t on the pinned line.
    below = s_num < 0.0
    above = s_num > s_den
    t_num = np.where(below, e, np.where(above, e + b, t_num))
    t_den = np.where(below | above, np.where(c > 0.0, c, 1.0), t_den)
    s_num = np.where(below, 0.0, np.where(above, s_den, s_num))

    # ... then the same for t, which can move s again.
    t_low = t_num < 0.0
    t_high = t_num > t_den
    s_low_pin = -d
    s_high_pin = -d + b
    s_num = np.where(
        t_low,
        np.where(s_low_pin < 0.0, 0.0, np.where(s_low_pin > a, s_den, s_low_pin)),
        np.where(
            t_high,
            np.where(s_high_pin < 0.0, 0.0, np.where(s_high_pin > a, s_den, s_high_pin)),
            s_num,
        ),
    )
    s_den = np.where(
        (t_low & (s_low_pin >= 0.0) & (s_low_pin <= a)) | (t_high & (s_high_pin >= 0.0) & (s_high_pin <= a)),
        a if a > 0.0 else 1.0,
        s_den,
    )
    t_num = np.where(t_low, 0.0, np.where(t_high, t_den, t_num))

    s = np.where(s_den == 0.0, 0.0, s_num / s_den)
    t = np.where(t_den == 0.0, 0.0, t_num / t_den)
    # An other-segment of zero length is a point: project it onto this segment instead.
    point_like = c <= 0.0
    if point_like.any():
        projection = np.clip(-d / a, 0.0, 1.0) if a > 0.0 else np.zeros_like(c)
        s = np.where(point_like, projection, s)
        t = np.where(point_like, 0.0, t)
    offset = w + s[:, None] * u - t[:, None] * v
    return np.linalg.norm(offset, axis=1), s, t


def _path_arrays(segment: Segment):
    """``(points, chord directions, cumulative arc length, total length)`` for one member."""
    points = np.asarray(segment.path, dtype=float)
    directions = points[1:] - points[:-1]
    lengths = np.linalg.norm(directions, axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
    return points, directions, cumulative, float(cumulative[-1])


def _curve_contact(first: Segment, second: Segment, tol: float) -> Crossing | None:
    """Refuse any contact that lands on the *interior* of a curved member.

    Measured on Abaqus 2025: a straight wire whose end lands on the midpoint of a spline
    wire imprints it -- the part went from 2 edges / 3 vertices to **3 edges / 4 vertices**.
    So a curve can be split exactly as a straight member can, but *where* the split falls
    along it is decided by the spline's own parameterisation, which nothing on adapy's side
    knows. A curved member's sub-edge count therefore cannot be predicted from its geometry
    the way a straight member's can, and this writer's standing rule for something it cannot
    state is to refuse it rather than approximate it -- the same rule that refuses a straight
    crossing and a tapered member.

    The prize for refusing is that every model the writer *does* accept has an exact
    prediction for every member, curved ones included: one sub-edge each. A curved member
    never turns the per-member guard off for the part it is in.

    A contact at a curve's own **end** is ordinary and is not reported: that is a joint, and
    if it lands inside a straight member the existing arithmetic predicts that member's
    split.
    """
    points_a, dirs_a, cumulative_a, length_a = _path_arrays(first)
    points_b, dirs_b, cumulative_b, length_b = _path_arrays(second)
    if length_a <= 0.0 or length_b <= 0.0:
        return None
    starts_b = points_b[:-1]
    for index in range(len(dirs_a)):
        distance, s, t = _segment_to_polyline(points_a[index], dirs_a[index], starts_b, dirs_b)
        close = np.flatnonzero(distance <= tol)
        for other in close:
            along_a = cumulative_a[index] + s[other] * float(np.linalg.norm(dirs_a[index]))
            along_b = cumulative_b[other] + t[other] * float(np.linalg.norm(dirs_b[other]))
            for segment, along, length, partner in (
                (first, along_a, length_a, second),
                (second, along_b, length_b, first),
            ):
                if not segment.is_curved:
                    continue
                if tol < along < length - tol:
                    where = points_a[index] + s[other] * dirs_a[index]
                    return Crossing(
                        kind="on_curve",
                        first=partner.name,
                        second=segment.name,
                        point=tuple(float(c) for c in where),
                    )
    return None


def _pair_crossing(first: Segment, second: Segment, tol: float) -> Crossing | None:
    if first.is_curved or second.is_curved:
        return _curve_contact(first, second, tol)
    a = np.asarray(first.p1, dtype=float)
    b = np.asarray(first.p2, dtype=float)
    c = np.asarray(second.p1, dtype=float)
    d = np.asarray(second.p2, dtype=float)
    u, v, w = b - a, d - c, a - c
    uu = float(np.dot(u, u))
    vv = float(np.dot(v, v))
    uv = float(np.dot(u, v))
    denominator = uu * vv - uv * uv
    if denominator <= _PARALLEL_SIN_SQUARED * uu * vv:
        return _parallel_overlap(first, second, tol)
    uw = float(np.dot(u, w))
    vw = float(np.dot(v, w))
    s = (uv * vw - vv * uw) / denominator
    t = (uu * vw - uv * uw) / denominator
    on_first = a + s * u
    on_second = c + t * v
    if float(np.linalg.norm(on_first - on_second)) > tol:
        return None
    # In arc length, so the margin is the same tolerance everything else uses.
    for parameter, squared in ((s, uu), (t, vv)):
        length = squared**0.5
        along = parameter * length
        if along <= tol or along >= length - tol:
            return None
    midpoint = (on_first + on_second) / 2.0
    return Crossing(
        kind="crossing",
        first=first.name,
        second=second.name,
        point=tuple(float(x) for x in midpoint),
    )


def _parallel_overlap(first: Segment, second: Segment, tol: float) -> Crossing | None:
    """Collinear members sharing a *length*, which the split arithmetic cannot describe.

    Two stacked columns are collinear and touch at one point; that is ordinary and is not
    an overlap. Sharing a span is not: measured, two collinear 4-long members overlapping
    by 2 built ``edges=3``, where counting a split per intruding endpoint would predict 4,
    and there is no honest reading of "which sub-edges belong to which member" for the
    shared stretch. A duplicated member is the same case with a full overlap.
    """
    start, unit, length = _unit_axis(first)
    positions = []
    for point in (second.p1, second.p2):
        rx = point[0] - start[0]
        ry = point[1] - start[1]
        rz = point[2] - start[2]
        along = rx * unit[0] + ry * unit[1] + rz * unit[2]
        ox = rx - along * unit[0]
        oy = ry - along * unit[1]
        oz = rz - along * unit[2]
        if (ox * ox + oy * oy + oz * oz) ** 0.5 > tol:
            return None
        positions.append(along)
    low = max(0.0, min(positions))
    high = min(length, max(positions))
    if high - low <= tol:
        return None
    return Crossing(
        kind="overlap",
        first=first.name,
        second=second.name,
        point=(start[0] + low * unit[0], start[1] + low * unit[1], start[2] + low * unit[2]),
    )


def expected_topology(segments, tol: float) -> PartTopology:
    """The sub-edge and vertex counts one CAE part must have, if CAE built what adapy said.

    A member becomes ``1 + (the number of distinct other endpoints strictly inside it)``
    sub-edges, because CAE imprints a landing member into the through member (measured:
    1 edge -> 3 for a girder plus a brace at its midspan). Interior *crossings* would add
    splits too; they are refused by the writer instead of being counted here -- see
    :func:`find_crossings` -- so a crossing that slipped past that refusal shows up here
    as sub-edges the kernel built and adapy did not predict, which is the failure this
    guard is for.

    ``tol`` is adapy's own ``Config().general_point_tol`` (1e-4 by default) and is
    therefore two orders of magnitude *looser* than the kernel's own 1e-6 merge tolerance.
    That asymmetry is the point, and it is deliberate in this direction:

    * closer than 1e-6: adapy calls it a joint and CAE merges it. They agree; it passes.
    * between 1e-6 and ``tol``: adapy calls it a joint -- this is the tolerance its own
      FEM node container merges nodes at, so the INP route *would* build it connected --
      and CAE will not merge it. The two Abaqus routes would disagree about whether the
      frame is connected, so the emitted script fails and names the member. Nothing here
      "fixes" the geometry: a 1e-5 miss is reported, not snapped.
    * further than ``tol``: neither adapy nor CAE calls it a joint, so no split is
      expected and none is built. A brace that stops a millimetre short of a girder is a
      gap in the model, and this writer's job is to reproduce the model.

    A user who tightens ``general_point_tol`` below 1e-6 inverts the middle band: CAE
    would then merge joints adapy does not, and the same guard fires from the other side,
    reporting sub-edges the kernel built that adapy did not predict.

    **Curved members** are stated as exactly **one** sub-edge, and that is a prediction
    rather than an exemption. A curve's endpoints say nothing about what it crosses, and a
    spline's sub-edge count under imprinting is not a function of anything adapy holds --
    where along the spline CAE would place an imprinted vertex is the spline's own
    parameterisation's business. So instead of guessing, the writer **refuses** any model in
    which something touches a curved member away from its ends
    (:func:`_curve_contact`), and every model that survives that refusal has a curve that
    CAE builds as one edge -- measured, at every sample count from 3 to 65 points. A curved
    member's own endpoints still split the straight members they land inside, by the same
    arithmetic as any other endpoint, so adding a curve to a part does not weaken the
    prediction for anything else in it.
    """
    ordered = sorted(segments, key=lambda s: s.name)
    endpoints: list[tuple[float, float, float]] = []
    landing: dict[str, list[tuple[float, float, float]]] = {}
    for segment in ordered:
        endpoints.append(tuple(float(c) for c in segment.p1))
        endpoints.append(tuple(float(c) for c in segment.p2))
        landing[segment.name] = []

    for first, second in _candidate_pairs(ordered, tol):
        # Both directions: a brace landing on a girder splits the girder, and the same
        # pair read the other way round splits nothing.
        for through, lander in ((ordered[first], ordered[second]), (ordered[second], ordered[first])):
            if through.is_curved:
                # A curved through-member's splits are refused by the writer, not counted
                # here -- see _curve_contact. So anything reaching this point has already
                # been shown not to touch the curve away from its ends, and the curve is
                # one sub-edge.
                continue
            for point in (lander.p1, lander.p2):
                if _point_inside(point, through, tol) is not None:
                    landing[through.name].append(tuple(float(c) for c in point))

    splits: dict[str, tuple] = {}
    edges_per_member: dict[str, int] = {}
    for segment in ordered:
        splits[segment.name] = tuple(sorted(landing[segment.name]))
        edges_per_member[segment.name] = 1 + count_distinct_points(landing[segment.name], tol)

    every_point = list(endpoints)
    for name in sorted(splits):
        every_point += list(splits[name])
    return PartTopology(
        edges_per_member=edges_per_member,
        vertices=count_distinct_points(every_point, tol),
        splits=splits,
    )


__all__ = [
    "CAE_MERGE_TOL",
    "Crossing",
    "PartTopology",
    "Segment",
    "count_distinct_points",
    "distinct_points",
    "expected_topology",
    "find_crossings",
]
