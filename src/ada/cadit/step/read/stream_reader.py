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
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ada.config import logger
from ada.geom import Geometry
from ada.geom.curves import (
    BSplineCurveFormEnum,
    BSplineCurveWithKnots,
    Circle,
    EdgeCurve,
    EdgeLoop,
    Ellipse,
    KnotType,
    Line,
    OrientedEdge,
    RationalBSplineCurveWithKnots,
)
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point
from ada.geom.surfaces import (
    AdvancedFace,
    BSplineSurfaceForm,
    BSplineSurfaceWithKnots,
    ClosedShell,
    ConicalSurface,
    CylindricalSurface,
    FaceBound,
    OpenShell,
    Plane,
    ShellBasedSurfaceModel,
    ToroidalSurface,
)

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
_COMPLEX_RE = re.compile(r"^\s*#(\d+)\s*=\s*\(", re.S)  # #id=(NAME(..)NAME(..)..) complex record
_COMPLEX = "__COMPLEX__"


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
        if rec.type == _COMPLEX:
            return _build_complex(self, rec.args)  # rec.args is a {SUBTYPE: subargs} dict
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


def _b_ellipse(r: _Resolver, a: list) -> Ellipse:
    # ELLIPSE('', #position, semi_axis_1, semi_axis_2)
    return Ellipse(position=r.deref(a[1]), semi_axis1=float(a[2]), semi_axis2=float(a[3]))


def _b_surface_curve(r: _Resolver, a: list):
    # SURFACE_CURVE / SEAM_CURVE('', #curve_3d, (#associated_geometry...), master)
    # The first arg is the 3D curve (LINE/CIRCLE/ELLIPSE/B-spline) — the edge geometry
    # we need; the associated p-curves only trim it on the face and aren't required to
    # build/tessellate via the backend. Unwrapping keeps OCCT-written STEP (any flavor)
    # readable without falling back to the kernel reader.
    return r.deref(a[1])


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


class _DegenerateLoop:
    """A VERTEX_LOOP — a single-vertex 'loop' at a pole/apex of a closed surface
    (sphere/cone). Not a real boundary wire; dropped from a face's bounds."""

    __slots__ = ()


_DEGENERATE_LOOP = _DegenerateLoop()


def _b_vertex_loop(r: _Resolver, a: list) -> _DegenerateLoop:
    return _DEGENERATE_LOOP


def _b_face_bound(r: _Resolver, a: list) -> FaceBound:
    # FACE_BOUND / FACE_OUTER_BOUND('', #bound, orientation)
    return FaceBound(bound=r.deref(a[1]), orientation=_enum_true(a[2]))


def _b_plane(r: _Resolver, a: list) -> Plane:
    return Plane(position=r.deref(a[1]))


def _b_cylindrical_surface(r: _Resolver, a: list) -> CylindricalSurface:
    return CylindricalSurface(position=r.deref(a[1]), radius=float(a[2]))


def _b_conical_surface(r: _Resolver, a: list) -> ConicalSurface:
    # CONICAL_SURFACE('', #position, radius, semi_angle)
    return ConicalSurface(position=r.deref(a[1]), radius=float(a[2]), semi_angle=float(a[3]))


def _b_spherical_surface(r: _Resolver, a: list):
    # A sphere face is closed in BOTH u and v; the kernel-free seam reconstruction
    # yields a degenerate face that aborts OCC's mesher (uncatchable). Until periodic
    # double-seam handling lands, signal unsupported so reader="auto" falls back to
    # the OCC reader for sphere-containing files rather than crashing downstream.
    raise StepStreamUnsupported("SPHERICAL_SURFACE not yet supported by the streaming reader (closed u+v)")


def _b_toroidal_surface(r: _Resolver, a: list) -> ToroidalSurface:
    # TOROIDAL_SURFACE('', #position, major_radius, minor_radius)
    return ToroidalSurface(position=r.deref(a[1]), major_radius=float(a[2]), minor_radius=float(a[3]))


