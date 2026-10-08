"""Read a planar face back out of the BREP text either CAD kernel writes.

``CadBackend.serialize`` is the one form of a shape both kernels share: OCCT's own
``CASCADE Topology`` text, written by pythonocc and by adacpp alike. Reading it here, in
plain Python, is what lets code that needs a face's exact boundary -- which curve each edge
runs on, its parameter range, which way it is used -- run on adacpp, where there is no
pythonocc to walk the topology with (Krande/adapy#435).

This reads the subset a planar face needs and refuses the rest by name
(:class:`BrepTextUnsupported`): the locations, the 3D curves (every kind is parsed so the
section can be walked; lines, circles and B-splines are converted), the surfaces (walked; a
plane is converted), and the face/wire/edge/vertex records.

What the text cannot give back exactly is said here rather than discovered later: OCCT writes
geometry (curves, surfaces) with 17 significant digits, which round-trips a double, but the
topology records -- an edge's parameter range, a vertex's point -- with 15. An edge's ends are
therefore evaluated from its curve at the written parameters, as a kernel would evaluate them,
and differ from the kernel's own by the 15-digit rounding of the parameter (measured against
pythonocc on the plate-boolean cases, 3.6e-15 at worst: see
``tests/core/cadit/sat/test_write_plate_booleans.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

#: The section headers of the text, in the order OCCT writes them.
_SECTIONS = (
    "Locations",
    "Curve2ds",
    "Curves",
    "Polygon3D",
    "PolygonOnTriangulations",
    "Surfaces",
    "Triangulations",
    "TShapes",
)

_CURVE_KINDS = {
    1: "line",
    2: "circle",
    3: "ellipse",
    4: "parabola",
    5: "hyperbola",
    6: "Bezier curve",
    7: "B-spline curve",
    8: "trimmed curve",
    9: "offset curve",
}

_SURFACE_KINDS = {
    1: "plane",
    2: "cylinder",
    3: "cone",
    4: "sphere",
    5: "torus",
    6: "surface of linear extrusion",
    7: "surface of revolution",
    8: "Bezier surface",
    9: "B-spline surface",
    10: "rectangular trimmed surface",
    11: "offset surface",
}

#: A location that is a rotation and translation leaves lengths alone to this, relative. A
#: located shape that scales is refused: a line's parameter would scale with it.
_RIGID_TOL = 1e-12


class BrepTextUnsupported(ValueError):
    """The BREP text holds something this reader does not convert -- named in the message."""


class _Tokens:
    def __init__(self, text: str):
        self._t = text.split()
        self.i = 0

    def next(self) -> str:
        tok = self._t[self.i]
        self.i += 1
        return tok

    def int(self) -> int:
        return int(self.next())

    def float(self) -> float:
        return float(self.next())

    def floats(self, n: int) -> list[float]:
        out = [float(x) for x in self._t[self.i : self.i + n]]
        if len(out) != n:
            raise BrepTextUnsupported("the BREP text ends inside a record")
        self.i += n
        return out


# --- geometry ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    location: np.ndarray
    direction: np.ndarray

    def value(self, t: float) -> np.ndarray:
        return self.location + t * self.direction


@dataclass(frozen=True)
class Circle:
    centre: np.ndarray
    axis: np.ndarray
    x_dir: np.ndarray
    y_dir: np.ndarray
    radius: float

    def value(self, t: float) -> np.ndarray:
        return self.centre + self.radius * (np.cos(t) * self.x_dir + np.sin(t) * self.y_dir)


@dataclass(frozen=True)
class BSpline:
    degree: int
    poles: np.ndarray  # (n, 3)
    weights: np.ndarray | None
    knots: list[float]
    multiplicities: list[int]
    periodic: bool

    def value(self, t: float) -> np.ndarray:
        knots = np.repeat(np.asarray(self.knots, dtype=float), np.asarray(self.multiplicities, dtype=int))
        w = self.weights if self.weights is not None else np.ones(len(self.poles))
        cp = [np.append(p * wi, wi) for p, wi in zip(self.poles, w)]
        deg = self.degree
        k = int(np.searchsorted(knots, t, side="right") - 1)
        k = max(deg, min(k, len(cp) - 1))
        d = [cp[j + k - deg].copy() for j in range(deg + 1)]
        for r in range(1, deg + 1):
            for j in range(deg, r - 1, -1):
                lo, hi = knots[j + k - deg], knots[j + 1 + k - r]
                a = 0.0 if hi == lo else (t - lo) / (hi - lo)
                d[j] = (1.0 - a) * d[j - 1] + a * d[j]
        return d[deg][:3] / d[deg][3]


@dataclass(frozen=True)
class _Unconverted:
    kind: str


def _read_curve(tk: _Tokens):
    kind = tk.int()
    if kind == 1:
        p, d = tk.floats(3), tk.floats(3)
        return Line(np.array(p), np.array(d))
    if kind == 2:
        c, n, x, y = (np.array(tk.floats(3)) for _ in range(4))
        return Circle(c, n, x, y, tk.float())
    if kind in (3, 5):
        tk.floats(12 + 2)
        return _Unconverted(_CURVE_KINDS[kind])
    if kind == 4:
        tk.floats(12 + 1)
        return _Unconverted(_CURVE_KINDS[kind])
    if kind == 6:
        rational, degree = tk.int(), tk.int()
        tk.floats((degree + 1) * (4 if rational else 3))
        return _Unconverted(_CURVE_KINDS[kind])
    if kind == 7:
        rational, periodic, degree, n_poles, n_knots = (tk.int() for _ in range(5))
        poles, weights = [], []
        for _ in range(n_poles):
            poles.append(tk.floats(3))
            if rational:
                weights.append(tk.float())
        knots, mults = [], []
        for _ in range(n_knots):
            knots.append(tk.float())
            mults.append(tk.int())
        return BSpline(degree, np.array(poles), np.array(weights) if rational else None, knots, mults, bool(periodic))
    if kind == 8:
        tk.floats(2)
        # An edge's range is on the basis curve's parameter, so the trim adds nothing to it.
        return _read_curve(tk)
    if kind == 9:
        tk.floats(1 + 3)
        _read_curve(tk)
        return _Unconverted(_CURVE_KINDS[kind])
    raise BrepTextUnsupported(f"a 3D curve of BREP type {kind}")


def _skip_curve2d(tk: _Tokens) -> None:
    kind = tk.int()
    sizes = {1: 4, 2: 7, 3: 8, 4: 7, 5: 8}  # line, circle, ellipse, parabola, hyperbola
    if kind in sizes:
        tk.floats(sizes[kind])
    elif kind == 6:
        rational, degree = tk.int(), tk.int()
        tk.floats((degree + 1) * (3 if rational else 2))
    elif kind == 7:
        rational, _periodic, _degree, n_poles, n_knots = (tk.int() for _ in range(5))
        tk.floats(n_poles * (3 if rational else 2) + 2 * n_knots)
    elif kind == 8:
        tk.floats(2)
        _skip_curve2d(tk)
    elif kind == 9:
        tk.floats(1)
        _skip_curve2d(tk)
    else:
        raise BrepTextUnsupported(f"a 2D curve of BREP type {kind}")


@dataclass(frozen=True)
class Plane:
    location: np.ndarray
    axis: np.ndarray
    x_dir: np.ndarray


def _read_surface(tk: _Tokens):
    kind = tk.int()
    analytic = {1: 0, 2: 1, 3: 2, 4: 1, 5: 2}
    if kind in analytic:
        frame = [np.array(tk.floats(3)) for _ in range(4)]
        tk.floats(analytic[kind])
        if kind == 1:
            return Plane(frame[0], frame[1], frame[2])
        return _Unconverted(_SURFACE_KINDS[kind])
    if kind == 6:
        tk.floats(3)
        _read_curve(tk)
    elif kind == 7:
        tk.floats(6)
        _read_curve(tk)
    elif kind == 8:
        u_rational, v_rational, u_degree, v_degree = (tk.int() for _ in range(4))
        per_pole = 4 if (u_rational or v_rational) else 3
        tk.floats((u_degree + 1) * (v_degree + 1) * per_pole)
    elif kind == 9:
        u_rational, v_rational, _up, _vp, _ud, _vd, n_u, n_v, n_uk, n_vk = (tk.int() for _ in range(10))
        per_pole = 4 if (u_rational or v_rational) else 3
        tk.floats(n_u * n_v * per_pole + 2 * n_uk + 2 * n_vk)
    elif kind == 10:
        tk.floats(4)
        _read_surface(tk)
    elif kind == 11:
        tk.floats(1)
        _read_surface(tk)
    else:
        raise BrepTextUnsupported(f"a surface of BREP type {kind}")
    return _Unconverted(_SURFACE_KINDS[kind])


# --- locations -----------------------------------------------------------------------------


def _read_locations(tk: _Tokens, n: int) -> list[np.ndarray]:
    """4x4 matrices, index 0 the identity, as ``TopTools_LocationSet`` reads them back."""
    locs = [np.eye(4)]
    for _ in range(n):
        kind = tk.int()
        if kind == 1:
            m = np.eye(4)
            m[:3, :] = np.array(tk.floats(12)).reshape(3, 4)
        elif kind == 2:
            m = np.eye(4)
            ref = tk.int()
            while ref != 0:
                power = tk.int()
                m = np.linalg.matrix_power(locs[ref], power) @ m
                ref = tk.int()
        else:
            raise BrepTextUnsupported(f"a location of BREP type {kind}")
        locs.append(m)
    return locs


def _is_identity(m: np.ndarray) -> bool:
    return bool(np.array_equal(m, np.eye(4)))


def _check_rigid(m: np.ndarray) -> None:
    r = m[:3, :3]
    if not np.allclose(r.T @ r, np.eye(3), rtol=0.0, atol=_RIGID_TOL):
        raise BrepTextUnsupported("a located shape whose location is not a rotation and translation")


def _move_point(m: np.ndarray, p: np.ndarray) -> np.ndarray:
    return m[:3, :3] @ p + m[:3, 3]


def _move(m: np.ndarray, geom):
    if _is_identity(m):
        return geom
    _check_rigid(m)
    r = m[:3, :3]
    if isinstance(geom, Line):
        return Line(_move_point(m, geom.location), r @ geom.direction)
    if isinstance(geom, Circle):
        return Circle(_move_point(m, geom.centre), r @ geom.axis, r @ geom.x_dir, r @ geom.y_dir, geom.radius)
    if isinstance(geom, BSpline):
        poles = np.array([_move_point(m, p) for p in geom.poles])
        return BSpline(geom.degree, poles, geom.weights, geom.knots, geom.multiplicities, geom.periodic)
    if isinstance(geom, Plane):
        return Plane(_move_point(m, geom.location), r @ geom.axis, r @ geom.x_dir)
    return geom


# --- topology ------------------------------------------------------------------------------


@dataclass
class _TShape:
    kind: str
    lines: list[str]
    children: list[tuple[str, int, int]] = field(default_factory=list)  # (orientation, ref, location)


def _read_tshapes(lines: list[str]) -> list[_TShape]:
    shapes: list[_TShape] = []
    i = 0
    while i < len(lines):
        head = lines[i].strip()
        i += 1
        if not head:
            continue
        shape = _TShape(head, [])
        # geometry lines up to the 7-digit flag line
        while not (len(lines[i].strip()) == 7 and set(lines[i].strip()) <= {"0", "1"}):
            shape.lines.append(lines[i])
            i += 1
        i += 1
        toks: list[str] = []
        while "*" not in toks:
            toks += lines[i].split()
            i += 1
        toks = toks[: toks.index("*")]
        for k in range(0, len(toks), 2):
            tok = toks[k]
            shape.children.append((tok[0], int(tok[1:]), int(toks[k + 1])))
        shapes.append(shape)
    return shapes


def _compose(parent: str, child: str) -> str:
    """``TopAbs::Compose``: a reversed parent reverses a forward or reversed child."""
    if parent == "-" and child in "+-":
        return "+" if child == "-" else "-"
    return child


@dataclass(frozen=True)
class FaceEdge:
    """One edge as its wire uses it, in global coordinates.

    ``curve`` is the edge's 3D curve, ``first``/``last`` its range on it, ``forward`` whether
    the face uses it along the curve. ``start``/``end`` are the vertex identities the face
    runs it from and to (equal for an edge that runs a whole closed curve).
    """

    curve: object
    first: float
    last: float
    forward: bool
    start: tuple[int, bytes]
    end: tuple[int, bytes]


@dataclass(frozen=True)
class PlanarFace:
    """A planar face's plane and wires, in the order the text stores them.

    ``plane`` is the face's surface as stored (its axis need not be the face's normal);
    ``reversed`` is the face's own orientation. Each wire lists its edges in the order the face
    runs them, each starting where the one before it ends.
    """

    plane: Plane
    reversed: bool
    wires: list[list[FaceEdge]]


def _split_sections(text: str) -> dict[str, tuple[int, list[str]]]:
    lines = text.splitlines()
    starts = []
    for i, line in enumerate(lines):
        words = line.split()
        if len(words) == 2 and words[0] in _SECTIONS and words[1].isdigit():
            starts.append((i, words[0], int(words[1])))
    out = {}
    for k, (i, name, n) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        out[name] = (n, lines[i + 1 : end])
    return out


def read_planar_face(text: str) -> PlanarFace:
    """The face the BREP text holds as its root, read into global coordinates.

    Raises :class:`BrepTextUnsupported` naming what it met that it does not convert: a root
    that is not a face, a face that is not on a plane, a degenerate or internal edge, a
    location that scales.
    """
    sections = _split_sections(text)
    version = re.search(r"CASCADE Topology V(\d+)", text)
    if "TShapes" not in sections or version is None:
        raise BrepTextUnsupported("text that is not OCCT BREP")
    # In V2 alone a curve on a surface carries the UV points of its ends: measured, adacpp's
    # serialize_brep (V2) writes them and both kernels' serialize (V3) does not.
    uv_points = int(version.group(1)) == 2

    n_loc, loc_lines = sections.get("Locations", (0, []))
    locs = _read_locations(_Tokens("\n".join(loc_lines)), n_loc)

    n_curves, curve_lines = sections.get("Curves", (0, []))
    tk = _Tokens("\n".join(curve_lines))
    curves = [None] + [_read_curve(tk) for _ in range(n_curves)]

    n_surf, surf_lines = sections.get("Surfaces", (0, []))
    tk = _Tokens("\n".join(surf_lines))
    surfaces = [None] + [_read_surface(tk) for _ in range(n_surf)]

    n_shapes, shape_lines = sections["TShapes"]
    # The root reference ("+1 0": orientation, last record, location) follows the last record.
    # The shape-set text alone (adacpp's serialize_brep) ends at the record: there the root is
    # the last record, as stored -- forward and unlocated.
    body = list(shape_lines)
    while body and not body[-1].strip():
        body.pop()
    root_tok = body.pop().split() if not body[-1].rstrip().endswith("*") else ["+1", "0"]
    shapes = _read_tshapes(body)
    if len(shapes) != n_shapes:
        raise BrepTextUnsupported(f"a TShapes section of {n_shapes} records that reads as {len(shapes)}")

    def shape(ref: int) -> _TShape:
        # a reference counts back from the last record written
        return shapes[n_shapes - ref]

    root_orient, root_ref, root_loc = root_tok[0][0], int(root_tok[0][1:]), int(root_tok[1])
    face = shape(root_ref)
    if face.kind != "Fa":
        raise BrepTextUnsupported(f"a root shape of kind {face.kind!r}, not a face")
    face_m = locs[root_loc]

    # "natural-restriction tolerance surface location"
    _nat, _tol, surf_idx, surf_loc = face.lines[0].split()[:4]
    plane = surfaces[int(surf_idx)]
    if not isinstance(plane, Plane):
        what = plane.kind if isinstance(plane, _Unconverted) else "an unknown surface"
        raise BrepTextUnsupported(f"a face on a {what}, not a plane")
    plane = _move(face_m @ locs[int(surf_loc)], plane)

    wires: list[list[FaceEdge]] = []
    for w_orient, w_ref, w_loc in face.children:
        wire = shape(w_ref)
        if wire.kind != "Wi":
            raise BrepTextUnsupported(f"a face whose child is a {wire.kind!r}, not a wire")
        w_orient = _compose(root_orient, w_orient)
        w_m = face_m @ locs[w_loc]
        edges = []
        for e_orient, e_ref, e_loc in wire.children:
            orient = _compose(w_orient, e_orient)
            if orient not in "+-":
                raise BrepTextUnsupported("an internal or external edge in a wire")
            edges.append(_face_edge(shape(e_ref), orient == "+", w_m @ locs[e_loc], locs, curves, uv_points))
        wires.append(_connect(edges))
    return PlanarFace(plane, root_orient == "-", wires)


def _face_edge(edge: _TShape, forward: bool, m: np.ndarray, locs, curves, uv_points: bool) -> FaceEdge:
    if edge.kind != "Ed":
        raise BrepTextUnsupported(f"a wire whose child is a {edge.kind!r}, not an edge")
    _tol, _same_param, _same_range, degenerated = edge.lines[0].split()[:4]
    if degenerated != "0":
        raise BrepTextUnsupported("a degenerate edge")
    curve = first = last = None
    # the edge's representations, each led by its kind, the list closed by a 0
    tk = _Tokens("\n".join(edge.lines[1:]))
    while (kind := tk.int()) != 0:
        if kind == 1:  # the 3D curve: index, location, range
            idx, loc = tk.int(), tk.int()
            first, last = tk.floats(2)
            c = curves[idx]
            if isinstance(c, _Unconverted):
                raise BrepTextUnsupported(f"an edge on a {c.kind}")
            curve = _move(m @ locs[loc], c)
        elif kind == 2:  # curve on surface: pcurve, surface, location, range [, uv of its ends]
            tk.floats(5 + (4 if uv_points else 0))
        elif kind == 3:  # seam: two pcurves, continuity, surface, location, range [, uv of its ends]
            tk.floats(7 + (8 if uv_points else 0))
        elif kind == 4:  # regularity: continuity, surface, location, surface, location
            tk.floats(5)
        elif kind == 5:  # 3D polygon, location
            tk.floats(2)
        elif kind == 6:  # polygon on triangulation, triangulation, location
            tk.floats(3)
        elif kind == 7:  # two polygons on triangulation, triangulation, location
            tk.floats(4)
        else:
            raise BrepTextUnsupported(f"an edge representation of BREP type {kind}")
    if curve is None:
        raise BrepTextUnsupported("an edge with no 3D curve")
    # the edge's vertices: the forward one is where the curve starts, the reversed one where it ends
    v_first = v_last = None
    for orient, v_ref, v_loc in edge.children:
        key = (v_ref, (m @ locs[v_loc]).tobytes())
        if orient == "+":
            v_first = key
        elif orient == "-":
            v_last = key
    if v_first is None or v_last is None:
        raise BrepTextUnsupported("an edge without a first and a last vertex")
    start, end = (v_first, v_last) if forward else (v_last, v_first)
    return FaceEdge(curve, first, last, forward, start, end)


def _connect(edges: list[FaceEdge]) -> list[FaceEdge]:
    """The wire's edges in the order the face runs them, from the first one stored.

    ``BRepTools_WireExplorer``'s order: each edge starts where the one before it ends. A vertex
    the wire could go on from two ways is refused rather than guessed.
    """
    left = list(edges[1:])
    out = [edges[0]]
    while left:
        nxt = [k for k, e in enumerate(left) if e.start == out[-1].end]
        if len(nxt) != 1:
            why = "stops" if not nxt else f"goes on {len(nxt)} ways"
            raise BrepTextUnsupported(f"a wire that {why} at one of its vertices")
        out.append(left.pop(nxt[0]))
    if out[-1].end != out[0].start:
        raise BrepTextUnsupported("a wire that does not close")
    return out
