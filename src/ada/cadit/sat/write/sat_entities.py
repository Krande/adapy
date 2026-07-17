from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import ada
import ada.geom.direction
from ada.cadit.sat.utils import make_ints_if_possible


@dataclass
class SATEntity:
    id: int

    def to_string(self) -> str:
        raise NotImplementedError("Each entity must implement its string representation.")


@dataclass
class Body(SATEntity):
    lump: Lump
    bbox: list[float]

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        return f"-{self.id} body $-1 -1 -1 $-1 ${self.lump.id} $-1 $-1 T {bbox_str} #"


@dataclass
class Lump(SATEntity):
    shell: Shell
    body: Body
    bbox: list[float]
    next_lump: Lump = None

    def to_string(self) -> str:
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        next_lump = -1 if self.next_lump is None else self.next_lump.id
        return f"-{self.id} lump $-1 -1 -1 $-1 ${next_lump} ${self.shell.id} ${self.body.id} T {bbox_str} #"


@dataclass
class Shell(SATEntity):
    face: Face
    lump: Lump
    bbox: list[float]
    wire: Wire = None

    def to_string(self) -> str:
        # ACIS `shell` record: $next_shell $subshell $first_face $first_wire $lump.
        # The wire pointer heads the chain of wire bodies — the edges that bound
        # no face (a beam with no plate under its axis).
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        face = -1 if self.face is None else self.face.id
        wire = -1 if self.wire is None else self.wire.id
        return f"-{self.id} shell $-1 -1 -1 $-1 $-1 $-1 ${face} ${wire} ${self.lump.id} T {bbox_str} #"


@dataclass
class Wire(SATEntity):
    """A connected collection of edges that bound no face (SAT v4.0 ch.7).

    Genie emits one per group of beams whose axes lie on no plate, hung off the
    shell's wire pointer, so those beams still have ACIS geometry to reference.
    """

    coedge: CoEdge
    shell: Shell
    bbox: list[float]
    next_wire: Wire = None

    def to_string(self) -> str:
        # $next_wire $first_coedge $body_or_shell $subshell <containment>
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        next_wire = -1 if self.next_wire is None else self.next_wire.id
        return (
            f"-{self.id} wire $-1 -1 -1 $-1 ${next_wire} ${self.coedge.id} " f"${self.shell.id} $-1 out T {bbox_str} #"
        )


@dataclass
class Face(SATEntity):
    loop: Loop
    shell: Shell
    name: StringAttribName
    surface: PlaneSurface
    next_face: Face = None
    # Whether the face's normal runs with its surface's or against it. A
    # spline-surface carries a sense of its own and Genie puts the flip there,
    # leaving every spline face forward; a plane-surface has none, so a plane
    # face carries its own (reversed on 112 of a hull export's).
    sense: Literal["forward", "reversed"] = "forward"
    # The face's 3D bounding box (6 floats) and, for a spline face, its UV
    # parameter box (4 floats). When present they are stated explicitly — Genie
    # rejects a face whose U/V range it cannot determine ("U or V range of the
    # face cannot be determined or bad"), and states them on every one of its own.
    bbox3d: list = None
    param_box: list = None

    def to_string(self) -> str:
        # ACIS `face` record (SAT v4.0 spec, ch.6): after the common ENTITY prefix
        # ($attrib -1 -1 $owner) come next_face_in_shell, first_loop, shell,
        # subshell, surface. A shell holds ONE face pointer; the rest of its faces
        # are reached by following next_face, so the chain must be linked. The tail
        # is `out T <3D box> <T <param box> | F>` when the boxes are known, else the
        # legacy `out F F` (ACIS then recomputes them, which it cannot always do).
        next_face = -1 if self.next_face is None else self.next_face.id
        if self.bbox3d:
            box = " ".join(str(x) for x in make_ints_if_possible(self.bbox3d))
            if self.param_box:
                pbox = "T " + " ".join(str(x) for x in make_ints_if_possible(self.param_box))
            else:
                pbox = "F"
            tail = f"T {box} {pbox}"
        else:
            tail = "F F"
        return (
            f"-{self.id} face ${self.name.id} -1 -1 $-1 ${next_face} ${self.loop.id} "
            f"${self.shell.id} $-1 ${self.surface.id} {self.sense} double out {tail} #"
        )


