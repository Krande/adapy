"""A curved member's axis, as the polyline CAE's ``WireSpline`` interpolates.

Phase 1 refused every ``Beam`` subclass, which meant refusing ``BeamCurved``,
``BeamRevolve`` and ``BeamSweep`` -- and those three carry the **exact** curve, not an
approximation of it: ``BeamCurved.curve3d`` is the ngeom curve the Genie SAT body was
authored with (a ``BSplineCurveWithKnots`` for an ACIS intcurve), ``BeamRevolve.curve``
is a circular arc with its centre, axis and radius, and ``BeamSweep.curve`` is a 3D
segment chain. So nothing has to be guessed at; the only question is how to hand that
curve to CAE.

Measured on Abaqus 2025 (``D:\\temp\\cae_ph15\\p1.py``), a spline wire through points on
a quarter circle of radius 2 (exact arc length pi = 3.141592654):

    samples  max turn between chords   built length    relative error
      3          0.785 rad             3.1010594250      1.290e-02
      5          0.393                 3.1372473888      1.383e-03
      9          0.196                 3.1410720146      1.657e-04
     17          0.098                 3.1415283795      2.046e-05
     33          0.049                 3.1415845016      2.595e-06
     65          0.025                 3.1415914811      3.732e-07

Every one of those built **one** edge and two vertices, so a curved member is one wire
however finely it is sampled. The error falls as the cube of the turning angle between
consecutive chords (fit: ``2.2e-02 * turn**3``, which reproduces all five of the rows
above), and that is why :data:`MAX_TURN_RADIANS` is stated as a turning angle rather than
as a sample count: the angle is a property of the curve's shape, so the same rule gives a
sparse sampling for a gentle arc and a dense one for a tight bend, in metres and in
millimetres alike.

``edge.getRadius()`` raises ``AbaqusException`` on a spline edge, so the *built* geometry
is checked against ``edge.getSize()`` -- its arc length -- and against ``findAt`` at every
interior sample point. Both are asserted inside the emitted script.

A ``BeamSweep``'s axis is not one curve, and that is the other half of this module.
``CurveOpen2d`` decomposes a filleted corner into **line, arc, line** -- four ``segments3d``
for three points, the fourth being a closer from the last point back to the first -- so such a
member is sampled as a *chain* of :class:`MemberLeg` by :func:`sample_member_legs` and drawn as
one wire per leg. Splining through the corners would round them off and chording them would
lose them; both are the silent change of shape this writer exists to refuse. Measured on
Abaqus 2025 (``D:\\temp\\cae_probe\\lift\\sweep``), the legs cost nothing in the kernel: three
wires sharing their end points under ``mergeType=IMPRINT`` build ``edges=3 vertices=4`` -- the
junction vertex merging exactly as a real joint's does -- and the mesh puts **one** node at
each junction (15 elements, 16 nodes at a 0.25 seed, 6 / 3 / 6 per leg).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ada import Beam

#: The largest angle, in radians, allowed between two consecutive chords of the emitted
#: sample polyline. At 0.05 rad the measured relative arc-length error of the spline CAE
#: builds is 2.6e-06 (the 33-sample row of the table above, whose 0.049 rad is this
#: criterion applied to a quarter circle), which is two orders inside
#: :data:`CURVE_LENGTH_REL_TOL`. Tightening it costs points in the emitted script and
#: nothing else; loosening it walks towards the chord, which at the 3-sample row is
#: already 1.3% short.
MAX_TURN_RADIANS = 0.05

#: Never fewer than this many points, so a curve whose turning is concentrated in one
#: short stretch is still sampled densely enough for the crossing scan to see it.
MIN_CURVE_POINTS = 9

#: And never more. A curve that cannot be expressed inside this budget at
#: :data:`MAX_TURN_RADIANS` is a curve this writer does not understand -- a cusp, a
#: near-degenerate spline -- and is refused rather than sampled to death.
MAX_CURVE_POINTS = 2049

#: How far the built wire's arc length may differ from the curve adapy sampled, relative.
#: Measured error at :data:`MAX_TURN_RADIANS` is 2.6e-06; a *chord* drawn instead of the
#: quarter arc would be 9.9% short and a wrong curve worse still, so the two failure modes
#: this separates are four orders apart and the tolerance does not have to be delicate.
CURVE_LENGTH_REL_TOL = 1.0e-04


class CurveNotSupported(Exception):
    """This member's axis cannot be handed to CAE as wires -- one spline, or one per leg."""


