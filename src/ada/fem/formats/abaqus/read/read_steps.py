"""History data: analysis steps and what they hold, plus model-level ``*Amplitude``.

Everything from the first ``*Step`` on is history data. The reader used to read none of it: a
deck's analysis steps, loads, output requests and amplitudes all went into the unread-keyword
report, and a model written by adapy came back with no analysis in it. This reads back what
the writer (``abaqus/write/write_steps.py`` and friends) writes:

* ``*Step`` with its procedure -- ``*Static`` (incl. stabilization), ``*Dynamic`` (implicit and
  ``EXPLICIT``), ``*Frequency``, ``*Complex Frequency``, ``*Steady State Dynamics``;
* boundary conditions, loads (``*Cload``, ``*Dload`` GRAV, ``*Dsload`` P), a point load's
  ``*Transform`` coordinate system, and contact written inside a step;
* output requests (``*Output`` field / history with the ``*Node``/``*Element``/``*Contact``/
  ``*Energy Output`` blocks under them) and ``*Restart``.

Each step gets its OWN ``StepSolverOptions``: ``Step.__init__`` defaults to one shared instance,
so setting a read step's restart interval on it would set every step's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .keywords import validate
from .lexer import (
    KeywordBlock,
    comment_property,
    iter_enclosed,
    iter_keywords,
    mark_read,
    normalize,
    tokenize,
)

if TYPE_CHECKING:
    from ada import FEM, Assembly

#: The keywords this module consumes inside a step -- what the unread-keyword report may treat
#: as read in history data. (Model-level keywords are tracked by the reader's own mark_read.)
HISTORY_KEYWORDS = frozenset(
    {
        "STEP",
        "END STEP",
        "STATIC",
        "DYNAMIC",
        "FREQUENCY",
        "COMPLEX FREQUENCY",
        "STEADY STATE DYNAMICS",
        "GLOBAL DAMPING",
        "LOAD CASE",
        "END LOAD CASE",
        "BOUNDARY",
        "CLOAD",
        "DLOAD",
        "DSLOAD",
        "OUTPUT",
        "NODE OUTPUT",
        "ELEMENT OUTPUT",
        "CONTACT OUTPUT",
        "ENERGY OUTPUT",
        "RESTART",
        "CONTACT PAIR",
        "CONTACT",
        "CONTACT INCLUSIONS",
        "CONTACT PROPERTY ASSIGNMENT",
    }
)

_PROCEDURES = ("STATIC", "DYNAMIC", "FREQUENCY", "COMPLEX FREQUENCY", "STEADY STATE DYNAMICS")
_OUTPUT_BLOCKS = ("NODE OUTPUT", "ELEMENT OUTPUT", "CONTACT OUTPUT", "ENERGY OUTPUT")


def first_step_offset(bulk_str: str) -> int:
    """Where history data begins: the first line that is a ``*Step`` keyword, else the end.

    The FIRST step, not the last: a multi-step deck's earlier steps are history data too, and
    reading everything before the last ``*Step`` as model data took an earlier step's boundary
    condition for a model one.
    """
    for block in tokenize(bulk_str):
        if block.keyword == "STEP":
            return block.start
    return len(bulk_str)


def _yes(value) -> bool:
    return value is not None and str(value).strip().upper() in ("YES", "TRUE", "ON")


def _floats(line: str) -> list[float | None]:
    out = []
    for field in line.split(","):
        field = field.strip()
        out.append(float(field) if field else None)
    return out


def _words(block: KeywordBlock) -> list[str]:
    return [w.strip() for line in block.data_lines for w in line.split(",") if w.strip()]


# ── amplitudes ──────────────────────────────────────────────────────────────────────────────


def read_amplitudes(bulk_str: str, fem: FEM) -> None:
    """Model-level ``*Amplitude`` (tabular): ``x, y`` pairs, any number per data line."""
    from ada.fem import Amplitude

    for block in iter_keywords(bulk_str, "AMPLITUDE"):
        validate(block)
        values = [v for line in block.data_lines for v in _floats(line) if v is not None]
        smooth = block.params.get("SMOOTH")
        fem.add_amplitude(
            Amplitude(
                block.params.get("NAME"),
                values[0::2],
                values[1::2],
                smooth=float(smooth) if smooth is not None else None,
            )
        )


# ── the steps ───────────────────────────────────────────────────────────────────────────────


def read_steps(history_str: str, assembly: Assembly) -> None:
    """Every ``*Step ... *End Step`` in ``history_str``, added to ``assembly.fem`` in deck order."""
    mark_read("STEP", "END STEP")
    for number, (block, body) in enumerate(iter_enclosed(history_str, "STEP", "END STEP"), start=1):
        try:
            step = _read_step(block, body, assembly, number)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # one step this reader cannot make sense of costs that step, not the model
            from ada.fem.formats import conversion_report

            conversion_report.current().omitted(
                "abaqus reader", "*STEP", block.params.get("NAME") or f"Step-{number}", "not readable", error=str(exc)
            )
            continue
        if step is not None:
            assembly.fem.add_step(step)


def parse_step(text: str, assembly: Assembly):
    """The one ``*Step ... *End Step`` in ``text`` as a step, its sets looked up in ``assembly``
    -- not added to it. None if ``text`` is not exactly one step."""
    steps = list(iter_enclosed(text, "STEP", "END STEP"))
    if len(steps) != 1:
        return None
    return _read_step(*steps[0], assembly)


def _read_step(block: KeywordBlock, body: str, assembly: Assembly, number: int = 1):
    from ada.fem.formats.abaqus.solver import Stabilize, StabilizeTypes
    from ada.fem.steps import (
        StepEigen,
        StepEigenComplex,
        StepExplicit,
        StepImplicitDynamic,
        StepImplicitStatic,
        StepSolverOptions,
        StepSteadyState,
    )

    validate(block)
    name = block.params.get("NAME") or f"Step-{number}"  # Abaqus's own name for an unnamed step
    nl_geom = _yes(block.params.get("NLGEOM"))
    inc = block.params.get("INC")
    options = StepSolverOptions()
    abq = options.ABAQUS
    if block.params.get("UNSYMM") is not None:
        abq.unsymm = _yes(block.params.get("UNSYMM"))

    blocks = tokenize(body)
    procedure = next((b for b in blocks if b.keyword in _PROCEDURES), None)
    if procedure is None:
        from ada.fem.formats import conversion_report

        conversion_report.current().omitted(
            "abaqus reader", "*STEP", name, "no procedure this reader knows", first_line=block.lineno
        )
        return None
    validate(procedure)
    common = dict(nl_geom=nl_geom, solver_options=options, use_default_outputs=False)
    first = _floats(procedure.data_lines[0]) if procedure.data_lines else []

    if procedure.keyword == "STATIC":
        stab = procedure.params.get("STABILIZE")
        if "STABILIZE" in procedure.params:
            allsdtol = procedure.params.get("ALLSDTOL")
            allsdtol = float(allsdtol) if allsdtol is not None else None
            if stab is None:  # ``stabilize, factor=f``: damping factor given directly
                abq.stabilize = Stabilize(float(procedure.params.get("FACTOR")), allsdtol, StabilizeTypes.DAMPING)
            else:  # ``stabilize=f``: dissipated energy fraction
                abq.stabilize = Stabilize(float(stab), allsdtol, StabilizeTypes.ENERGY)
        init, total, min_incr, max_incr = (first + [None] * 4)[:4]
        init = total if init is None else init  # blank: Abaqus starts with the whole period
        step = StepImplicitStatic(
            name,
            total_time=total,
            total_incr=int(inc) if inc is not None else 1000,
            init_incr=init,
            min_incr=min_incr,
            max_incr=max_incr,
            **common,
        )
    elif procedure.keyword == "DYNAMIC" and "EXPLICIT" in procedure.params:
        total = (first + [None, None])[1]
        step = StepExplicit(name, total_time=total, **common)
    elif procedure.keyword == "DYNAMIC":
        initial = procedure.params.get("INITIAL")
        if initial is not None:
            abq.init_accel_calc = _yes(initial)
        init, total, min_incr, max_incr = (first + [None] * 4)[:4]
        init = total if init is None else init  # blank: Abaqus starts with the whole period
        step = StepImplicitDynamic(
            name,
            dyn_type=procedure.params.get("APPLICATION") or StepImplicitDynamic.TYPES_DYNAMIC.QUASI_STATIC,
            total_time=total,
            total_incr=int(inc) if inc is not None else 1000,
            init_incr=init,
            min_incr=min_incr,
            max_incr=max_incr,
            **common,
        )
    elif procedure.keyword == "FREQUENCY":
        step = StepEigen(name, num_eigen_modes=int(first[0]), **common)
    elif procedure.keyword == "COMPLEX FREQUENCY":
        friction = _yes(procedure.params.get("FRICTION DAMPING"))
        step = StepEigenComplex(name, num_eigen_modes=int(first[0]), friction_damping=friction, **common)
    else:  # STEADY STATE DYNAMICS
        step = _steady_state(name, procedure, blocks, assembly, common, StepSteadyState)

    for b in blocks:
        if b.keyword == "RESTART":
            validate(b)
            freq = b.params.get("FREQUENCY")
            if freq is not None:
                abq.restart_int = int(float(freq))

    _read_step_bcs(body, step, assembly)
    _read_loads(blocks, step, assembly)
    _read_outputs(blocks, step, assembly)
    _read_step_interactions(body, step, assembly)
    return step


def _steady_state(name, procedure: KeywordBlock, blocks, assembly, common, cls):
    """``*Steady State Dynamics`` + ``*Global Damping`` + the unit load of its ``*Load Case``.

    The frequency range is the first data line's ``lower, upper[, points]``. Decks adapy wrote
    before listed one frequency per line (upper limit blank); those read as first..last.
    """
    rows = [_floats(line) for line in procedure.data_lines]
    fmin = rows[0][0]
    fmax = rows[0][1] if len(rows[0]) > 1 and rows[0][1] else rows[-1][0]
    damping = next((b for b in blocks if b.keyword == "GLOBAL DAMPING"), None)
    alpha = beta = None
    if damping is not None:
        validate(damping)
        alpha = float(damping.params.get("ALPHA")) if damping.params.get("ALPHA") is not None else None
        beta = float(damping.params.get("BETA")) if damping.params.get("BETA") is not None else None
    cload = next((b for b in blocks if b.keyword == "CLOAD"), None)
    unit_load = None
    if cload is not None:
        loads = _cloads([cload], assembly)
        unit_load = loads[0] if loads else None
    kwargs = {}
    if alpha is not None:
        kwargs["alpha"] = alpha
    if beta is not None:
        kwargs["beta"] = beta
    return cls(name, unit_load, fmin, fmax, **kwargs, **common)


# ── boundary conditions, loads, outputs, interactions ───────────────────────────────────────


def _bc_fem(body: str, assembly: Assembly):
    """The FEM a step's ``*Boundary`` references resolve in: the assembly's, unless a bare name
    or node label is found only in a part -- a flat deck, whose one part holds its sets."""
    refs = {
        line.split(",")[0].strip()
        for block in tokenize(body)
        if block.keyword == "BOUNDARY"
        for line in block.data_lines
    }
    bare = {r for r in refs if "." not in r}

    def resolves(fem, ref):
        return ref in fem.nsets or (ref.isdigit() and int(ref) in fem.nodes.dmap)

    if all(resolves(assembly.fem, r) for r in bare):
        return assembly.fem
    for part in assembly.get_all_parts_in_assembly():
        if all(resolves(part.fem, r) for r in bare):
            return part.fem
    return assembly.fem


def _read_step_bcs(body: str, step, assembly: Assembly) -> None:
    from .reader import get_bcs_from_bulk

    for bc in get_bcs_from_bulk(body, _bc_fem(body, assembly)):
        bc.parent = step
        step.add_bc(bc)


def _set(ref: str, assembly: Assembly, kind: str):
    from .helper_utils import get_set_from_assembly

    ref = ref.strip()
    if kind == "nset" and ref.split(".")[-1].isdigit():
        return _node_label_set(ref, assembly)
    try:
        return get_set_from_assembly(ref, assembly.fem, kind)
    except (ValueError, KeyError):
        pass
    # A set the reader filed somewhere other than where the reference points: an instance set
    # of connector elements lives at assembly level. Found by name, instance prefix dropped.
    name = ref.split(".")[-1].lower()
    for part in assembly.get_all_parts_in_assembly(include_self=True):
        pool = part.fem.surfaces.values() if kind == "surface" else part.fem.sets
        for s in pool:
            if s.name.lower() == name and (kind == "surface" or s.type == kind):
                return s
    raise KeyError(f"no {kind} named {ref!r}")


def _amplitude(name: str | None, assembly: Assembly):
    """The model's ``*Amplitude`` a load names, as the object (the name if the deck has none)."""
    if name is None:
        return None
    for amp_name, amp in assembly.fem.amplitudes.items():
        if amp_name.lower() == name.lower():
            return amp
    return name