@dataclass
class Loop(SATEntity):
    coedge: CoEdge
    bbox: list[float]
    face: Face = None
    next_loop: Loop = None
    periphery_plane: PlaneSurface = None

    def to_string(self) -> str:
        # ACIS `loop` record: $next_loop $first_coedge $face — the last field is
        # the face this loop bounds, NOT a constant. It used to be hardcoded to
        # `$3`, which only ever happened to be right for a single-plate model
        # (whose face is entity 3); every loop of every other model pointed at
        # the wrong entity.
        bbox_str = " ".join(str(coord) for coord in self.bbox)
        periphery = "unknown"
        if self.periphery_plane is not None:
            periphery = f"periphery ${self.periphery_plane.id} F"
        face_ref = -1 if self.face is None else self.face.id
        # A face points at its first loop only; any hole loops hang off
        # next_loop (an imprint can enclose a region and produce them).
        next_loop = -1 if self.next_loop is None else self.next_loop.id
        return (
            f"-{self.id} loop $-1 -1 -1 $-1 ${next_loop} ${self.coedge.id} ${face_ref} " f"T {bbox_str} {periphery} #"
        )


@dataclass
class Vertex(SATEntity):
    edge: Edge
    point: SatPoint
    attrib: VertEdgeAttribute = None

    def to_string(self) -> str:
        # A vertex names one of its edges — but only when that names the vertex
        # unambiguously. Where its edges fall into separable regions there is no
        # single right answer, so the pointer goes null and a VertEdgeAttribute
        # names one edge per region instead (as Genie writes it).
        attrib = -1 if self.attrib is None else self.attrib.id
        edge = -1 if self.edge is None else self.edge.id
        return f"-{self.id} vertex ${attrib} -1 -1 $-1 ${edge} ${self.point.id} #"


@dataclass
class VertEdgeAttribute(SATEntity):
    """One edge pointer per separable manifold region at a non-manifold vertex.

    SAT v4.0 ch.7 ``vertedge`` (ATTRIB_VERTEDGE : ATTRIB_SYS : ATTRIB): "Contains
    a list of edge pointers ... At nonmanifold vertices, there should be a
    pointer to an edge in each separable manifold region."

    Where a beam's axis runs off the plate it lies on, the vertex at the plate
    boundary carries both the plate's face edges and the wire edge for the free
    run. Nothing joins those two regions, so ACIS cannot reach one from the
    other and the model fails verification with "vertex has edge in multiple
    groups" unless they are declared here.
    """

    vertex: Vertex
    edges: list[Edge]

    # Genie always writes four slots, padding with $-1, whatever the region
    # count — matched rather than trimmed to len(edges), since this is an ACIS
    # system attribute and its own exports are the only worked example.
    slots: int = 4

    def to_string(self) -> str:
        slots = max(self.slots, len(self.edges))
        refs = [f"${e.id}" for e in self.edges] + ["$-1"] * (slots - len(self.edges))
        return (
            f"-{self.id} vertedge-sys-attrib $-1 -1 $-1 $-1 ${self.vertex.id} "
            f"1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 {slots} {' '.join(refs)} #"
        )


@dataclass
class SatPoint(SATEntity):
    point: ada.Point

    def to_string(self) -> str:
        point_str = " ".join(str(x) for x in make_ints_if_possible(self.point))
        return f"-{self.id} point $-1 -1 -1 $-1 {point_str} #"


@dataclass
class CoEdge(SATEntity):
    next_coedge: CoEdge
    prev_coedge: CoEdge
    edge: Edge
    loop: Loop | Wire  # a coedge is owned by a loop, or by a wire when it bounds no face
    orientation: Literal["forward", "reversed"]
    partner: CoEdge = None
    # The coedge's curve in its face's parameter space. Stays None on a planar
    # face, whose edges need none; a coedge on a spline face is unusable without
    # one (see PCurve).
    pcurve: PCurve = None

    def to_string(self) -> str:
        # ACIS `coedge` record: $next_in_loop $prev_in_loop $next_coedge_on_edge
        # $edge <sense> $loop $pcurve. The third pointer is the partner ring: all
        # coedges lying on the same edge form a circular list through it. An edge
        # used by a single face leaves it $-1 (as Genie writes it); an edge shared
        # by two faces links the pair to each other.
        partner = -1 if self.partner is None else self.partner.id
        pcurve = -1 if self.pcurve is None else self.pcurve.id
        return (
            f"-{self.id} coedge $-1 -1 -1 $-1 ${self.next_coedge.id} ${self.prev_coedge.id} "
            f"${partner} ${self.edge.id} {self.orientation} ${self.loop.id} ${pcurve} #"
        )