def is_curved_beam_type(bm: Beam) -> bool:
    """Whether ``bm``'s axis is a curve rather than the straight line between its nodes.

    By exact type, like every other shape test in this writer: a subclass nobody has seen
    must not be waved through as one of these three.
    """
    return type(bm).__name__ in ("BeamCurved", "BeamRevolve", "BeamSweep")


def _turning_angles(points: np.ndarray) -> np.ndarray:
    """The angle between each consecutive pair of chords of ``points``."""
    chords = points[1:] - points[:-1]
    lengths = np.linalg.norm(chords, axis=1)
    keep = lengths > 0.0
    chords = chords[keep]
    lengths = lengths[keep]
    if len(chords) < 2:
        return np.zeros(0)
    units = chords / lengths[:, None]
    dots = np.clip(np.einsum("ij,ij->i", units[:-1], units[1:]), -1.0, 1.0)
    return np.arccos(dots)


def polyline_length(points) -> float:
    arr = np.asarray(points, dtype=float)
    return float(np.linalg.norm(arr[1:] - arr[:-1], axis=1).sum())


#: How closely the extrapolated arc length has to have settled before it is used as the
#: figure CAE's own edge is measured against. Two Richardson steps normally agree to 1e-10;
#: anything above this means the curve is not smooth enough for the extrapolation to mean
#: what it says, and the member is refused rather than compared against a number nobody
#: knows the accuracy of.
ARC_LENGTH_CONVERGENCE = 1.0e-07


def _arc_length(sampler, count: int, snap) -> tuple[float, float]:
    """``(arc length, how far the extrapolation moved on its last step)``.

    The emitted sample polyline's own length is **not** the curve's arc length and must not
    be used as one: a chord polyline is systematically short, by 1.0e-04 relative for the
    33 points a quarter circle gets here -- which is *forty times* the spline's own error, so
    a guard that compared the two would be measuring its own sampling and would sit a hair
    inside its tolerance for no reason. (It did, before this function existed: the arc read
    9.78e-05 against a 1.0e-04 tolerance, and 9.78e-05 is exactly the chord deficit minus
    the spline error.)

    A polyline's length converges on the arc's from below as ``n**-2``, so two refinements
    and a Richardson step give the arc length to about ``n**-4``, and the step between two
    such estimates says whether that is true of this curve.
    """
    lengths = []
    for refinement in (1, 2, 4):
        points = snap(np.asarray(sampler(refinement * (count - 1) + 1), dtype=float))
        lengths.append(polyline_length(points))
    first = (4.0 * lengths[1] - lengths[0]) / 3.0
    second = (4.0 * lengths[2] - lengths[1]) / 3.0
    if second <= 0.0:
        return 0.0, 1.0
    return second, abs(second - first) / second


def _sample_arc(p1, axis_point, axis_dir, angle_rad, count) -> np.ndarray:
    """``count`` points on the circular arc that rotates ``p1`` about an axis.

    Exact: every point is ``p1`` rotated, so there is no fitting anywhere in the
    ``BeamRevolve`` path.
    """
    origin = np.asarray(axis_point, dtype=float)
    unit = np.asarray(axis_dir, dtype=float)
    unit = unit / np.linalg.norm(unit)
    start = np.asarray(p1, dtype=float) - origin
    along = float(np.dot(start, unit)) * unit
    radial = start - along
    perpendicular = np.cross(unit, radial)
    angles = np.linspace(0.0, float(angle_rad), count)
    return origin + along + np.outer(np.cos(angles), radial) + np.outer(np.sin(angles), perpendicular)


