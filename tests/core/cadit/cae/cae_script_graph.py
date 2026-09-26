"""Read an emitted Abaqus/CAE script as a graph and say what is wrong with it.

This is the licence-free half of the verification. It does **not** fake the CAE kernel: nothing here
pretends to know what ``WirePolyLine`` *does*. It only reads the script the writer produced, resolves
the references between the calls in it, and asserts the things that must hold for the script to
describe a complete model -- every section assigned, every profile defined before it is used, every
part instanced once, every name unique, every ``n1`` unit and perpendicular to the member it orients.

Those are the same assertions a recording stub would have made, minus the fake API that a stub has to
keep in step with the real one, and minus the illusion that "it ran". Whether the calls themselves
are *right* is settled by running the script -- see ``abaqus_runner`` and the licensed tests.

Everything is keyed on the **method name** (``.SectionAssignment``, ``.IProfile``, ...), never on the
receiver variable, so the writer is free to name its locals whatever it likes.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field

# The CAE profile constructors, from `dir(model)` on a real Abaqus 2025 kernel.
PROFILE_METHODS = frozenset(
    {
        "ArbitraryProfile",
        "BoxProfile",
        "ChannelProfile",
        "CircularProfile",
        "GeneralizedProfile",
        "HatProfile",
        "HexagonalProfile",
        "IProfile",
        "LProfile",
        "PipeProfile",
        "RectangularProfile",
        "TProfile",
        "TrapezoidalProfile",
    }
)

REQUIRED_IMPORTS = ("abaqus", "abaqusConstants", "caeModules")

# `n1` is `beam.yvec`, which adapy already normalises, so the only slack these need to absorb is the
# rounding in the emitted literal. A real misorientation is O(0.1) -- nine orders away.
UNIT_TOL = 1e-9
PERP_TOL = 1e-9
#: How far off a member's axis a located cylinder's own axis may lie, relative to the member length.
COLLINEAR_REL_TOL = 1e-9

# Syntax the emitted script must stay clear of to remain readable by the py2.7 kernel this repo still
# ships an in-Abaqus script for (`ada/fem/formats/abaqus/results/aba_io_py27.py`, `import cPickle`).
_PY27_FORBIDDEN = {
    "JoinedStr": "f-string",
    "FormattedValue": "f-string",
    "AnnAssign": "variable annotation",
    "NamedExpr": "walrus operator",
    "AsyncFunctionDef": "async def",
    "Await": "await",
    "MatchValue": "match statement",
    "TryStar": "except*",
}


class CaeGraphError(AssertionError):
    """The emitted script does not describe a complete, consistent model."""


@dataclass(frozen=True)
class Call:
    """One call in the emitted script, with its literal arguments already evaluated."""

    method: str
    receiver: str
    lineno: int
    args: tuple
    kwargs: dict
    raw_kwargs: dict = field(compare=False, default_factory=dict)

    def kw(self, name, default=None):
        return self.kwargs.get(name, default)


class _Unresolved:
    """A value the reader could not evaluate to a literal (a name, an expression, a call)."""

    __slots__ = ("src", "call")

    def __init__(self, src: str, call: Call | None = None):
        self.src = src
        self.call = call

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unresolved {}>".format(self.src)

    def __eq__(self, other):
        return isinstance(other, _Unresolved) and other.src == self.src

    def __hash__(self):
        return hash(("unresolved", self.src))


class ScriptGraph:
    """Every call in an emitted script, plus the variable -> producing-call map that links them."""

    def __init__(self, source: str, name: str = "<emitted>"):
        self.source = source
        self.name = name
        self.tree = ast.parse(source, filename=name)
        self.calls: list[Call] = []
        self._var_calls: dict[str, Call] = {}
        self._var_values: dict[str, object] = {}
        self._read()

    # ------------------------------------------------------------------ reading

    def _read(self) -> None:
        # Assignments first, in source order, so a reference can be resolved to its producer.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target = node.targets[0].id
                value = node.value
                if isinstance(value, ast.Call):
                    self._var_calls[target] = self._as_call(value)
                else:
                    literal = self._literal(value)
                    if not isinstance(literal, _Unresolved):
                        self._var_values[target] = literal
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                self.calls.append(self._as_call(node))
        self.calls.sort(key=lambda c: (c.lineno, c.method))

    def _as_call(self, node: ast.Call) -> Call:
        func = node.func
        if isinstance(func, ast.Attribute):
            method = func.attr
            receiver = _src(func.value)
        elif isinstance(func, ast.Name):
            method, receiver = func.id, ""
        else:  # pragma: no cover - defensive
            method, receiver = _src(func), ""
        kwargs, raw = {}, {}
        for kw in node.keywords:
            if kw.arg is None:
                continue
            kwargs[kw.arg] = self._literal(kw.value)
            raw[kw.arg] = _src(kw.value)
        return Call(
            method=method,
            receiver=receiver,
            lineno=node.lineno,
            args=tuple(self._literal(a) for a in node.args),
            kwargs=kwargs,
            raw_kwargs=raw,
        )

    def _literal(self, node: ast.AST) -> object:
        try:
            return ast.literal_eval(node)
        except (ValueError, SyntaxError, TypeError):
            pass
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = self._literal(node.operand)
            if isinstance(inner, (int, float)):
                return -inner
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(self._literal(e) for e in node.elts)
        if isinstance(node, ast.Call):
            return _Unresolved(_src(node), self._as_call(node))
        return _Unresolved(_src(node))

    # ----------------------------------------------------------------- querying

    def by_method(self, *methods: str) -> list[Call]:
        wanted = set(methods)
        return [c for c in self.calls if c.method in wanted]

    def profile_calls(self) -> list[Call]:
        return [c for c in self.calls if c.method in PROFILE_METHODS]

    def created_names(self, *methods: str) -> dict[str, Call]:
        out: dict[str, Call] = {}
        for call in self.by_method(*methods):
            name = call.kw("name")
            if isinstance(name, str):
                out[name] = call
        return out

    def resolve_call(self, value) -> Call | None:
        """The call that produced ``value``, whether it was inlined or went through a variable."""
        if isinstance(value, _Unresolved):
            if value.call is not None:
                return value.call
            return self._var_calls.get(value.src)
        return None

    def resolve_value(self, value):
        """A literal for ``value``, following one hop through a variable if needed."""
        if isinstance(value, _Unresolved) and value.src in self._var_values:
            return self._var_values[value.src]
        return value


# --------------------------------------------------------------------- helpers


def _src(node: ast.AST) -> str:
    return ast.unparse(node)


def _fail(message: str) -> None:
    raise CaeGraphError(message)


def _as_vec3(value) -> tuple[float, float, float] | None:
    if isinstance(value, (tuple, list)) and len(value) == 3:
        if all(isinstance(v, (int, float)) for v in value):
            return (float(value[0]), float(value[1]), float(value[2]))
    return None


def _length(v) -> float:
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    ln = _length(v)
    return (v[0] / ln, v[1] / ln, v[2] / ln)


def _wire_segments(graph: ScriptGraph) -> list[tuple[tuple, tuple, int]]:
    """Every (start, end, lineno) drawn by a ``WirePolyLine``."""
    segments = []
    for call in graph.by_method("WirePolyLine"):
        points = graph.resolve_value(call.kw("points"))
        if points is None and call.args:
            points = graph.resolve_value(call.args[0])
        if not isinstance(points, (tuple, list)):
            _fail("WirePolyLine on line {} has no readable points= argument".format(call.lineno))
        for polyline in points:
            if not isinstance(polyline, (tuple, list)) or len(polyline) < 2:
                _fail("WirePolyLine on line {} has a polyline that is not a pair of points".format(call.lineno))
            for start, end in zip(polyline[:-1], polyline[1:]):
                p1, p2 = _as_vec3(start), _as_vec3(end)
                if p1 is None or p2 is None:
                    _fail("WirePolyLine on line {} has a point that is not three numbers".format(call.lineno))
                if _length((p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2])) == 0.0:
                    _fail("WirePolyLine on line {} draws a zero-length member".format(call.lineno))
                segments.append((p1, p2, call.lineno))
    return segments


def _region_lookup(graph: ScriptGraph, call: Call, what: str) -> Call:
    """The edge-lookup call behind the ``region=`` of a section assignment or an orientation."""
    region = call.kw("region")
    if region is None and call.args:
        region = call.args[0]
    if region is None:
        _fail("{} on line {} has no region= argument".format(what, call.lineno))
    if isinstance(region, (tuple, list)) and len(region) == 0:
        _fail("{} on line {} is given an empty region".format(what, call.lineno))
    set_call = graph.resolve_call(region)
    if set_call is None or set_call.method not in ("Set", "Surface"):
        _fail(
            "{} on line {} does not take its region from a Set(...) -- got {!r}".format(
                what, call.lineno, call.raw_kwargs.get("region", "?")
            )
        )
    if set_call.lineno > call.lineno:
        _fail("{} on line {} uses a region defined later, on line {}".format(what, call.lineno, set_call.lineno))
    edges = set_call.kw("edges")
    if edges is None:
        _fail("the Set on line {} used by {} has no edges= argument".format(set_call.lineno, what))
    if isinstance(edges, (tuple, list)) and len(edges) == 0:
        _fail("the Set on line {} used by {} is built from an empty edge sequence".format(set_call.lineno, what))
    lookup = graph.resolve_call(edges)
    if lookup is None:
        _fail(
            "the Set on line {} takes edges from {!r}, which is not a call this reader can follow".format(
                set_call.lineno, set_call.raw_kwargs.get("edges", "?")
            )
        )
    if lookup.method == "findAt":
        _fail(
            "the Set on line {} locates its member with findAt; a member split by a landing brace "
            "returns only the sub-edge containing the point, so the rest of it would go "
            "unsectioned".format(set_call.lineno)
        )
    if lookup.method != "getByBoundingCylinder":
        _fail(
            "the Set on line {} locates its member with {}, not getByBoundingCylinder".format(
                set_call.lineno, lookup.method
            )
        )
    return lookup


def _cylinder_axis(lookup: Call, graph: ScriptGraph) -> tuple[tuple, tuple]:
    c1 = _as_vec3(graph.resolve_value(lookup.kw("center1")))
    c2 = _as_vec3(graph.resolve_value(lookup.kw("center2")))
    if c1 is None or c2 is None:
        _fail("getByBoundingCylinder on line {} has centres this reader cannot read".format(lookup.lineno))
    if _length((c2[0] - c1[0], c2[1] - c1[1], c2[2] - c1[2])) == 0.0:
        _fail("getByBoundingCylinder on line {} has a zero-length cylinder".format(lookup.lineno))
    return c1, c2


def _region_key(graph: ScriptGraph, call: Call) -> str:
    """A set is named inside its part, so the key that identifies a member is part + set name."""
    set_call = graph.resolve_call(call.kw("region"))
    name = set_call.kw("name") if set_call is not None else None
    if isinstance(name, str):
        return "{}.{}".format(set_call.receiver, name)
    return call.raw_kwargs.get("region", "?")


# ---------------------------------------------------------------------- checks


def check_preamble(graph: ScriptGraph) -> None:
    """``from caeModules import *`` is not optional: without it ``mdb`` has no geometry importers."""
    star_imports = {
        node.module
        for node in ast.walk(graph.tree)
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
    }
    explicit = {node.module for node in ast.walk(graph.tree) if isinstance(node, ast.ImportFrom)}
    for module in REQUIRED_IMPORTS:
        if module not in star_imports and module not in explicit:
            _fail("the emitted script never imports {}".format(module))


def check_names_unique(graph: ScriptGraph) -> None:
    """A collision must fail, not silently overwrite -- and a dot is rejected by CAE outright."""
    namespaces = {
        # model- and mdb-level names: one namespace each
        "part": (("Part",), False),
        "material": (("Material",), False),
        "profile": (tuple(sorted(PROFILE_METHODS)), False),
        "section": (("BeamSection",), False),
        "instance": (("Instance",), False),
        # a set belongs to its part, so uniqueness is per receiver
        "set": (("Set",), True),
    }
    for namespace, (methods, per_receiver) in namespaces.items():
        seen: dict[tuple, int] = {}
        for call in graph.by_method(*methods):
            name = call.kw("name")
            if not isinstance(name, str):
                continue
            if "." in name:
                _fail(
                    "the {} name {!r} on line {} contains a dot, which CAE rejects".format(namespace, name, call.lineno)
                )
            key = (call.receiver, name) if per_receiver else (name,)
            if key in seen:
                _fail(
                    "the {} name {!r} is emitted twice, on lines {} and {}".format(
                        namespace, name, seen[key], call.lineno
                    )
                )
            seen[key] = call.lineno


def check_profiles_and_materials_defined_before_use(graph: ScriptGraph) -> None:
    profiles = graph.created_names(*sorted(PROFILE_METHODS))
    materials = graph.created_names("Material")
    for call in graph.by_method("BeamSection"):
        for kind, table in (("profile", profiles), ("material", materials)):
            ref = call.kw(kind)
            if not isinstance(ref, str):
                _fail("the BeamSection on line {} has no literal {}= name".format(call.lineno, kind))
            if ref not in table:
                _fail(
                    "the BeamSection on line {} refers to the {} {!r}, which is never created".format(
                        call.lineno, kind, ref
                    )
                )
            if table[ref].lineno > call.lineno:
                _fail(
                    "the BeamSection on line {} uses the {} {!r} before it is created on line {}".format(
                        call.lineno, kind, ref, table[ref].lineno
                    )
                )


def check_sections_defined_before_use(graph: ScriptGraph) -> None:
    sections = graph.created_names("BeamSection")
    for call in graph.by_method("SectionAssignment"):
        ref = call.kw("sectionName")
        if not isinstance(ref, str):
            _fail("the SectionAssignment on line {} has no literal sectionName=".format(call.lineno))
        if ref not in sections:
            _fail(
                "the SectionAssignment on line {} names the section {!r}, which is never "
                "created".format(call.lineno, ref)
            )
        if sections[ref].lineno > call.lineno:
            _fail(
                "the SectionAssignment on line {} uses the section {!r} before it is created on "
                "line {}".format(call.lineno, ref, sections[ref].lineno)
            )


def check_every_part_instanced_once(graph: ScriptGraph) -> None:
    parts = graph.created_names("Part")
    if not parts:
        _fail("the emitted script creates no Part at all")
    instanced: dict[str, int] = {}
    for call in graph.by_method("Instance"):
        ref = call.kw("part")
        producer = graph.resolve_call(ref)
        part_name = None
        if producer is not None and producer.method == "Part":
            part_name = producer.kw("name")
        elif isinstance(ref, str):
            part_name = ref
        if part_name is None:
            _fail("the Instance on line {} does not name a part this reader can follow".format(call.lineno))
        if part_name not in parts:
            _fail("the Instance on line {} instances {!r}, which is never created".format(call.lineno, part_name))
        instanced[part_name] = instanced.get(part_name, 0) + 1
    for name in sorted(parts):
        count = instanced.get(name, 0)
        if count != 1:
            _fail("the part {!r} is instanced {} times; it must be instanced exactly once".format(name, count))


def check_every_member_is_sectioned_and_oriented(graph: ScriptGraph) -> None:
    """One wire, one section assignment, one orientation -- and all three on the same region."""
    segments = _wire_segments(graph)
    if not segments:
        _fail("the emitted script draws no WirePolyLine at all")
    assigned = [_region_key(graph, c) for c in graph.by_method("SectionAssignment")]
    oriented = [_region_key(graph, c) for c in graph.by_method("assignBeamSectionOrientation")]
    if sorted(assigned) != sorted(set(assigned)):
        _fail("a region is given a section assignment more than once: {}".format(sorted(assigned)))
    if sorted(oriented) != sorted(set(oriented)):
        _fail("a region is given a beam orientation more than once: {}".format(sorted(oriented)))
    if set(assigned) != set(oriented):
        missing_orientation = sorted(set(assigned) - set(oriented))
        missing_section = sorted(set(oriented) - set(assigned))
        _fail(
            "section assignments and beam orientations cover different regions: "
            "no orientation for {}, no section for {}".format(missing_orientation, missing_section)
        )
    if len(assigned) != len(segments):
        _fail("{} members are drawn but {} regions get a section assignment".format(len(segments), len(assigned)))


def check_members_are_located_by_a_cylinder_spanning_them(graph: ScriptGraph) -> None:
    """The located cylinder must be the member's own axis, with its end caps outside the member."""
    segments = _wire_segments(graph)
    unmatched = list(segments)
    for call in graph.by_method("SectionAssignment"):
        lookup = _region_lookup(graph, call, "SectionAssignment")
        c1, c2 = _cylinder_axis(lookup, graph)
        cyl_dir = _unit((c2[0] - c1[0], c2[1] - c1[1], c2[2] - c1[2]))
        hit = None
        for segment in unmatched:
            p1, p2, _ = segment
            seg_dir = _unit((p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]))
            if abs(abs(_dot(cyl_dir, seg_dir)) - 1.0) > COLLINEAR_REL_TOL:
                continue
            # both endpoints must lie strictly inside the cylinder's caps
            span = _length((c2[0] - c1[0], c2[1] - c1[1], c2[2] - c1[2]))
            ts = []
            for point in (p1, p2):
                rel = (point[0] - c1[0], point[1] - c1[1], point[2] - c1[2])
                off = (
                    rel[0] - _dot(rel, cyl_dir) * cyl_dir[0],
                    rel[1] - _dot(rel, cyl_dir) * cyl_dir[1],
                    rel[2] - _dot(rel, cyl_dir) * cyl_dir[2],
                )
                if _length(off) > COLLINEAR_REL_TOL * span:
                    ts = None
                    break
                ts.append(_dot(rel, cyl_dir))
            if ts is None:
                continue
            if min(ts) <= 0.0 or max(ts) >= span:
                _fail(
                    "the cylinder on line {} does not overshoot its member's ends, so an end "
                    "sub-edge can fall outside it".format(lookup.lineno)
                )
            hit = segment
            break
        if hit is None:
            _fail("the cylinder on line {} does not lie on any member drawn by WirePolyLine".format(lookup.lineno))
        unmatched.remove(hit)
    if unmatched:
        _fail("{} drawn members are never located by a cylinder: {}".format(len(unmatched), unmatched))