@dataclass
class Edge(SATEntity):
    vertex_start: Vertex
    vertex_end: Vertex
    coedge: CoEdge
    straight_curve: StraightCurve

    start_pt: ada.Point
    end_pt: ada.Point
    attrib_name: StringAttribName = None
    # The curve's parameters at the two vertices. A straight-curve is
    # parameterised by arc length from its start, so 0..length is right and is
    # the default; nothing else is. On a circle it is the angle, on a b-spline
    # the knot value, and guessing it from the endpoints is ambiguous — a closed
    # curve passes through any two points twice. SAT records them on every edge,
    # so pass the authored pair through rather than re-deriving it.
    t_start: float = None
    t_end: float = None

    def to_string(self) -> str:
        attrib_ref = "-1"
        if self.attrib_name:
            attrib_ref = self.attrib_name.id
        # The trailing `T <box>` is a bounding box, so it must run min-corner then
        # max-corner. Emitting start_pt/end_pt verbatim inverted the box for any
        # edge running backwards along an axis.
        lo = [min(a, b) for a, b in zip(self.start_pt, self.end_pt)]
        hi = [max(a, b) for a, b in zip(self.start_pt, self.end_pt)]
        bbox_str = " ".join(str(x) for x in make_ints_if_possible([*lo, *hi]))
        if self.t_start is None or self.t_end is None:
            vec = ada.Direction(self.end_pt - self.start_pt)
            s1 = 0
            s2 = make_ints_if_possible([vec.get_length()])[0]
        else:
            s1 = make_ints_if_possible([self.t_start])[0]
            s2 = make_ints_if_possible([self.t_end])[0]
        return (
            f"-{self.id} edge ${attrib_ref} -1 -1 $-1 ${self.vertex_start.id} {s1} "
            f"${self.vertex_end.id} {s2} ${self.coedge.id} ${self.straight_curve.id} "
            f"forward @7 unknown T {bbox_str} #"
        )


@dataclass
class StraightCurve(SATEntity):
    start_pt: ada.Point
    direction: ada.Direction

    def to_string(self) -> str:
        start_str = " ".join([str(x) for x in make_ints_if_possible(self.start_pt)])
        direction_str = " ".join([str(x) for x in make_ints_if_possible(self.direction.get_normalized())])
        return f"-{self.id} straight-curve $-1 -1 -1 $-1 {start_str} {direction_str} I I #"


@dataclass
class PlaneSurface(SATEntity):
    centroid: ada.Point
    normal: ada.Direction
    xvec: ada.Direction

    def to_string(self) -> str:
        centroid_str = " ".join([str(x) for x in make_ints_if_possible(self.centroid)])
        normal_str = " ".join([str(x) for x in make_ints_if_possible(self.normal)])
        xvec_str = " ".join([str(x) for x in make_ints_if_possible(self.xvec)])
        return f"-{self.id} plane-surface $-1 -1 -1 $-1 {centroid_str} {normal_str} {xvec_str} forward_v I I I I #"


def _num(x: float) -> str:
    """A real, as ACIS writes them: repr, but integral values without the '.0'."""
    f = float(x)
    return str(int(f)) if f == int(f) and abs(f) < 1e15 else repr(f)


def _lines(*segments: str) -> str:
    """Join a subtype body's fields the way ACIS lays one out on disk.

    The three records that carry a ``{ ... }`` body are not written on one line:
    the header, the knot vector, each control point and each trailing field get
    a line of their own, tab-indented, with a trailing space. That is what every
    Genie export looks like, and it is not cosmetic — the reader is line-
    oriented (``extract_data_lines`` collects lines until one carries a ``}``,
    then indexes the knots and control points by line), so a body squeezed onto
    one line reads back as no data at all. The single-line records (edge,
    coedge, ellipse-curve, straight-curve) have no body and stay as they are.
    """
    return " \n\t".join(segments)