def _node_label_set(ref: str, assembly: Assembly):
    """A node label where a node set may stand (``3`` or ``Part-1.3``), as a one-node set named
    after it: loads and outputs refer to sets. Written back, it is an ``*Nset`` of that node."""
    from ada.fem import FemSet

    *prefix, label = ref.split(".")
    for part in assembly.get_all_parts_in_assembly(include_self=True):
        fem = part.fem
        if prefix and prefix[0].lower() != (fem.instance_name or fem.name).lower():
            continue
        try:
            node = fem.nodes.from_id(int(label))
        except ValueError:
            continue
        name = f"_n{label}"
        if name in fem.nsets:
            return fem.nsets[name]
        return fem.add_set(FemSet(name, [node], "nset", parent=fem))
    raise KeyError(f"no node labelled {ref!r}")


def _load_name(block: KeywordBlock, fallback: str) -> tuple[str, str | None]:
    props = comment_property(block, "Name", "Type")
    return props.get("Name") or fallback, props.get("Type")


def _cloads(blocks: list[KeywordBlock], assembly: Assembly):
    """``*Cload`` blocks as point loads, one per (name, set).

    The writer splits a load into ``<name>_F`` (forces) and ``<name>_M`` (moments) blocks; both
    halves come back as ONE load with its per-DOF forces and magnitude 1 -- the split into a
    magnitude and a direction is not something the text holds (see canonical.py R11).
    """
    from ada.fem.loads import LoadPoint

    loads: dict[tuple[str, str], dict] = {}
    for i, block in enumerate(blocks):
        validate(block)
        name, kind = _load_name(block, f"cload{i + 1}")
        base = name
        if kind is not None and name[-2:] in ("_F", "_M"):
            base = name[:-2]
        for line in block.data_lines:
            fields = [f.strip() for f in line.split(",")]
            ref, dof = fields[0], int(float(fields[1]))
            value = float(fields[2]) if len(fields) > 2 and fields[2] else 0.0
            entry = loads.setdefault(
                (base, ref),
                dict(
                    forces=[0.0] * 6,
                    follower="FOLLOWER" in block.params,
                    amplitude=block.params.get("AMPLITUDE"),
                ),
            )
            entry["forces"][dof - 1] = value

    out = []
    for (base, ref), entry in loads.items():
        fem_set = _set(ref, assembly, "nset")
        csys = _transform_csys(fem_set, assembly)
        out.append(
            LoadPoint(
                base,
                1.0,
                fem_set,
                entry["forces"],
                amplitude=_amplitude(entry["amplitude"], assembly),
                follower_force=entry["follower"],
                csys=csys,
            )
        )
    return out


