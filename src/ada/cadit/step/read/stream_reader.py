"""A kernel-free, lazily-streaming STEP (ISO-10303-21 / Part-21) reader.

This is the read-side counterpart to :mod:`ada.cadit.step.write.ap242_stream`.
It parses the analytic B-rep vocabulary that the streaming emitter produces
(``PLANE`` / ``CYLINDRICAL_SURFACE`` faces bound by ``LINE`` / ``CIRCLE`` edge
loops) and yields one adapy :class:`~ada.geom.Geometry` per ``MANIFOLD_SOLID_BREP``
*as it is encountered*. The caller can feed each ``Geometry`` straight to
``active_backend().build(geom)`` for tessellation and drop it again — so peak
memory tracks a single solid rather than the whole model.

Why not OpenCascade's ``STEPControl_Reader``? It materialises every root shape
into one in-memory compound (plus the reader's transfer maps) before anything
can be tessellated — that is the source of the OOM when re-reading a large
(e.g. 700 MB+) emitted STEP file. Why not a third-party C/C++ lazy reader
(STEPcode's ``cllazyfile``)? The two hard problems it solves — a general
EXPRESS-schema-bound parser and a lazy instance offset-index — are unnecessary
here: we only need a tiny fixed entity vocabulary, and the emitter writes each
solid's entity closure contiguously and bottom-up (definitions precede
references within the solid's block). That locality lets a *single forward
pass* with a per-solid entity pool that is cleared at each solid boundary do
the job in pure Python, with no kernel and no global index.

``local_pool=True`` (the default) relies on that locality. For arbitrary STEP
where entities are shared across solids (global point tables, forward
references), pass ``local_pool=False`` to keep the full entity pool for the
duration of the read.

Coverage is intentionally scoped to the emitter's vocabulary; unsupported
surface/curve types raise so the caller can fall back to the OCC reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ada.config import logger
from ada.geom import Geometry
from ada.geom.curves import EdgeCurve, EdgeLoop, Line, Circle, OrientedEdge
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point
from ada.geom.surfaces import AdvancedFace, ClosedShell, CylindricalSurface, FaceBound, Plane

__all__ = ["stream_read_step", "StepStreamUnsupported"]


class StepStreamUnsupported(NotImplementedError):
    """Raised when the file uses an entity outside the streaming reader's scope.

    Signals the caller to fall back to the full OCC ``STEPControl_Reader``."""


# --------------------------------------------------------------------------- #
# Part-21 tokenizing
# --------------------------------------------------------------------------- #
class _Ref:
    """A reference to another instance (``#42``)."""

    __slots__ = ("id",)

    def __init__(self, i: int):
        self.id = i

    def __repr__(self):
        return f"#{self.id}"


class _Enum:
    """An enumeration / logical value (``.T.``, ``.UNSPECIFIED.``)."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f".{self.name}."


_STAR = object()  # '*' — derived/redundant value
_DOLLAR = object()  # '$' — unset optional value

_HEADER_RE = re.compile(r"^\s*#(\d+)\s*=\s*([A-Z0-9_]+)\s*\(", re.S)


def _iter_statements(fh, chunk_size: int = 1 << 20) -> Iterator[str]:
    """Yield raw Part-21 statements (text between ``;`` separators), streaming.

    ``;`` inside a quoted string does not terminate a statement. STEP strings
    are single-quoted with ``''`` as an escaped quote; the simple in/out toggle
    handles both because there is never a ``;`` between the two quote chars of
    an escaped pair.
    """
    pending = ""
    in_str = False
    while True:
        chunk = fh.read(chunk_size)
        if not chunk:
            break
        start = 0
        for j, c in enumerate(chunk):
            if c == "'":
                in_str = not in_str
            elif c == ";" and not in_str:
                yield pending + chunk[start:j]
                pending = ""
                start = j + 1
        pending += chunk[start:]
    if pending.strip():
        yield pending


def _parse_seq(s: str, i: int, end_char: str) -> tuple[list, int]:
    """Parse a comma-separated value sequence starting at ``i`` until ``end_char``."""
    vals: list = []
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        c = s[i]
        if c == end_char:
            return vals, i + 1
        if c == ",":
            i += 1
            continue
        val, i = _parse_value(s, i)
        vals.append(val)
    return vals, i


def _parse_value(s: str, i: int) -> tuple[object, int]:
    c = s[i]
    if c == "(":
        return _parse_seq(s, i + 1, ")")
    if c == "'":
        j = i + 1
        out = []
        while j < len(s):
            if s[j] == "'":
                if j + 1 < len(s) and s[j + 1] == "'":  # escaped quote
                    out.append("'")
                    j += 2
                    continue
                break
            out.append(s[j])
            j += 1
        return "".join(out), j + 1
    if c == "#":
        j = i + 1
        while j < len(s) and s[j].isdigit():
            j += 1
        return _Ref(int(s[i + 1 : j])), j
    if c == ".":
        j = i + 1
        while j < len(s) and s[j] != ".":
            j += 1
        return _Enum(s[i + 1 : j]), j + 1
    if c == "*":
        return _STAR, i + 1
    if c == "$":
        return _DOLLAR, i + 1
    # bare token: number or keyword
    j = i
    while j < len(s) and s[j] not in ",()":
        j += 1
    tok = s[i:j].strip()
    return _parse_scalar(tok), j


def _parse_scalar(tok: str):
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


# --------------------------------------------------------------------------- #
# Entity resolution: parsed tokens -> adapy geom objects
# --------------------------------------------------------------------------- #
@dataclass
class _Rec:
    type: str
    args: list


class _Resolver:
    """Resolves instance ids into adapy geom objects against an entity pool,
    memoizing within a single solid so shared points/edges are built once."""

    def __init__(self, pool: dict[int, _Rec]):
        self._pool = pool
        self._cache: dict[int, object] = {}

    def reset_cache(self):
        self._cache = {}

    def deref(self, val):
        """Resolve a value that may be a reference into a built object."""
        if isinstance(val, _Ref):
            return self.resolve(val.id)
        return val

    def resolve(self, ref_id: int):
        cached = self._cache.get(ref_id, _STAR)
        if cached is not _STAR:
            return cached
        rec = self._pool.get(ref_id)
        if rec is None:
            raise KeyError(f"unresolved reference #{ref_id}")
        obj = self._build(rec)
        self._cache[ref_id] = obj
        return obj

    def _build(self, rec: _Rec):
        builder = _BUILDERS.get(rec.type)
        if builder is None:
            raise StepStreamUnsupported(f"entity type {rec.type} not supported by the streaming reader")
        return builder(self, rec.args)


def _enum_true(v) -> bool:
    return isinstance(v, _Enum) and v.name == "T"


def _b_cartesian_point(r: _Resolver, a: list) -> Point:
    coords = a[1]  # ('', (x, y, z))
    return Point(*[float(x) for x in coords])


def _b_direction(r: _Resolver, a: list) -> Direction:
    coords = a[1]
    return Direction(*[float(x) for x in coords])


def _b_vector(r: _Resolver, a: list) -> Direction:
    # VECTOR('', #orientation, magnitude) -> the (unit) direction; magnitude is
    # irrelevant for the consumers here (Line.dir is a Direction).
    return r.deref(a[1])


def _b_vertex_point(r: _Resolver, a: list) -> Point:
    return r.deref(a[1])  # VERTEX_POINT('', #point)


def _b_axis2_placement_3d(r: _Resolver, a: list) -> Axis2Placement3D:
    # AXIS2_PLACEMENT_3D('', #location, #axis, #ref_direction)
    location = r.deref(a[1])
    kwargs = {"location": location}
    if len(a) > 2 and isinstance(a[2], _Ref):
        kwargs["axis"] = r.deref(a[2])
    if len(a) > 3 and isinstance(a[3], _Ref):
        kwargs["ref_direction"] = r.deref(a[3])
    return Axis2Placement3D(**kwargs)


def _b_line(r: _Resolver, a: list) -> Line:
    return Line(pnt=r.deref(a[1]), dir=r.deref(a[2]))


def _b_circle(r: _Resolver, a: list) -> Circle:
    return Circle(position=r.deref(a[1]), radius=float(a[2]))


def _b_edge_curve(r: _Resolver, a: list) -> EdgeCurve:
    # EDGE_CURVE('', #start_vertex, #end_vertex, #edge_geometry, same_sense)
    start = r.deref(a[1])
    end = r.deref(a[2])
    geometry = r.deref(a[3])
    return EdgeCurve(start=start, end=end, edge_geometry=geometry, same_sense=_enum_true(a[4]))


def _b_oriented_edge(r: _Resolver, a: list) -> OrientedEdge:
    # ORIENTED_EDGE('', *, *, #edge_element, orientation)
    edge_element = r.deref(a[3])
    orientation = _enum_true(a[4])
    start, end = edge_element.start, edge_element.end
    if not orientation:
        start, end = end, start
    return OrientedEdge(start=start, end=end, edge_element=edge_element, orientation=orientation)


def _b_edge_loop(r: _Resolver, a: list) -> EdgeLoop:
    return EdgeLoop(edge_list=[r.deref(x) for x in a[1]])


def _b_face_bound(r: _Resolver, a: list) -> FaceBound:
    # FACE_BOUND / FACE_OUTER_BOUND('', #bound, orientation)
    return FaceBound(bound=r.deref(a[1]), orientation=_enum_true(a[2]))


def _b_plane(r: _Resolver, a: list) -> Plane:
    return Plane(position=r.deref(a[1]))


def _b_cylindrical_surface(r: _Resolver, a: list) -> CylindricalSurface:
    return CylindricalSurface(position=r.deref(a[1]), radius=float(a[2]))


def _b_advanced_face(r: _Resolver, a: list) -> AdvancedFace:
    # ADVANCED_FACE('', (#bounds), #face_surface, same_sense)
    bounds = [r.deref(x) for x in a[1]]
    return AdvancedFace(bounds=bounds, face_surface=r.deref(a[2]), same_sense=_enum_true(a[3]))


def _b_closed_shell(r: _Resolver, a: list) -> ClosedShell:
    return ClosedShell(cfs_faces=[r.deref(x) for x in a[1]])


_BUILDERS = {
    "CARTESIAN_POINT": _b_cartesian_point,
    "DIRECTION": _b_direction,
    "VECTOR": _b_vector,
    "VERTEX_POINT": _b_vertex_point,
    "AXIS2_PLACEMENT_3D": _b_axis2_placement_3d,
    "LINE": _b_line,
    "CIRCLE": _b_circle,
    "EDGE_CURVE": _b_edge_curve,
    "ORIENTED_EDGE": _b_oriented_edge,
    "EDGE_LOOP": _b_edge_loop,
    "FACE_BOUND": _b_face_bound,
    "FACE_OUTER_BOUND": _b_face_bound,
    "PLANE": _b_plane,
    "CYLINDRICAL_SURFACE": _b_cylindrical_surface,
    "ADVANCED_FACE": _b_advanced_face,
    "CLOSED_SHELL": _b_closed_shell,
}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def stream_read_step(filepath: str | Path, *, local_pool: bool = True) -> Iterator[Geometry]:
    """Lazily stream a STEP file, yielding one :class:`Geometry` per solid.

    Each yielded ``Geometry`` wraps a :class:`~ada.geom.surfaces.ClosedShell`
    built from the solid's ``MANIFOLD_SOLID_BREP``, ready to hand to
    ``active_backend().build(geom)`` for tessellation.

    Parameters
    ----------
    filepath:
        Path to the ``.step`` / ``.stp`` file.
    local_pool:
        When ``True`` (default) the entity pool is cleared at every solid
        boundary — constant memory, valid for files whose solids are written as
        self-contained contiguous blocks (the adapy streaming emitter). Set
        ``False`` for arbitrary STEP that shares entities across solids.
    """
    filepath = Path(filepath)
    pool: dict[int, _Rec] = {}
    resolver = _Resolver(pool)
    n_solids = 0

    with filepath.open("r", encoding="utf-8", errors="replace") as fh:
        for stmt in _iter_statements(fh):
            m = _HEADER_RE.match(stmt)
            if m is None:
                # header keywords (ISO-10303-21, HEADER, DATA, ENDSEC, ...) and
                # complex/instance records starting with '(' — not geometry roots.
                continue
            inst_id = int(m.group(1))
            etype = m.group(2)
            body_open = m.end() - 1  # position of '('
            args, _ = _parse_seq(stmt, body_open + 1, ")")

            if etype == "MANIFOLD_SOLID_BREP":
                # MANIFOLD_SOLID_BREP('name', #shell)
                name = args[0] if isinstance(args[0], str) and args[0] else f"solid_{n_solids + 1}"
                try:
                    shell = resolver.deref(args[1])
                except StepStreamUnsupported:
                    raise
                except Exception as ex:  # noqa: BLE001 - report and skip a bad solid
                    logger.warning(f"stream_read_step: skipping solid {name!r}: {ex}")
                else:
                    n_solids += 1
                    yield Geometry(id=name, geometry=shell)
                if local_pool:
                    pool.clear()
                    resolver.reset_cache()
                continue

            pool[inst_id] = _Rec(etype, args)