def _acis_knots(knots: list[float], multiplicities: list[int], degree: int) -> str:
    """``value mult`` pairs, in ACIS's knot convention.

    IFC (and STEP) give a knot vector of ``n_ctrl + degree + 1`` entries. ACIS
    stores ``n_ctrl + degree - 1``: it drops one knot from each end, so the end
    multiplicities are one lower — degree rather than degree+1 for a clamped
    curve. Checked against a Genie export: a degree-2 u with IFC mults [3, 3] is
    written [2, 2], a degree-3 v with [4, 4] is written [3, 3], and its own
    control-point count only comes out right under this reading
    (``n_ctrl = sum(mults) - degree + 1``).
    """
    if len(knots) != len(multiplicities):
        raise ValueError(f"{len(knots)} knots but {len(multiplicities)} multiplicities")
    mults = list(multiplicities)
    mults[0] -= 1
    mults[-1] -= 1
    if any(m < 1 for m in mults):
        raise ValueError(f"knot multiplicities {multiplicities} too low for degree {degree}")
    return " ".join(f"{_num(k)} {m}" for k, m in zip(knots, mults))


@dataclass
class SplineSurface(SATEntity):
    """A NURBS patch — ACIS ``spline-surface``, an ``exactsur`` spl_sur.

    Written to carry a :class:`~ada.geom.surfaces.RationalBSplineSurfaceWithKnots`
    (what the SAT reader hands back for a Genie ``curved_shell``) unchanged, so a
    curved plate survives a round trip instead of degrading to its boundary
    polygon.

    The non-data tokens are fixed exactly as Genie writes them — ``both open open
    none none`` and the trailing ``0 0 0 0 0 0 0 F 1 F 0 F 1 F 0`` are identical
    across every surface in a reference export, whatever the degree or grid.
    """

    surface: object  # RationalBSplineSurfaceWithKnots
    sense: Literal["forward", "reversed"] = "forward"

    def subtype(self) -> str:
        """The ``{ ... }`` spl_sur body, also embedded inside a pcurve."""
        s = self.surface
        n_u = len(s.control_points_list)
        n_v = len(s.control_points_list[0])

        u_knots = _acis_knots(s.u_knots, s.u_multiplicities, s.u_degree)
        v_knots = _acis_knots(s.v_knots, s.v_multiplicities, s.v_degree)

        weights = getattr(s, "weights_data", None)
        # Control points as `x y z w`, u varying fastest — the transpose of the
        # [u][v] grid the geometry holds. Checked against a Genie export: its
        # first run of points is n_u long and carries the 1, cos(t/2), 1 weights
        # of a degree-2 rational arc, which is the u direction.
        pts = []
        for iv in range(n_v):
            for iu in range(n_u):
                p = s.control_points_list[iu][iv]
                w = weights[iu][iv] if weights is not None else 1.0
                pts.append(f"{_num(p[0])} {_num(p[1])} {_num(p[2])} {_num(w)}")

        return _lines(
            f"{{ exactsur full nurbs {s.u_degree} {s.v_degree} both open open none none "
            f"{len(s.u_knots)} {len(s.v_knots)}",
            u_knots,
            v_knots,
            *pts,
            *(["0"] * 7),
            "F 1 F 0 F 1 F 0 }",
        )

    def to_string(self) -> str:
        return f"-{self.id} spline-surface $-1 -1 -1 $-1 {self.sense} {self.subtype()} I I I I #"


@dataclass
class EllipseCurve(SATEntity):
    """A circle or ellipse — ACIS ``ellipse-curve``.

    The major axis is a *vector*: its direction is the circle's reference
    direction and its length the major radius. ``ratio`` is minor/major, so 1 is
    a circle — which is all a Genie hull export contains (every one of its
    ellipse-curves has ratio 1).

    ``t_start``/``t_end`` are the angular range in radians about the axis,
    measured from the reference direction, and must ASCEND: ACIS writes the
    range in the curve's own direction and lets each coedge's sense say which
    way its loop runs it, so an edge traversed backwards still records an
    ascending range. Checked against a Genie export — every edge there ascends,
    and the range on the curve equals the edge's own (4171 of 4241; the rest are
    arcs that were split, where both halves keep the range of the arc they came
    from). They are trim data rather than a property of the circle, so the caller
    supplies them (see :func:`circle_param_of`).
    """

    circle: object  # geom.curves.Circle | Ellipse
    t_start: float
    t_end: float

    def __post_init__(self):
        if self.t_end < self.t_start:
            raise ValueError(
                f"ellipse range must ascend, got ({self.t_start}, {self.t_end}) — "
                "the coedge sense carries the direction, not the curve"
            )

    def to_string(self) -> str:
        import numpy as np

        c = self.circle
        pos = c.position
        radius = getattr(c, "radius", None)
        if radius is None:  # an ellipse: semi_axis1 is the major radius
            radius = c.semi_axis1
            ratio = c.semi_axis2 / c.semi_axis1
        else:
            ratio = 1.0
        major = np.asarray(pos.ref_direction, dtype=float) * float(radius)

        centre = " ".join(_num(x) for x in pos.location)
        normal = " ".join(_num(x) for x in pos.axis)
        major_s = " ".join(_num(x) for x in major)
        return (
            f"-{self.id} ellipse-curve $-1 -1 -1 $-1 {centre} {normal} {major_s} "
            f"{_num(ratio)} F {_num(self.t_start)} F {_num(self.t_end)} #"
        )


