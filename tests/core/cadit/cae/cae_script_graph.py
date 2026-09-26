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
import re
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
#
# Perpendicularity is measured against the *cylinder axis*, which the writer derives from
# `Beam.axis_global()`. That matters: `beam.yvec` is exactly perpendicular to the unrounded axis, but
# `Beam.xvec` is a `Direction` rounded to 7 decimals, so `dot(yvec, xvec)` reads 4.5e-8 on a (6,3,4)
# member. Checking against `xvec` would need ~1e-6; against the endpoints, 1e-12 holds.
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
    """The set name behind a region -- unique across the whole script, and the INP's elset name."""
    set_call = graph.resolve_call(call.kw("region"))
    name = set_call.kw("name") if set_call is not None else None
    if isinstance(name, str):
        return name
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
    """A collision must fail, not silently overwrite -- and a dot is rejected by CAE outright.

    Measured, and it is why this check is not redundant with "CAE would have noticed": a duplicate
    ``Part`` or ``Set`` name does **not** raise. CAE replaces the object and invalidates every handle to
    the old one, so the script runs on and the model is quietly wrong. "The kernel would have caught it"
    is not available as an argument anywhere in this verification.

    Set names are checked *globally*, not per part: the writer's result sidecar keys ``edges_per_member``
    by set name, so a name reused in a second part would silently overwrite one member's edge count even
    though CAE itself scopes sets to their part.
    """
    namespaces = {
        "part": ("Part",),
        "material": ("Material",),
        "profile": tuple(sorted(PROFILE_METHODS)),
        "section": ("BeamSection",),
        "instance": ("Instance",),
        "set": ("Set",),
    }
    for namespace in sorted(namespaces):
        seen: dict[str, int] = {}
        for call in graph.by_method(*namespaces[namespace]):
            name = call.kw("name")
            if not isinstance(name, str):
                continue
            if "." in name:
                _fail(
                    "the {} name {!r} on line {} contains a dot, which CAE rejects".format(namespace, name, call.lineno)
                )
            if name in seen:
                _fail(
                    "the {} name {!r} is emitted twice, on lines {} and {}".format(
                        namespace, name, seen[name], call.lineno
                    )
                )
            seen[name] = call.lineno


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


def _axis_positions(segment, c1, cyl_dir, span) -> list[float] | None:
    """Where a segment's endpoints fall along a cylinder's axis, or ``None`` if it is off that axis."""
    positions = []
    for point in segment[:2]:
        rel = (point[0] - c1[0], point[1] - c1[1], point[2] - c1[2])
        projection = _dot(rel, cyl_dir)
        off = (
            rel[0] - projection * cyl_dir[0],
            rel[1] - projection * cyl_dir[1],
            rel[2] - projection * cyl_dir[2],
        )
        if _length(off) > COLLINEAR_REL_TOL * span:
            return None
        positions.append(projection)
    return positions


def check_members_are_located_by_a_cylinder_spanning_them(graph: ScriptGraph) -> None:
    """The located cylinder must be the member's own axis, with its end caps outside the member.

    Stacked columns are ordinary, so two members are routinely *collinear* and lie on each other's
    cylinder axis. An earlier version of this check matched a cylinder to the first collinear segment it
    met and complained about the caps if that segment stuck out -- which reported "does not overshoot"
    against a perfectly good script whenever the regions happened not to be emitted in the same order as
    the wires. So a segment that sticks out of the caps is treated as *not this cylinder's member*, and
    the caps are only reported once no candidate fits at all. Where several fit, the tightest wins.
    """
    segments = _wire_segments(graph)
    unmatched = list(segments)
    for call in graph.by_method("SectionAssignment"):
        set_name = _region_key(graph, call)
        lookup = _region_lookup(graph, call, "SectionAssignment")
        c1, c2 = _cylinder_axis(lookup, graph)
        axis = (c2[0] - c1[0], c2[1] - c1[1], c2[2] - c1[2])
        span = _length(axis)
        cyl_dir = _unit(axis)

        on_axis, fitting = [], []
        for segment in unmatched:
            p1, p2, _ = segment
            seg_dir = _unit((p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]))
            if abs(abs(_dot(cyl_dir, seg_dir)) - 1.0) > COLLINEAR_REL_TOL:
                continue
            positions = _axis_positions(segment, c1, cyl_dir, span)
            if positions is None:
                continue
            on_axis.append((segment, positions))
            if min(positions) > 0.0 and max(positions) < span:
                # slack: how much cylinder is left over at the two caps
                fitting.append((min(positions) + (span - max(positions)), segment))

        if not on_axis:
            _fail(
                "the cylinder for member {!r} on line {} does not lie on any member drawn by "
                "WirePolyLine".format(set_name, lookup.lineno)
            )
        if not fitting:
            starts = [positions for _, positions in on_axis]
            _fail(
                "the cylinder for member {!r} on line {} does not overshoot its member's ends, so an "
                "end sub-edge can fall outside it (span {:.12g}, member ends at {})".format(
                    set_name, lookup.lineno, span, starts
                )
            )
        fitting.sort(key=lambda item: (item[0], item[1]))
        unmatched.remove(fitting[0][1])
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