def _curve_sampler(bm: Beam):
    """``(callable taking a point count, "why it cannot be sampled")`` for one member.

    Exactly one of the two is ``None``. The refusals name the container rather than the
    beam class, because what decides whether a curve can be drawn as one spline wire is
    the curve, not the class that holds it.
    """
    from ada.geom import curves as gc

    kind = type(bm).__name__
    if kind == "BeamRevolve":
        curve = bm.curve
        if curve is None or curve.radius is None or curve.rot_origin is None or curve.angle is None:
            return None, (
                "its CurveRevolve has no resolved centre, radius or angle, so the arc it stands for "
                "is not determined"
            )
        rot_axis = curve.rot_axis
        if rot_axis is None:
            return None, "its CurveRevolve has no rotation axis"
        # ``CurveRevolve.angle`` is unsigned, and the sense of the sweep is not stated
        # anywhere on the curve: whether the arc runs with or against the rotation axis is
        # decided by which of the two candidate centres adapy picked. So the sign is read
        # off the geometry -- the sweep that lands on the curve's own second point wins --
        # rather than assumed. If neither does, the ends check in sample_member_curve
        # refuses the member instead of drawing an arc to the wrong place.
        size = math.radians(abs(float(curve.angle)))
        target = np.asarray(curve.p2, dtype=float)
        best = min(
            (size, -size),
            key=lambda signed: float(
                np.linalg.norm(_sample_arc(curve.p1, curve.rot_origin, rot_axis, signed, 2)[-1] - target)
            ),
        )
        return (lambda n: _sample_arc(curve.p1, curve.rot_origin, rot_axis, best, n)), None

    if kind == "BeamCurved":
        curve = bm.curve3d
        if curve is None:
            return None, "its curve3d is None, so there is no curve to draw"
        if isinstance(curve, gc.BSplineCurveWithKnots):
            # RationalBSplineCurveWithKnots subclasses this and sample() de-homogenises it.
            return (lambda n: np.asarray(curve.sample(n), dtype=float)), None
        if isinstance(curve, gc.ArcLine):
            return _arc_line_sampler(curve)
        if isinstance(curve, (gc.PolyLine, gc.Line)):
            return None, (
                "its curve3d is a {0}, which is straight or piecewise straight rather than curved; a "
                "spline drawn through the corners of a polyline rounds them off".format(type(curve).__name__)
            )
        return None, "its curve3d is a {0}, a curve container this writer cannot sample".format(type(curve).__name__)

    if kind == "BeamSweep":
        return None, (
            "its axis is a chain of legs rather than one curve, so it is not sampled as one: see "
            "sample_member_legs, which samples a sweep leg by leg"
        )

    return None, "it is a {0}, which is not one of the curved beam types".format(kind)


def _arc_line_sampler(curve):
    """A three-point arc: start, a point on it, end.

    The centre is the circumcentre of the three points, computed in closed form, and the
    sweep is taken **through the midpoint** -- which is the only thing that distinguishes a
    major arc from its minor complement, since the two share all three of their extreme
    points.
    """
    start = np.asarray(curve.start, dtype=float)
    middle = np.asarray(curve.midpoint, dtype=float)
    end = np.asarray(curve.end, dtype=float)
    a = middle - start
    b = end - start
    normal = np.cross(a, b)
    norm_squared = float(np.dot(normal, normal))
    if norm_squared <= 0.0:
        return None, "its three arc points are collinear, so they do not define an arc"
    centre = start + (float(np.dot(a, a)) * np.cross(b, normal) + float(np.dot(b, b)) * np.cross(normal, a)) / (
        2.0 * norm_squared
    )
    normal = normal / norm_squared**0.5
    u = start - centre
    radius = float(np.linalg.norm(u))
    if radius <= 0.0:
        return None, "its arc has zero radius"
    u = u / radius
    v = np.cross(normal, u)

    def angle_of(point):
        radial = np.asarray(point, dtype=float) - centre
        angle = math.atan2(float(np.dot(radial, v)), float(np.dot(radial, u)))
        return angle if angle >= 0.0 else angle + 2.0 * math.pi

    mid_angle = angle_of(middle)
    end_angle = angle_of(end)
    # Angles increase in the +v direction from 0 at the start point. The midpoint lies
    # before the end going that way only if its angle is the smaller of the two; otherwise
    # the arc runs the other way round and the sweep is negative.
    total = end_angle if mid_angle < end_angle else end_angle - 2.0 * math.pi
    return (lambda n: _sample_arc(start, centre, normal, total, n)), None


def _to_global(bm: Beam, points: np.ndarray) -> np.ndarray:
    """``points`` pushed through the owning Part's placement.

    The same three branches as :meth:`ada.Beam.axis_global`, because the curve's points and
    the beam's end nodes have to land in the same frame -- and the sampler checks exactly
    that by comparing its own two ends against ``axis_global()``.
    """
    from ada.api.transforms import Placement
    from ada.core.vector_utils import is_identity_rot_matrix

    if bm.placement.is_identity():
        return points
    absolute = bm.placement.get_absolute_placement(include_rotations=True)
    if not is_identity_rot_matrix(absolute.rot_matrix):
        return absolute.transform_array_from_other_place(points, Placement())
    return absolute.origin + points