def _b_advanced_face(r: _Resolver, a: list) -> AdvancedFace:
    # ADVANCED_FACE('', (#bounds), #face_surface, same_sense)
    # Drop degenerate vertex-loop bounds (pole/apex of a closed surface) — they
    # carry no boundary wire; the surface trims to its real edge-loop bounds.
    bounds = [fb for fb in (r.deref(x) for x in a[1]) if not isinstance(fb.bound, _DegenerateLoop)]
    return AdvancedFace(bounds=bounds, face_surface=r.deref(a[2]), same_sense=_enum_true(a[3]))


def _b_closed_shell(r: _Resolver, a: list) -> ClosedShell:
    return ClosedShell(cfs_faces=[r.deref(x) for x in a[1]])


def _b_open_shell(r: _Resolver, a: list) -> OpenShell:
    # OPEN_SHELL('', (#faces)) — a pure (thickness-less) surface shell.
    return OpenShell(cfs_faces=[r.deref(x) for x in a[1]])


def _b_shell_based_surface_model(r: _Resolver, a: list) -> ShellBasedSurfaceModel:
    # SHELL_BASED_SURFACE_MODEL('', (#shells)) — how a surface (no-thickness) shape
    # is wrapped, e.g. a curved B-spline plate exported as an open shell.
    return ShellBasedSurfaceModel(sbsm_boundary=[r.deref(x) for x in a[1]])


# -- B-splines -------------------------------------------------------------- #
def _enum_name(v) -> str:
    return v.name if isinstance(v, _Enum) else str(v)


def _make_bspline_curve(r, degree, cp_refs, curve_form, closed, si, mults, knots, knot_spec, weights=None):
    cps = [r.deref(ref) for ref in cp_refs]
    common = dict(
        degree=int(degree),
        control_points_list=cps,
        curve_form=BSplineCurveFormEnum(_enum_name(curve_form)),
        closed_curve=_enum_true(closed),
        self_intersect=_enum_true(si),
        knot_multiplicities=[int(x) for x in mults],
        knots=[float(x) for x in knots],
        knot_spec=KnotType.from_str(_enum_name(knot_spec)),
    )
    if weights is not None:
        return RationalBSplineCurveWithKnots(**common, weights_data=[float(w) for w in weights])
    return BSplineCurveWithKnots(**common)


def _make_bspline_surface(
    r, u_deg, v_deg, cp_grid, surf_form, u_closed, v_closed, si, u_mults, v_mults, u_knots, v_knots, knot_spec, weights=None
):
    if weights is not None:
        # A rational B-spline face's boundary edges only trim correctly when their
        # 2D p-curves are supplied; kernel-free 3D->UV reprojection collapses the
        # face to zero area (empty mesh). Signal unsupported so reader="auto" falls
        # back to OCC (renders correctly). Non-rational surfaces reproject fine.
        # Follow-up: parse SURFACE_CURVE/PCURVE p-curves and attach them per edge.
        raise StepStreamUnsupported("rational B-spline surface needs p-curve trimming (not yet); OCC reader handles it")
    cps = [[r.deref(ref) for ref in row] for row in cp_grid]
    common = dict(
        u_degree=int(u_deg),
        v_degree=int(v_deg),
        control_points_list=cps,
        surface_form=BSplineSurfaceForm.from_str(_enum_name(surf_form)),
        u_closed=_enum_true(u_closed),
        v_closed=_enum_true(v_closed),
        self_intersect=_enum_true(si),
        u_multiplicities=[int(x) for x in u_mults],
        v_multiplicities=[int(x) for x in v_mults],
        u_knots=[float(x) for x in u_knots],
        v_knots=[float(x) for x in v_knots],
        knot_spec=KnotType.from_str(_enum_name(knot_spec)),
    )
    return BSplineSurfaceWithKnots(**common)


def _b_bspline_curve_with_knots(r: _Resolver, a: list):
    # B_SPLINE_CURVE_WITH_KNOTS('', degree, (cps), form, .closed., .si., (mults), (knots), spec)
    return _make_bspline_curve(r, a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8])