@dataclass
class IntCurve(SATEntity):
    """A B-spline edge curve — ACIS ``intcurve-curve``, an ``exactcur`` int_cur.

    "Exact" means the spline IS the curve, rather than an approximation of some
    other construction: the ``null_surface``/``nullbs`` run says it lies on no
    surface and needs no approximating data. Genie writes the same form for the
    great majority of its edges (5134 of 6284 in a hull export; the rest are
    ``lawintcur``, which encodes a procedural curve as a law expression and is
    not synthesised here).

    A rational curve is written ``nurbs``, with a weight after each control
    point, the way a rational surface carries its weights. Genie's own exactcurs
    are all ``nubs``, but its ``parcur`` records — a curve in the parameter space
    of a surface — are rational, and reach here as a
    ``RationalBSplineCurveWithKnots`` with real weights (the 1, cos(t/2), 1 of a
    degree-2 arc). Writing one as nubs would drop them and move the curve.
    """

    curve: object  # geom.curves.BSplineCurveWithKnots | RationalBSplineCurveWithKnots
    sense: Literal["forward", "reversed"] = "forward"
    fit_tolerance: float = 0.0

    def to_string(self) -> str:
        c = self.curve
        weights = getattr(c, "weights_data", None)
        if weights and len(weights) != len(c.control_points_list):
            raise ValueError(f"{len(weights)} weights for {len(c.control_points_list)} control points")

        knots = _acis_knots(c.knots, c.knot_multiplicities, c.degree)
        if weights:
            pts = [f"{_num(p[0])} {_num(p[1])} {_num(p[2])} {_num(w)}" for p, w in zip(c.control_points_list, weights)]
        else:
            pts = [f"{_num(p[0])} {_num(p[1])} {_num(p[2])}" for p in c.control_points_list]

        # The two surface slots stay null: this curve is the spline, not an
        # approximation of a curve on a surface. Genie's own exactcurs name the
        # surface they lie on in the first slot; ours has none to name.
        return _lines(
            f"-{self.id} intcurve-curve $-1 -1 -1 $-1 {self.sense} "
            f"{{ exactcur full {'nurbs' if weights else 'nubs'} {c.degree} open {len(c.knots)}",
            knots,
            *pts,
            _num(self.fit_tolerance),
            "null_surface",
            "null_surface",
            "nullbs",
            "nullbs",
            "-1",
            "-1",
            "I I",
            "0",
            "0",
            "0",
            "",
            "-1",
            "none F F 1 F 0 } I I #",
        )


def circle_param_of(circle, point) -> float:
    """The ACIS parameter of ``point`` on ``circle`` — its angle, in radians.

    Measured about the circle's axis from its reference direction, the same
    convention the edge's start/end parameters use. Wrapped to [0, 2*pi) so a
    point just short of the reference direction reads as ~2*pi rather than a
    small negative.
    """
    import numpy as np

    pos = circle.position
    centre = np.asarray(pos.location, dtype=float)
    axis = np.asarray(pos.axis, dtype=float)
    ref = np.asarray(pos.ref_direction, dtype=float)
    axis = axis / np.linalg.norm(axis)
    ref = ref - axis * float(ref @ axis)  # the reference need not be orthogonal
    ref = ref / np.linalg.norm(ref)
    other = np.cross(axis, ref)

    v = np.asarray(point, dtype=float) - centre
    angle = float(np.arctan2(v @ other, v @ ref))
    return angle + 2.0 * np.pi if angle < 0 else angle