def _read_loads(blocks: list[KeywordBlock], step, assembly: Assembly) -> None:
    from ada.fem.loads import Load, LoadGravity, LoadPressure

    mark_read("CLOAD", "DLOAD", "DSLOAD")
    in_load_case = False
    cloads = []
    for block in blocks:
        if block.keyword == "LOAD CASE":
            in_load_case = True  # a steady-state step's unit load, not one of its loads
        elif block.keyword == "END LOAD CASE":
            in_load_case = False
        elif block.keyword == "CLOAD" and not in_load_case:
            cloads.append(block)
        elif block.keyword == "DLOAD":
            validate(block)
            name, kind = _load_name(block, "gravity")
            for line in block.data_lines:
                fields = [f.strip() for f in line.split(",")]
                if len(fields) < 3 or fields[1].upper() != "GRAV":
                    # an element-face or body load (P1, BX, ...): adapy has no load for it yet
                    from ada.fem.formats import conversion_report

                    conversion_report.current().omitted(
                        "abaqus reader",
                        "*DLOAD",
                        f"{step.name}: {line.strip()}",
                        f"load type {fields[1] if len(fields) > 1 else '?'} is not read (only GRAV)",
                        first_line=block.lineno,
                    )
                    continue
                magnitude = float(fields[2])
                direction = [float(x) if x else 0.0 for x in fields[3:6]]
                if kind is not None and normalize(kind) == "ACCELERATION":
                    load = Load(name, Load.TYPES.ACC, magnitude, dof=[_int_if_whole(x) for x in direction])
                else:
                    load = LoadGravity(name, magnitude)
                    load._dof = [_int_if_whole(x) for x in direction]
                step.add_load(load)
        elif block.keyword == "DSLOAD":
            validate(block)
            name, _ = _load_name(block, "pressure")
            for line in block.data_lines:
                fields = [f.strip() for f in line.split(",")]
                surface = _set(fields[0], assembly, "surface")
                step.add_load(LoadPressure(name, float(fields[2]), surface))
    for load in _cloads(cloads, assembly):
        step.add_load(load)