def _b_bspline_surface_with_knots(r: _Resolver, a: list):
    # B_SPLINE_SURFACE_WITH_KNOTS('', u_deg, v_deg, (cp grid), form, .uc., .vc., .si.,
    #                             (u_mults), (v_mults), (u_knots), (v_knots), spec)
    return _make_bspline_surface(r, a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12])


def _build_complex(r: _Resolver, subs: dict):
    """Build a rational/non-rational B-spline from a complex record's sub-entities.
    Note: complex sub-entity args have NO leading '' name, so they are 0-indexed."""
    if "B_SPLINE_SURFACE" in subs and "B_SPLINE_SURFACE_WITH_KNOTS" in subs:
        s = subs["B_SPLINE_SURFACE"]  # [u_deg, v_deg, cp_grid, form, u_closed, v_closed, si]
        k = subs["B_SPLINE_SURFACE_WITH_KNOTS"]  # [u_mults, v_mults, u_knots, v_knots, spec]
        rat = subs.get("RATIONAL_B_SPLINE_SURFACE")  # [weight_grid] or None
        weights = rat[0] if rat else None
        return _make_bspline_surface(
            r, s[0], s[1], s[2], s[3], s[4], s[5], s[6], k[0], k[1], k[2], k[3], k[4], weights
        )
    if "B_SPLINE_CURVE" in subs and "B_SPLINE_CURVE_WITH_KNOTS" in subs:
        c = subs["B_SPLINE_CURVE"]  # [degree, cps, form, closed, si]
        k = subs["B_SPLINE_CURVE_WITH_KNOTS"]  # [mults, knots, spec]
        rat = subs.get("RATIONAL_B_SPLINE_CURVE")  # [weights] or None
        weights = rat[0] if rat else None
        return _make_bspline_curve(r, c[0], c[1], c[2], c[3], c[4], k[0], k[1], k[2], weights)
    raise StepStreamUnsupported(f"complex entity {sorted(subs)} not supported by the streaming reader")


_BUILDERS = {
    "CARTESIAN_POINT": _b_cartesian_point,
    "DIRECTION": _b_direction,
    "VECTOR": _b_vector,
    "VERTEX_POINT": _b_vertex_point,
    "AXIS2_PLACEMENT_3D": _b_axis2_placement_3d,
    "LINE": _b_line,
    "CIRCLE": _b_circle,
    "ELLIPSE": _b_ellipse,
    "B_SPLINE_CURVE_WITH_KNOTS": _b_bspline_curve_with_knots,
    "B_SPLINE_SURFACE_WITH_KNOTS": _b_bspline_surface_with_knots,
    "SURFACE_CURVE": _b_surface_curve,
    "SEAM_CURVE": _b_surface_curve,
    "EDGE_CURVE": _b_edge_curve,
    "ORIENTED_EDGE": _b_oriented_edge,
    "EDGE_LOOP": _b_edge_loop,
    "VERTEX_LOOP": _b_vertex_loop,
    "FACE_BOUND": _b_face_bound,
    "FACE_OUTER_BOUND": _b_face_bound,
    "PLANE": _b_plane,
    "CYLINDRICAL_SURFACE": _b_cylindrical_surface,
    "CONICAL_SURFACE": _b_conical_surface,
    "SPHERICAL_SURFACE": _b_spherical_surface,
    "TOROIDAL_SURFACE": _b_toroidal_surface,
    "ADVANCED_FACE": _b_advanced_face,
    "CLOSED_SHELL": _b_closed_shell,
    "OPEN_SHELL": _b_open_shell,
    "SHELL_BASED_SURFACE_MODEL": _b_shell_based_surface_model,
}