def check_orientation_vectors(graph: ScriptGraph) -> None:
    """``n1`` must be a unit vector perpendicular to the member it orients."""
    calls = graph.by_method("assignBeamSectionOrientation")
    if not calls:
        _fail("the emitted script assigns no beam section orientation at all")
    for call in calls:
        method = call.raw_kwargs.get("method")
        if method != "N1_COSINES":
            _fail("the orientation on line {} uses method={!r}; phase 1 emits N1_COSINES".format(call.lineno, method))
        n1 = _as_vec3(graph.resolve_value(call.kw("n1")))
        if n1 is None:
            _fail("the orientation on line {} has no readable n1= of three numbers".format(call.lineno))
        if abs(_length(n1) - 1.0) > UNIT_TOL:
            _fail("n1 on line {} has length {:.12g}, not 1".format(call.lineno, _length(n1)))
        lookup = _region_lookup(graph, call, "assignBeamSectionOrientation")
        c1, c2 = _cylinder_axis(lookup, graph)
        axis = _unit((c2[0] - c1[0], c2[1] - c1[1], c2[2] - c1[2]))
        if abs(_dot(axis, n1)) > PERP_TOL:
            _fail(
                "n1 on line {} is not perpendicular to its member axis: n1.t = {:.12g}".format(
                    call.lineno, _dot(axis, n1)
                )
            )