def _int_if_whole(x: float):
    return int(x) if float(x).is_integer() else x


def _transform_csys(fem_set, assembly: Assembly):
    """The ``*Transform`` a load's node set carries, as the load's coordinate system.

    The writer puts it on an internal ``_T-<SET>`` node set whose one member is the load's set
    (Abaqus/CAE's own convention). That set is the writer's scaffolding, not a model set, so it
    is removed once read; the transform becomes the load's ``csys``.
    """
    from ada.fem import Csys

    target = f"_T-{fem_set.name}".lower()
    # at assembly level where the writer puts it; in the part in a flat deck
    pools = [assembly.fem] + ([fem_set.parent] if fem_set.parent is not assembly.fem else [])
    found = next(((f, s) for f in pools for n, s in f.nsets.items() if n.lower() == target), None)
    if found is None:
        return None
    fem, t_set = found
    bulk = assembly.fem.metadata.get("_abaqus_transforms", {})
    coords = bulk.get(t_set.name.lower())
    if coords is None:
        return None
    # consumed: drop the scaffolding set
    fem.sets._nomap.pop(t_set.name, None)
    if t_set in fem.sets.sets:
        fem.sets.sets.remove(t_set)
    name = coords["name"] or f"csys_{fem_set.name}"
    a, b = coords["a"], coords["b"]
    c = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    return Csys(name, coords=[tuple(a), tuple(b), tuple(_int_if_whole(x) for x in c)], parent=fem)