# Top-level renderable geometry roots — one yielded Geometry per record. A solid
# (MANIFOLD_SOLID_BREP -> its ClosedShell) and a pure surface shell
# (SHELL_BASED_SURFACE_MODEL -> ShellBasedSurfaceModel). Shells nested inside
# these are reached by reference, never yielded on their own, so no double-count.
_ROOT_BUILDERS = {
    "MANIFOLD_SOLID_BREP": lambda r, a: r.deref(a[1]),
    "SHELL_BASED_SURFACE_MODEL": _b_shell_based_surface_model,
}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def stream_read_step(filepath: str | Path, *, local_pool: bool = True, tolerant: bool = False) -> Iterator[Geometry]:
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
        boundary — constant memory, valid only for files whose solids are
        written as self-contained, bottom-up contiguous blocks (definitions
        precede references), which is what the adapy streaming emitter produces.

        When ``False`` the reader does a two-pass deferred resolution (load the
        whole entity table, then resolve each solid). This holds the full pool
        but correctly handles **forward references** — a ``MANIFOLD_SOLID_BREP``
        written before its shell/faces/points, which is how OpenCASCADE and most
        other writers emit STEP. Use ``False`` for arbitrary STEP.
    tolerant:
        When ``True`` a solid using an unsupported surface/curve (e.g. a spherical
        or rational-B-spline face) is *skipped* and the reader keeps going, instead
        of raising ``StepStreamUnsupported``. Lets a large mixed CAD file read its
        supported solids kernel-free rather than dropping the whole file to OCC (and
        OOM-ing); a one-line summary of what was skipped is logged at the end.
    """
    filepath = Path(filepath)
    skipped: Counter = Counter()

    if not local_pool:
        yield from _read_two_pass(filepath, tolerant=tolerant, skipped=skipped)
        _log_skips(filepath, skipped)
        return

    pool: dict[int, _Rec] = {}
    resolver = _Resolver(pool)
    n_solids = 0

    with filepath.open("r", encoding="utf-8", errors="replace") as fh:
        for stmt in _iter_statements(fh):
            parsed = _parse_statement(stmt)
            if parsed is None:
                # header keywords (ISO-10303-21, HEADER, DATA, ENDSEC, ...) and
                # complex/instance records starting with '(' — not geometry roots.
                continue
            inst_id, etype, args = parsed

            root = _ROOT_BUILDERS.get(etype)
            if root is not None:
                name = _solid_name(args, n_solids)
                geom = _try_resolve_root(resolver, name, root, args, tolerant=tolerant, skipped=skipped)
                if geom is not None:
                    n_solids += 1
                    yield Geometry(id=name, geometry=geom)
                pool.clear()  # per-root clear: constant memory, bottom-up only
                resolver.reset_cache()
                continue

            pool[inst_id] = _Rec(etype, args)
    _log_skips(filepath, skipped)


def _parse_statement(stmt: str):
    """Parse one Part-21 statement into (instance_id, type, args), or None for
    header keywords. A *complex* record ``#id=(NAME(..)NAME(..)..)`` (how STEP
    encodes rational B-splines) returns type ``_COMPLEX`` with args a dict
    ``{NAME: subargs}``."""
    m = _HEADER_RE.match(stmt)
    if m is not None:
        args, _ = _parse_seq(stmt, m.end(), ")")  # m.end() is just past the '('
        return int(m.group(1)), m.group(2), args
    cm = _COMPLEX_RE.match(stmt)
    if cm is None:
        return None
    return int(cm.group(1)), _COMPLEX, _parse_complex(stmt, cm.end())  # cm.end() just past the outer '('


def _parse_complex(s: str, i: int) -> dict:
    """Parse a complex record body ``NAME(args)NAME(args)...)`` into {NAME: args}."""
    subs: dict[str, list] = {}
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n or s[i] == ")":
            break
        j = i
        while j < n and (s[j].isalnum() or s[j] == "_"):
            j += 1
        name = s[i:j]
        args, i = _parse_seq(s, j + 1, ")")  # s[j] == '('
        subs[name] = args
    return subs


def _solid_name(args: list, n_solids: int) -> str:
    return args[0] if args and isinstance(args[0], str) and args[0] else f"solid_{n_solids + 1}"


def _short_reason(ex: Exception) -> str:
    """A compact, groupable label for a skipped-solid summary."""
    s = str(ex)
    m = re.match(r"complex entity (\[[^\]]*\])", s)
    if m:
        return f"complex {m.group(1)}"
    # leading ALL-CAPS entity token, e.g. "SPHERICAL_SURFACE not yet ..." or
    # "entity type B_SPLINE_SURFACE ..."
    m = re.match(r"(?:entity type )?([A-Z][A-Z_0-9]{2,})", s)
    if m:
        return m.group(1)
    return s.split(" (")[0].split(";")[0][:40]


def _try_resolve_root(resolver: "_Resolver", name: str, root_builder, args: list, *, tolerant, skipped):
    """Build one root geometry (solid shell / surface model); return None on a bad
    root. A StepStreamUnsupported root re-raises (so the caller can fall back to OCC)
    unless ``tolerant`` — then it is tallied in ``skipped`` and dropped so the rest of
    the file still reads kernel-free."""
    try:
        return root_builder(resolver, args)
    except StepStreamUnsupported as ex:
        if not tolerant:
            raise
        skipped[_short_reason(ex)] += 1
        return None
    except Exception as ex:  # noqa: BLE001 - report and skip a bad root
        logger.warning(f"stream_read_step: skipping {name!r}: {ex}")
        skipped["error"] += 1
        return None


def _log_skips(filepath: Path, skipped) -> None:
    if skipped:
        total = sum(skipped.values())
        logger.info(
            "stream_read_step: %s: skipped %d unsupported solid(s) — %s", filepath.name, total, dict(skipped)
        )


# Files above this size resolve against an mmap + offset-index pool (parse each
# entity on demand) instead of materialising every entity as a parsed _Rec — the
# dict pool is ~7x the file size in Python objects (5+ GB on a 750 MB CAD assembly),
# which OOMs a worker pod. Small files keep the simpler/faster dict pool.
_LAZY_POOL_THRESHOLD = 64 * 1024 * 1024
_WS = frozenset(b" \t\r\n")


def _read_two_pass(filepath: Path, *, tolerant: bool = False, skipped=None, low_memory: bool | None = None):
    """General STEP (forward references): resolve each root against the full entity
    table. Large files use a constant-memory mmap + offset-index pool so a worker pod
    stays within budget; small files use a plain parsed-entity dict."""
    if skipped is None:
        skipped = Counter()
    if low_memory is None:
        try:
            low_memory = filepath.stat().st_size > _LAZY_POOL_THRESHOLD
        except OSError:
            low_memory = False
    gen = _read_two_pass_lazy if low_memory else _read_two_pass_dict
    yield from gen(filepath, tolerant=tolerant, skipped=skipped)


def _read_two_pass_dict(filepath: Path, *, tolerant: bool, skipped):
    pool: dict[int, _Rec] = {}
    root_ids: list[int] = []
    with filepath.open("r", encoding="utf-8", errors="replace") as fh:
        for stmt in _iter_statements(fh):
            parsed = _parse_statement(stmt)
            if parsed is None:
                continue
            inst_id, etype, args = parsed
            pool[inst_id] = _Rec(etype, args)
            if etype in _ROOT_BUILDERS:
                root_ids.append(inst_id)

    resolver = _Resolver(pool)
    n_solids = 0
    for rid in root_ids:
        rec = pool[rid]
        name = _solid_name(rec.args, n_solids)
        resolver.reset_cache()
        geom = _try_resolve_root(resolver, name, _ROOT_BUILDERS[rec.type], rec.args, tolerant=tolerant, skipped=skipped)
        if geom is not None:
            n_solids += 1
            yield Geometry(id=name, geometry=geom)


def _stmt_end(mm, start: int, n: int) -> int:
    """Byte index of the statement-terminating ';' at/after ``start`` (a ';' inside a
    single-quoted string doesn't terminate). Returns ``n`` if there is none."""
    end = mm.find(b";", start)
    if end < 0:
        return n
    while mm[start:end].count(b"'") & 1:  # the ';' fell inside an open string
        nxt = mm.find(b";", end + 1)
        if nxt < 0:
            return n
        end = nxt
    return end


def _read_statement_at(mm, start: int, n: int) -> str:
    return mm[start:_stmt_end(mm, start, n)].decode("utf-8", "replace")


def _is_kw_byte(b: int) -> bool:
    return (0x41 <= b <= 0x5A) or (0x61 <= b <= 0x7A) or (0x30 <= b <= 0x39) or b == 0x5F


def _scan_offset_index(mm):
    """One linear pass: record (id -> byte offset) for every ``#id=…`` entity plus the
    ids of the geometry roots. Uses array.array (raw int64 — no per-int Python object
    blow-up), so the index of an 11 M-entity file is ~170 MB, not gigabytes."""
    import array

    ids = array.array("q")
    offs = array.array("q")
    roots: list[int] = []
    n = len(mm)
    pos = 0
    while pos < n:
        end = _stmt_end(mm, pos, n)
        if end >= n:
            break
        s = pos
        while s < end and mm[s] in _WS:
            s += 1
        if s < end and mm[s] == 0x23:  # '#'
            k = s + 1
            while k < end and 0x30 <= mm[k] <= 0x39:
                k += 1
            if k > s + 1:
                rid = int(mm[s + 1 : k])
                ids.append(rid)
                offs.append(s)
                # locate the type keyword for root detection: skip ws, '=', ws (OCC
                # writes "#33 = MANIFOLD_SOLID_BREP(...)" with spaces around '=').
                eq = k
                while eq < end and mm[eq] in _WS:
                    eq += 1
                if eq < end and mm[eq] == 0x3D:  # '='
                    m = eq + 1
                    while m < end and mm[m] in _WS:
                        m += 1
                    p = m
                    while p < end and _is_kw_byte(mm[p]):
                        p += 1
                    if p > m and mm[m:p].decode("ascii", "replace") in _ROOT_BUILDERS:
                        roots.append(rid)
        pos = end + 1
    return ids, offs, roots


class _OffsetPool:
    """Drop-in for the entity dict: ``get(id)`` seeks to the entity's offset in the
    mmap and parses it on demand. The _Resolver caches the BUILT object per solid, so
    each entity is parsed about once; the file pages stay mmap-resident (reclaimable
    by the OS), never copied onto the Python heap."""

    def __init__(self, mm, ids_sorted, offs_sorted):
        self._mm = mm
        self._ids = ids_sorted
        self._offs = offs_sorted
        self._n = len(mm)

    def get(self, rid):
        import numpy as np

        i = int(np.searchsorted(self._ids, rid))
        if i >= self._ids.shape[0] or self._ids[i] != rid:
            return None
        stmt = _read_statement_at(self._mm, int(self._offs[i]), self._n)
        parsed = _parse_statement(stmt)
        if parsed is None:
            return None
        return _Rec(parsed[1], parsed[2])


def _read_two_pass_lazy(filepath: Path, *, tolerant: bool, skipped):
    import mmap

    import numpy as np

    fh = open(filepath, "rb")  # noqa: SIM115 - kept open for the generator's lifetime
    mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        ids_arr, offs_arr, roots = _scan_offset_index(mm)
        ids_np = np.frombuffer(ids_arr, dtype=np.int64)
        offs_np = np.frombuffer(offs_arr, dtype=np.int64)
        order = np.argsort(ids_np, kind="stable")
        ids_sorted = np.ascontiguousarray(ids_np[order])
        offs_sorted = np.ascontiguousarray(offs_np[order])
        del ids_np, offs_np, order, ids_arr, offs_arr
        pool = _OffsetPool(mm, ids_sorted, offs_sorted)
        resolver = _Resolver(pool)
        n_solids = 0
        for rid in roots:
            rec = pool.get(rid)
            if rec is None:
                continue
            name = _solid_name(rec.args, n_solids)
            resolver.reset_cache()
            geom = _try_resolve_root(
                resolver, name, _ROOT_BUILDERS[rec.type], rec.args, tolerant=tolerant, skipped=skipped
            )
            if geom is not None:
                n_solids += 1
                yield Geometry(id=name, geometry=geom)
    finally:
        mm.close()
        fh.close()