def check_failure_is_signalled(graph: ScriptGraph) -> None:
    """CAE can exit 0 on a half-built model, so the script must say so itself."""
    exits = [c for c in graph.by_method("exit", "_exit") if c.receiver in ("sys", "os", "")]
    if not any(c.args and c.args[0] not in (0, None) for c in exits):
        _fail("the emitted script never calls sys.exit with a non-zero status")
    if "cae_build_result.json" not in graph.source:
        _fail("the emitted script never writes a <stem>.cae_build_result.json")


def check_python_floor(graph: ScriptGraph) -> None:
    """Syntax floor: this repo still ships a py2.7 in-Abaqus script, and ``.format()`` costs nothing."""
    for node in ast.walk(graph.tree):
        forbidden = _PY27_FORBIDDEN.get(type(node).__name__)
        if forbidden is not None:
            _fail(
                "the emitted script uses a {} on line {}, which the py2.7 floor excludes".format(
                    forbidden, getattr(node, "lineno", "?")
                )
            )
    has_future_print = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(a.name == "print_function" for a in node.names)
        for node in ast.walk(graph.tree)
    )
    if not has_future_print:
        for call in graph.by_method("print"):
            if call.receiver == "" and len(call.args) > 1:
                _fail(
                    "print on line {} takes {} arguments but the script does not import "
                    "print_function, so py2 would print a tuple".format(call.lineno, len(call.args))
                )


ALL_CHECKS = (
    check_preamble,
    check_names_unique,
    check_profiles_and_materials_defined_before_use,
    check_sections_defined_before_use,
    check_every_part_instanced_once,
    check_every_member_is_sectioned_and_oriented,
    check_members_are_located_by_a_cylinder_spanning_them,
    check_orientation_vectors,
    check_failure_is_signalled,
    check_python_floor,
)


def check_emitted_script(source: str, name: str = "<emitted>") -> ScriptGraph:
    """Run every graph check over ``source``. Raises :class:`CaeGraphError` on the first failure."""
    graph = ScriptGraph(source, name=name)
    for check in ALL_CHECKS:
        check(graph)
    return graph


def orientations_by_set_name(source: str) -> dict[str, tuple[float, float, float]]:
    """``{set name: n1}`` for every oriented member -- the handle the cross-writer test needs."""
    graph = ScriptGraph(source)
    out = {}
    for call in graph.by_method("assignBeamSectionOrientation"):
        n1 = _as_vec3(graph.resolve_value(call.kw("n1")))
        out[_region_key(graph, call)] = n1
    return out