def sample_member_curve(bm: Beam, p1, p2, tol: float) -> tuple[tuple[tuple[float, float, float], ...], float]:
    """``(the polyline CAE's WireSpline will interpolate, the curve's arc length)``.

    ``p1``/``p2`` are the member's endpoints from :meth:`ada.Beam.axis_global`, and the
    returned polyline **starts and ends exactly on them**: they are what every other
    member's joint is computed against, and CAE merges two wire points only below 1e-6, so
    a curve whose own first point sat a nanometre away from its neighbour's would build a
    frame that is loose exactly where the model says it is joined.

    The curve is only allowed to be snapped that way if it already agrees: an end further
    than ``tol`` (adapy's own ``general_point_tol``) from the node is a curve that is not
    this member's axis, and is refused rather than dragged into place.

    Raises :class:`CurveNotSupported` with the reason otherwise.
    """
    raw, why = _curve_sampler(bm)
    if raw is None:
        raise CurveNotSupported(why)

    def sampler(n):
        return _to_global(bm, np.asarray(raw(n), dtype=float))

    return refine_and_measure(sampler, p1, p2, tol)


def refine_and_measure(sampler, p1, p2, tol: float) -> tuple[tuple[tuple[float, float, float], ...], float]:
    """``(the polyline to draw, its curve's arc length)`` for one already-global sampler.

    The whole of :func:`sample_member_curve` bar the choice of sampler, so that a **leg** of a
    swept path can be sampled by exactly the code a whole curved member is -- same turning-angle
    criterion, same snapping to the nodes, same Richardson-extrapolated arc length, same
    refusals. ``p1``/``p2`` are the two ends the polyline has to start and finish on: a member's
    nodes for a whole curve, and the leg's own ends for a leg.
    """
    count = MIN_CURVE_POINTS
    points = sampler(count)
    while count < MAX_CURVE_POINTS:
        angles = _turning_angles(points)
        if len(angles) == 0 or float(angles.max()) <= MAX_TURN_RADIANS:
            break
        count = 2 * (count - 1) + 1
        points = sampler(count)
    else:
        raise CurveNotSupported(
            "its axis still turns by more than {0} radians between consecutive samples at {1} points, "
            "which is a cusp or a degenerate curve rather than something a spline wire can "
            "follow".format(MAX_TURN_RADIANS, MAX_CURVE_POINTS)
        )

    if points.shape[0] < 2:
        raise CurveNotSupported("its axis sampled to fewer than two points")

    start = np.asarray(p1, dtype=float)
    end = np.asarray(p2, dtype=float)
    forwards = float(np.linalg.norm(points[0] - start)) + float(np.linalg.norm(points[-1] - end))
    backwards = float(np.linalg.norm(points[0] - end)) + float(np.linalg.norm(points[-1] - start))
    # A curve parameterised from n2 to n1 says nothing about the member, so it is reversed
    # rather than refused. The reversal lives in ``snap`` below so that it is applied once,
    # to the emitted polyline and to every refinement the arc length is measured on alike.
    reversed_curve = backwards < forwards
    oriented = points[::-1] if reversed_curve else points
    worst = max(float(np.linalg.norm(oriented[0] - start)), float(np.linalg.norm(oriented[-1] - end)))
    if worst > tol:
        raise CurveNotSupported(
            "its curve ends {0:g} length units away from the node it is supposed to start or finish at, "
            "which is further than adapy's own point tolerance {1:g}: that curve is not this member's "
            "axis".format(worst, tol)
        )

    def snap(arr):
        """``arr`` put the member's way round, with its ends replaced by the member's nodes.

        The same substitution the emitted polyline gets, so the arc length is measured on
        the curve the script actually draws rather than on the unsnapped one.
        """
        out = arr[::-1].copy() if reversed_curve else arr.copy()
        out[0] = start
        out[-1] = end
        return out

    points = snap(points)
    arc_length, settled = _arc_length(sampler, count, snap)
    if arc_length <= 0.0 or settled > ARC_LENGTH_CONVERGENCE:
        raise CurveNotSupported(
            "its arc length does not settle under refinement -- two Richardson estimates still differ by "
            "{0:g} relative, against {1:g} -- so there is no figure to measure the wire CAE builds "
            "against".format(settled, ARC_LENGTH_CONVERGENCE)
        )
    return tuple(tuple(float(c) for c in point) for point in points), arc_length