@dataclass
class PCurve(SATEntity):
    """A coedge's curve in its face's parameter space — ACIS ``pcurve``/``exppc``.

    A coedge on a spline face names one in the slot that stays ``$-1`` on a
    planar face: without it ACIS has no 2D curve for the edge and the face is not
    usable. The surface the curve lives on is embedded in the record; Genie emits
    ``{ ref n }`` to share one between pcurves, which this does not do yet — the
    surface is written out in full each time, which is larger but self-contained.
    """

    pcurve: object  # Pcurve2dBSpline
    surface: SplineSurface
    sense: Literal["forward", "reversed"] = "forward"

    def to_string(self) -> str:
        pc = self.pcurve
        pts = [f"{_num(u)} {_num(v)}" for u, v in pc.control_points_2d]
        knots = _acis_knots(pc.knots, pc.knot_multiplicities, pc.degree)
        # `nubs`: a rational pcurve would be `nurbs` and carry weights. The
        # reader has never produced one, so refuse rather than drop the weights.
        if getattr(pc, "weights", None):
            raise NotImplementedError("rational pcurve (weights) is not supported yet")
        return _lines(
            f"-{self.id} pcurve $-1 -1 -1 $-1 0 {self.sense} {{ exppc nubs {pc.degree} open {len(pc.knots)}",
            knots,
            *pts,
            _num(pc.fit_tolerance),
            "-1",
            f"spline {self.surface.sense} {self.surface.subtype()} I I I I",
            "} 0 0 #",
        )


@dataclass
class StringAttribName(SATEntity):
    name: str
    entity: SATEntity
    attrib_ref: CachedPlaneAttribute | FusedFaceAttribute | FusedEdgeAttribute = None

    def to_string(self) -> str:
        cache_attrib = -1 if self.attrib_ref is None else self.attrib_ref.id
        return f"-{self.id} string_attrib-name_attrib-gen-attrib $-1 -1 ${cache_attrib} $-1 ${self.entity.id} 2 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 1 1 @6 dnvscp @12 {self.name} #"


@dataclass
class CachedPlaneAttribute(SATEntity):
    entity: SATEntity
    name: StringAttribName
    centroid: ada.Point
    normal: ada.Direction

    def to_string(self) -> str:
        centroid_str = " ".join([str(x) for x in make_ints_if_possible(self.centroid)])
        normal_str = " ".join([str(x) for x in make_ints_if_possible(self.normal)])
        if isinstance(self.entity, int):
            entity = self.entity
        else:
            entity = self.entity.id
        return f"-{self.id} CachedPlaneAttribute-DNV-attrib $-1 -1 $-1 ${self.name.id} ${entity} 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 {centroid_str} {normal_str} 1 #"


@dataclass
class PositionAttribName(SATEntity):
    position_attrib: PositionAttribName
    fused_face_attrib: FusedFaceAttribute
    face: Face
    face_bbox: list[float]
    box_attrib: Literal["ExactBoxLow", "ExactBoxHigh"]

    def to_string(self) -> str:
        if self.box_attrib == "ExactBoxLow":
            box_attrib = "@11 ExactBoxLow " + " ".join([str(x) for x in self.face_bbox[:3]])
        else:
            box_attrib = "@12 ExactBoxHigh " + " ".join([str(x) for x in self.face_bbox[3:]])

        return f"-{self.id} position_attrib-name_attrib-gen-attrib $-1 -1 ${self.position_attrib.id} ${self.fused_face_attrib.id} ${self.face.id} 2 0 0 0 1 1 1 1 1 1 1 1 1 1 0 1 1 1 {box_attrib} #"


@dataclass
class FusedFaceAttribute(SATEntity):
    name: StringAttribName
    posattrib: PositionAttribName
    face: Face

    def to_string(self) -> str:
        return f"-{self.id} FusedFaceAttribute-DNV-attrib $-1 -1 ${self.posattrib.id} ${self.name.id} ${self.face.id} 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 F 1 0 0 #"


@dataclass
class FusedEdgeAttribute(SATEntity):
    name: StringAttribName
    entity: SATEntity
    edge_idx: int
    edge_seq: tuple[int, int]
    edge_length: int | float

    def to_string(self) -> str:
        length = make_ints_if_possible([self.edge_length])[0]
        edge_spec = f"{self.edge_seq[0]} {self.edge_seq[1]} {self.edge_idx} 0 {length}"
        return f"-{self.id} FusedEdgeAttribute-DNV-attrib $-1 -1 $-1 ${self.name.id} ${self.entity.id} 1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1 1 {edge_spec} #"