#: Every CAE object the analysis emission creates that names a step and a region.
_STEP_SCOPED_METHODS = ("DisplacementBC", "ConcentratedForce", "Moment")
#: Abaqus' six ``DisplacementBC`` keywords.
_BC_KEYWORDS = ("u1", "u2", "u3", "ur1", "ur2", "ur3")
#: ``<anything>.sets['NAME']`` -- the only shape of region the analysis emission writes. The
#: receiver is deliberately unconstrained, for the same reason every other check here keys on the
#: method name: the writer is free to call its locals whatever it likes, and a checker that
#: insisted on one spelling would be testing a variable name.
_REGION_SUBSCRIPT = re.compile(r"^[A-Za-z_]\w*\.sets\[(?P<quote>['\"])(?P<name>[^'\"]+)(?P=quote)\]$")


def check_analysis_references_resolve(graph: ScriptGraph) -> None:
    """Every support and load names a step and a region that exist, and restrains something.

    Nothing here fires on a geometry-only script, which emits none of these calls. On one that
    carries an analysis, what it catches is the class of defect a *reader* can catch and the
    kernel cannot usefully report: a load in a step that was never created, a region named
    before it is built, a ``DisplacementBC`` with every DOF ``UNSET`` (an object that reads as
    a support and is not one), a ``ConcentratedForce`` of nothing, and a region no support or
    load ever acts on.

    Regions are read out of the emitted ``_analysis_region`` helper rather than out of a
    ``Set`` call, because that is where they are created: the helper exists so that a
    ``findAt`` returning no vertex fails loudly in the kernel, and it keeps its arguments
    positional so a text reader can see them.
    """
    regions: dict[str, int] = {}
    for call in graph.calls:
        if call.method != "_analysis_region":
            continue
        if len(call.args) < 5:
            _fail("the _analysis_region on line {} takes {} argument(s), not 5".format(call.lineno, len(call.args)))
        name, instance, points = call.args[1], call.args[2], call.args[3]
        if not isinstance(name, str) or not isinstance(instance, str):
            _fail("the _analysis_region on line {} has no literal set and instance name".format(call.lineno))
        if name in regions:
            _fail(
                "the support/load region {!r} is created twice, on lines {} and {}; CAE would replace "
                "the first with the second".format(name, regions[name], call.lineno)
            )
        if not isinstance(points, tuple) or not points:
            _fail("the region {!r} on line {} names no point at all".format(name, call.lineno))
        for point in points:
            if _as_vec3(point) is None:
                _fail(
                    "the region {!r} on line {} names {!r}, which is not three numbers".format(name, call.lineno, point)
                )
        regions[name] = call.lineno

    steps: dict[str, int] = {}
    for call in graph.by_method("StaticStep"):
        name = call.kw("name")
        previous = call.kw("previous")
        if not isinstance(name, str):
            _fail("the StaticStep on line {} has no literal name=".format(call.lineno))
        if name in steps:
            _fail("the step {!r} is created twice, on lines {} and {}".format(name, steps[name], call.lineno))
        if previous != "Initial" and previous not in steps:
            _fail(
                "the step {!r} on line {} follows {!r}, which is neither 'Initial' nor a step created "
                "before it -- so the step chain is not a chain".format(name, call.lineno, previous)
            )
        steps[name] = call.lineno

    used: set[str] = set()
    for call in graph.by_method("FieldOutputRequest", *_STEP_SCOPED_METHODS):
        step = call.kw("createStepName")
        if step != "Initial" and step not in steps:
            _fail(
                "the {} on line {} is created in step {!r}, which is neither 'Initial' nor a step this "
                "script creates".format(call.method, call.lineno, step)
            )
        if call.method == "FieldOutputRequest":
            continue
        raw = call.raw_kwargs.get("region", "")
        match = _REGION_SUBSCRIPT.match(raw)
        if match is None:
            _fail(
                "the {} on line {} acts on region {!r}, which is not an assembly set this reader can "
                "follow".format(call.method, call.lineno, raw)
            )
        region = match.group("name")
        if region not in regions:
            _fail(
                "the {} on line {} acts on the region {!r}, which is never created".format(
                    call.method, call.lineno, region
                )
            )
        if regions[region] > call.lineno:
            _fail(
                "the {} on line {} acts on the region {!r} before it is created on line {}".format(
                    call.method, call.lineno, region, regions[region]
                )
            )
        used.add(region)

    for call in graph.by_method("DisplacementBC"):
        missing = [keyword for keyword in _BC_KEYWORDS if keyword not in call.raw_kwargs]
        if missing:
            _fail(
                "the DisplacementBC on line {} does not say what it does with {}; every DOF is written, "
                "with UNSET for the free ones, so a reader never has to know CAE's default".format(call.lineno, missing)
            )
        if all(call.raw_kwargs[keyword] == "UNSET" for keyword in _BC_KEYWORDS):
            _fail(
                "the DisplacementBC on line {} leaves every DOF UNSET, so it is an object that reads as "
                "a support and restrains nothing".format(call.lineno)
            )

    for call in graph.by_method("ConcentratedForce", "Moment"):
        prefix = "cf" if call.method == "ConcentratedForce" else "cm"
        components = [call.kw("{}{}".format(prefix, axis)) for axis in (1, 2, 3)]
        if any(component is None for component in components):
            _fail(
                "the {} on line {} does not give all three {}1..{}3 components".format(
                    call.method, call.lineno, prefix, prefix
                )
            )
        if not any(component for component in components):
            _fail(
                "the {} on line {} is zero in every component, so it is a load that loads "
                "nothing".format(call.method, call.lineno)
            )

    orphans = sorted(set(regions) - used)
    if orphans:
        _fail(
            "the region(s) {} are created and no support or load acts on them. A region nothing uses is "
            "a support or a load that did not arrive".format(orphans)
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
    check_analysis_references_resolve,
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