def read_transforms(model_str: str, fem: FEM) -> None:
    """Record every ``*Transform`` (name from the ``** Transform:`` comment above it, the two
    points of its data line) so a load read later can take it as its coordinate system."""
    mark_read("TRANSFORM")
    store = fem.metadata.setdefault("_abaqus_transforms", {})
    for block in iter_keywords(model_str, "TRANSFORM"):
        validate(block)
        values = [v for line in block.data_lines for v in _floats(line) if v is not None]
        name = comment_property(block, "Transform").get("Transform")
        store[(block.params.get("NSET") or "").lower()] = dict(
            name=name,
            a=[_int_if_whole(x) for x in values[0:3]],
            b=[_int_if_whole(x) for x in values[3:6]],
        )


def _interval(params) -> tuple[str, float]:
    from ada.fem.outputs import IntervalTypes

    for key, value in params.items():
        if key in ("FIELD", "HISTORY", "VARIABLE", "OP"):
            continue
        kind = IntervalTypes.INTERVAL if key == normalize(IntervalTypes.INTERVAL) else key.lower()
        number = float(value)
        return kind, int(number) if number.is_integer() else number
    return IntervalTypes.FREQUENCY, 1


def _read_outputs(blocks: list[KeywordBlock], step, assembly: Assembly) -> None:
    """``*Output, field`` / ``*Output, history`` and the output blocks under each.

    A field output collects every ``*Node/Element/Contact Output`` until the next ``*Output``;
    a history output is the one output block after its ``*Output``. Names come from the
    ``** FIELD OUTPUT:`` / ``** HISTORY OUTPUT:`` comments the writer (and Abaqus/CAE) puts there.
    """
    from ada.fem.outputs import FieldOutput

    current = None  # ("field", FieldOutput) | ("history", (kind, value, name above it))
    for block in blocks:
        if block.keyword == "OUTPUT":
            validate(block)
            kind, value = _interval(block.params)
            if "FIELD" in block.params:
                name = (
                    comment_property(block, "FIELD OUTPUT").get("FIELD OUTPUT") or f"field{len(step.field_outputs) + 1}"
                )
                fo = FieldOutput(name, nodal=[], element=[], contact=[], int_value=value, int_type=kind)
                step.add_field_output(fo)
                current = ("field", fo)
            else:  # CAE names it above the *Output; adapy's writer below it (on the next block)
                current = ("history", (kind, value, comment_property(block, "HISTORY OUTPUT").get("HISTORY OUTPUT")))
            continue
        if block.keyword not in _OUTPUT_BLOCKS or current is None:
            continue
        validate(block)
        variables = _words(block)
        if current[0] == "field":
            fo = current[1]
            if block.keyword == "NODE OUTPUT":
                fo._nodal = variables
            elif block.keyword == "ELEMENT OUTPUT":
                fo._element = variables
            elif block.keyword == "CONTACT OUTPUT":
                fo._contact = variables
            continue
        kind, value, above = current[1]
        name = (
            comment_property(block, "HISTORY OUTPUT").get("HISTORY OUTPUT")
            or above
            or f"hist{len(step.hist_outputs) + 1}"
        )
        try:
            ho = _hist_output(block, name, variables, value, kind, assembly)
        except (ValueError, KeyError) as exc:
            from ada.fem.formats import conversion_report

            conversion_report.current().omitted(
                "abaqus reader", f"*{block.keyword}", name, "names a set the deck does not define", error=str(exc)
            )
            current = None
            continue
        ho.parent = step
        step.hist_outputs.append(ho)
        current = None