@dataclass(frozen=True)
class MemberLeg:
    """One leg of a member's axis, which is one wire in CAE.

    A **straight** leg carries its two ends and nothing else; a **curved** one carries the
    polyline ``WireSpline`` interpolates and that polyline's arc length, exactly as a whole
    curved member does. A member with one curved leg is the ordinary ``BeamCurved`` case; a
    ``BeamSweep`` has one leg per segment of its path.
    """

    p1: tuple[float, float, float]
    p2: tuple[float, float, float]
    #: The polyline to spline through, or ``None`` for a straight leg.
    points: tuple[tuple[float, float, float], ...] | None = None
    #: That polyline's curve's arc length, or ``None`` for a straight leg.
    curve_length: float | None = None

    @property
    def is_curved(self) -> bool:
        return self.points is not None


def _distance(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)))


def _sweep_legs(bm: Beam, p1, p2, tol: float):
    """``(the legs of a BeamSweep's path in member order, None)``, or ``(None, why not)``.

    Two things about ``CurveOpen2d`` shape this, and both were measured on adapy's own
    containers rather than assumed.

    **Its ``segments3d`` carries a closer.** For the three points
    ``[(0,0), (2,0,r=0.5), (2,2)]`` it returns **four** legs: a line from the *last* point back
    to the first, then line, arc, line. The closer is not part of the member's axis -- the
    member runs from ``points3d[0]`` to ``points3d[-1]`` -- so it is identified (the one leg
    whose two ends are the path's two ends) and dropped. A path whose closer cannot be told
    apart that way is refused rather than guessed at; two points give a single leg and no
    closer at all.

    **The legs are not necessarily in path order, or pointing along it.** So the chain is
    walked from the first point, taking at each step the one unused leg that touches where the
    last one ended and turning it round if it points backwards. A step with no candidate is a
    broken path and a step with two is a branch; neither is a member's axis, and both are
    refused with the point they were found at.
    """
    try:
        segments = list(bm.curve.segments3d)
        corners = np.asarray([list(point)[:3] for point in bm.curve.points3d], dtype=float)
    except Exception as exc:  # noqa: BLE001 - any failure to build the path is a refusal
        return None, "its sweep path could not be built: {0}: {1}".format(type(exc).__name__, exc)
    if not segments or len(corners) < 2:
        return None, "its sweep path holds no leg at all"

    ends = _to_global(bm, np.asarray([corners[0], corners[-1]], dtype=float))
    first_point, last_point = ends[0], ends[1]
    legs = []
    for segment in segments:
        span = _to_global(bm, np.asarray([list(segment.p1)[:3], list(segment.p2)[:3]], dtype=float))
        midpoint = getattr(segment, "midpoint", None)
        if midpoint is not None:
            midpoint = _to_global(bm, np.asarray([list(midpoint)[:3]], dtype=float))[0]
        legs.append([span[0], span[1], midpoint])

    if len(legs) > 1:
        closers = [
            index
            for index, (start, end, midpoint) in enumerate(legs)
            if midpoint is None
            and min(
                max(_distance(start, first_point), _distance(end, last_point)),
                max(_distance(start, last_point), _distance(end, first_point)),
            )
            <= tol
        ]
        if len(closers) != 1:
            return None, (
                "its sweep path has {0} legs and {1} of them run between the path's own two ends {2} and "
                "{3}. adapy's CurveOpen2d closes the point list, so exactly one of them is the closer "
                "this writer drops; with {1} there is no telling which leg is part of the member's axis "
                "and which is the closing line".format(
                    len(legs),
                    len(closers),
                    tuple(round(float(c), 6) for c in first_point),
                    tuple(round(float(c), 6) for c in last_point),
                )
            )
        legs.pop(closers[0])

    ordered = []
    current = first_point
    while legs:
        touching = [
            index
            for index, (start, end, _) in enumerate(legs)
            if min(_distance(start, current), _distance(end, current)) <= tol
        ]
        if len(touching) != 1:
            return None, (
                "its sweep path does not chain: {0} of its remaining {1} legs touch {2}, where the leg "
                "before it ended. A member's axis is one chain of legs from end to end, so nothing here "
                "says which way it runs".format(len(touching), len(legs), tuple(round(float(c), 6) for c in current))
            )
        start, end, midpoint = legs.pop(touching[0])
        if _distance(end, current) < _distance(start, current):
            start, end = end, start
        ordered.append((start, end, midpoint))
        current = end

    node1 = np.asarray(p1, dtype=float)
    node2 = np.asarray(p2, dtype=float)
    forwards = _distance(ordered[0][0], node1) + _distance(ordered[-1][1], node2)
    backwards = _distance(ordered[0][0], node2) + _distance(ordered[-1][1], node1)
    if backwards < forwards:
        # Which way round the path was authored says nothing about the member, so it is turned
        # rather than refused -- the same rule sample_member_curve applies to a single curve.
        ordered = [(end, start, midpoint) for start, end, midpoint in reversed(ordered)]
    worst = max(_distance(ordered[0][0], node1), _distance(ordered[-1][1], node2))
    if worst > tol:
        return None, (
            "its sweep path ends {0:g} length units away from the node it is supposed to start or finish "
            "at, which is further than adapy's own point tolerance {1:g}: that path is not this member's "
            "axis".format(worst, tol)
        )

    from ada.geom.curves import ArcLine

    out = []
    count = len(ordered)
    carried = None
    for index, (start, end, midpoint) in enumerate(ordered):
        # The member's own nodes at the two far ends, exactly: every joint in the part is
        # computed against them, and CAE merges two wire points only below 1e-6. At a junction
        # the previous leg's own end point is reused rather than recomputed, so the two wires
        # share their point to the last bit instead of to within the tolerance they chained at.
        leg_start = tuple(float(c) for c in (node1 if index == 0 else start))
        if carried is not None:
            leg_start = carried
        leg_end = tuple(float(c) for c in (node2 if index == count - 1 else end))
        if midpoint is None:
            out.append(MemberLeg(p1=leg_start, p2=leg_end))
        else:
            sampler, why = _arc_line_sampler(ArcLine(start=leg_start, midpoint=midpoint, end=leg_end))
            if sampler is None:
                return None, "leg {0} of {1} of its sweep path: {2}".format(index + 1, count, why)
            try:
                points, arc_length = refine_and_measure(sampler, leg_start, leg_end, tol)
            except CurveNotSupported as exc:
                return None, "leg {0} of {1} of its sweep path: {2}".format(index + 1, count, exc)
            out.append(MemberLeg(p1=points[0], p2=points[-1], points=points, curve_length=arc_length))
        carried = out[-1].p2
    return tuple(out), None