def _hist_output(block: KeywordBlock, name: str, variables, value, kind, assembly: Assembly):
    from ada.fem.outputs import HistOutput

    if block.keyword == "NODE OUTPUT":
        return HistOutput(name, _set(block.params.get("NSET"), assembly, "nset"), "node", variables, value, kind)
    elif block.keyword == "ELEMENT OUTPUT":
        return HistOutput(name, _set(block.params.get("ELSET"), assembly, "elset"), "connector", variables, value, kind)
    elif block.keyword == "ENERGY OUTPUT":
        elset = block.params.get("ELSET")
        return HistOutput(name, _set(elset, assembly, "elset") if elset else None, "energy", variables, value, kind)
    else:  # CONTACT OUTPUT: the pair, (slave, master) as the writer holds it
        pair = [
            _set(block.params.get("SLAVE"), assembly, "surface"),
            _set(block.params.get("MASTER"), assembly, "surface"),
        ]
        return HistOutput(name, pair, "contact", variables, value, kind)


def _read_step_interactions(body: str, step, assembly: Assembly) -> None:
    """Contact written inside a step. adapy writes a MODEL interaction into the first step when
    that step is explicit (Abaqus/Explicit wants it there), so contact read from a step is a
    model interaction -- the inverse of that writer rule."""
    from .reader import add_interactions_from_bulk_str

    add_interactions_from_bulk_str(body, assembly)