def sample_member_legs(bm: Beam, p1, p2, tol: float) -> tuple[MemberLeg, ...]:
    """A curved member's axis as one :class:`MemberLeg` per wire CAE has to draw.

    ``BeamCurved`` and ``BeamRevolve`` are one curve and therefore one curved leg -- this is
    :func:`sample_member_curve` under another name. A ``BeamSweep`` is a *chain*: adapy's
    ``CurveOpen2d`` decomposes a filleted corner into line, arc, line, and each of those is its
    own CAE wire and its own edge. Drawing the chain as one spline would round its corners off,
    which is the silent misrepresentation the shape guard exists to prevent, so the legs stay
    legs and the writer draws one wire, and one bounding volume, for each.

    What that costs is nothing in CAE, measured: three wires sharing their end points build
    ``edges=3 vertices=4`` under ``mergeType=IMPRINT`` -- the junction vertex merges, exactly as
    a real joint does -- and the mesh puts **one** node at each junction (Abaqus 2025,
    ``D:\\temp\\cae_probe\\lift\\sweep``). So a swept member is one member whose sub-edge count
    is its number of legs, which is a count the topology guard can state.

    Raises :class:`CurveNotSupported` with the reason if the path cannot be drawn.
    """
    if type(bm).__name__ != "BeamSweep":
        points, arc_length = sample_member_curve(bm, p1, p2, tol)
        return (MemberLeg(p1=points[0], p2=points[-1], points=points, curve_length=arc_length),)
    legs, why = _sweep_legs(bm, p1, p2, tol)
    if legs is None:
        raise CurveNotSupported(why)
    return legs


__all__ = [
    "ARC_LENGTH_CONVERGENCE",
    "CURVE_LENGTH_REL_TOL",
    "MAX_CURVE_POINTS",
    "MAX_TURN_RADIANS",
    "MIN_CURVE_POINTS",
    "CurveNotSupported",
    "MemberLeg",
    "is_curved_beam_type",
    "polyline_length",
    "refine_and_measure",
    "sample_member_curve",
    "sample_member_legs",
]
