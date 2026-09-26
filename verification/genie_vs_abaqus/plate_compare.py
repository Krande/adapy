"""The plate case's comparison: convergence, extrapolation, and the guards it needs of its own.

:mod:`compare` is reused unchanged for the cross-solver verdict -- the per-point, per-component
table, the probe-set rule and the all-zero rule all apply as written. What this module adds is
the part shells need and beams did not: a **convergence sequence per solver per variant**, the
extrapolation that turns three tables into one, and four plate-specific guards.

Why the tolerance is what it is
===============================

:data:`PLATE_REL_TOL` is **1e-04**, and it is read off the convergence study rather than felt.
The reasoning has three steps and each one is a measurement.

**1. A single-mesh comparison would be measuring discretisation, not translation.** At the
coarsest of :data:`plate_model.MESH_SIZES` the two solvers' mid-span deflections differ by
7.65e-04 relative on the bare strip; at the finest, by 3.16e-05. Nothing about the translation
changed between those two runs. A tolerance able to pass the coarse mesh would have to be 8e-04
or looser, which is 44x the difference the *converged* answers have -- so it would be admitting
a real 5e-04 translation error as noise.

**2. Both solvers converge at second order, measured.** Three meshes give one error ratio and on
the bare strip it is 4.0000 for Sestra and 3.9997 for Abaqus, i.e. ``p = 2.0000`` and
``p = 1.9999`` (:func:`plate_hand_check.observed_order`). That is what licenses a Richardson
extrapolation instead of a guess at the limit, and the order is re-measured on every run rather
than assumed -- a sequence outside :data:`plate_hand_check.ORDER_BAND` raises.

**3. The extrapolants then agree to 1.7e-05.** Bare strip, mid-span ``u3``:

    Sestra   0.1733333194   (closed form 0.1733333333, rel 8.02e-09)
    Abaqus   0.1733363138   (closed form 0.1733333333, rel 1.720e-05)
    between them                                        rel 1.720e-05

and on the stiffened strip 1.271e-05. Across **all** significant components of all seven probes
and both variants the worst is **1.81e-05** -- the bare strip's quarter-span deflection; the
stiffened strip's worst is 1.76e-05 at the supported edge's rotation. So 1e-04 is 5.5x the worst
residual that a correct translation actually leaves, and it is 100x below the smallest thing it
exists to catch. What a plate translation gets wrong is not subtle:

=================================================  ==========================================
defect                                             what it does to the answer
=================================================  ==========================================
the long-edge constraint lost (no cylindrical       up to 9% -- the whole ``1 - nu^2``
bending)
a thickness read as 12 mm instead of 10             42% stiffer -- ``t^3``
a rotation fixed at a "simple" support              5x stiffer
the stiffener present but not attached              2.66x (that is
                                                    :func:`assert_stiffener_present`)
the stiffener's section rotated 90 degrees          2.46x -- a ratio of 0.924, not 0.376
half the pressure                                  2x
=================================================  ==========================================

:data:`PLATE_MESH_REL_TOL` is the looser **1e-03** used for the *per-mesh* table, which is printed
for the diagnosis it gives rather than as the verdict. It is applied to the finest mesh, where the
worst component is 7.21e-05 (the stiffened strip's quarter-span deflection), so it has 14x of
headroom there -- and it is deliberately **not** applied to the coarser two, whose 7.65e-04 gap
would sit just inside it. A tolerance that a coarse mesh only just passes is not a tolerance.

What this comparison can and cannot prove is :mod:`compare`'s docstring, with one addition and
one subtraction. The addition: agreeing *after extrapolation* is a stronger statement than
agreeing at one mesh, because it is a statement about the two solvers' limits and not about two
meshes that happened to land close. The subtraction: it says nothing about behaviour at a mesh
too coarse to be converged, which for shells is where real models live -- the per-mesh table is
what speaks to that, and it shows the two elements 7.65e-04 apart at 32 elements per span, with
``S4R`` the softer of the two at every density.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import compare, plate_hand_check, plate_model
from .displacements import COMPONENTS, DisplacementTable

#: Cross-solver relative tolerance on the **extrapolated** tables. See the module docstring.
PLATE_REL_TOL = 1.0e-04

#: Cross-solver relative tolerance on a **single mesh**. Looser, and diagnostic rather than the
#: verdict: the two elements' discretisation errors differ by a measured 7.65e-04 at the coarsest
#: density, which is a property of the elements and not of the translation.
PLATE_MESH_REL_TOL = 1.0e-03

#: How closely the three mid-span probes must agree for the strip to be in cylindrical bending.
#:
#: 1e-03. Measured, the *bare* strip's three mid-span values are **identical** in both solvers, at
#: every density: the spread this returns is exactly 0.0, so nothing about that case rests on the
#: tolerance. It is set by the **stiffened** strip, which genuinely varies by 8.1e-05 across the width
#: because the bar is a line of stiffness at the centreline and the plate spans transversely
#: between it and the long edges (the same 8e-05 in both solvers, which is what says it is the
#: model). 1e-03 is 12x that. Losing the long-edge constraint entirely fans these three points out
#: by percent, not by 1e-04.
CYLINDRICAL_REL_TOL = 1.0e-03

#: How closely a solver's reaction total must match ``-q L b``, relative.
#:
#: 1e-06. Measured: Sestra returns 2000.0 exactly on the bare strip and 1999.99996 on the
#: stiffened one (2.3e-08 relative); Abaqus returns 2000.0 and 1999.999962 (1.9e-08). So this is 44x
#: the worst residual either solver leaves, and it is an equilibrium identity rather than a
#: modelling approximation -- there is nothing here for a physical effect to consume.
REACTION_REL_TOL = 1.0e-06


class NotCylindrical(AssertionError):
    """The three mid-span probes disagree, so the strip is not in cylindrical bending.

    Which means the closed form this whole case is checked against -- ``D`` with its
    ``1 - nu^2`` -- is the wrong one, by up to 9%. Fatal, because the numbers would otherwise
    look plausible: a strip with free long edges still deflects in a smooth half-sine and still
    reacts the whole load.
    """


class StiffenerMissing(AssertionError):
    """The measured stiffness ratio is not the one the parallel-spring closed form predicts.

    The check for the stiffened variant, and deliberately a *stiffness* check rather than an
    element count: a bar that is in the deck and attached to nothing, a bar whose section was
    rotated 90 degrees, and a run that solved the bare model twice all produce a ratio near 1.0
    against a predicted 0.376.
    """


class ReactionMismatch(AssertionError):
    """A solver did not react the load the pressure integrates to."""


class NotASequence(ValueError):
    """The list handed to :func:`extrapolate` is not one solver's one variant, coarse to fine.

    Deliberately **not** a subclass of :class:`plate_hand_check.NotConverging`, and that is not
    tidiness. The two say different things -- "these three numbers do not approach a limit" and
    "these three numbers are not of the same thing" -- and the arithmetic will often raise the
    first for a list that is wrong in the second way, which makes a test that accepts either
    unable to tell whether the structural check is still there. Found by mutation: deleting the
    :func:`_assert_one_sequence` call left every sequence check passing, because a list mixing two
    variants also happens to turn around.
    """


@dataclass(frozen=True)
class ComponentConvergence:
    """One component at one probe, over the refinement sequence."""

    probe: str
    component: str
    values: tuple[float, ...]
    #: ``None`` when the component is zero to within :data:`compare.ABS_FLOOR` at every mesh, in
    #: which case there is no rate to read and the finest value is carried through as it stands.
    convergence: plate_hand_check.Convergence | None
    extrapolated: float

    @property
    def negligible(self) -> bool:
        return self.convergence is None


@dataclass
class ConvergenceReport:
    """One solver, one variant: every component's sequence, plus the headline quantity.

    :attr:`deflection` is the mid-span ``u3`` sequence and is what the printed table leads with,
    because it is the one the closed form predicts and the one whose order the tolerance was set
    from. The rest are carried so that a component converging differently from the others is
    visible rather than averaged away.
    """

    solver: str
    solver_version: str
    variant: str
    mesh_sizes: tuple[float, ...]
    node_counts: tuple[int, ...]
    components: list[ComponentConvergence] = field(default_factory=list)
    closed_form: float = 0.0

    @property
    def deflection(self) -> ComponentConvergence:
        return self.at(plate_model.DEFLECTION_PROBE, "u3")

    def at(self, probe: str, component: str) -> ComponentConvergence:
        for entry in self.components:
            if entry.probe == probe and entry.component == component:
                return entry
        raise KeyError(f"{probe}.{component} is not in this report")

    @property
    def residuals(self) -> tuple[float, ...]:
        """``|value| / closed_form - 1`` at each mesh, for the headline deflection."""
        if not self.closed_form:
            return ()
        return tuple(abs(abs(v) / self.closed_form - 1.0) for v in self.deflection.values)

    def format_table(self) -> str:
        head = f"{'seed [m]':>10} {'nodes':>7} {'u3 mid [m]':>17} {'|rel| vs closed form':>21}"
        lines = [
            f"{self.solver} {self.solver_version} -- {self.variant} strip, mid-span deflection",
            head,
            "-" * len(head),
        ]
        residuals = self.residuals
        for index, size in enumerate(self.mesh_sizes):
            value = self.deflection.values[index]
            rel = f"{residuals[index]:.3e}" if residuals else "-"
            lines.append(f"{size:>10} {self.node_counts[index]:>7} {value:>17.10e} {rel:>21}")
        conv = self.deflection.convergence
        lines.append("-" * len(head))
        if conv is None:
            lines.append("the mid-span deflection is zero to within the floor -- nothing converged here")
        else:
            extrapolated = self.deflection.extrapolated
            rel = abs(abs(extrapolated) / self.closed_form - 1.0) if self.closed_form else 0.0
            lines.append(
                f"observed order {conv.order:.4f} (error ratios "
                f"{_ratio_text(self.deflection.values)}), Richardson extrapolant "
                f"{extrapolated:.10e}"
            )
            lines.append(
                f"closed form {self.closed_form:.10e}: the finest mesh is {residuals[-1]:.3e} from it, "
                f"the extrapolant {rel:.3e}"
            )
        return "\n".join(lines)


def _ratio_text(values) -> str:
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    parts = []
    for index in range(len(diffs) - 1):
        if diffs[index + 1] == 0.0:
            parts.append("inf")
        else:
            parts.append(f"{abs(diffs[index] / diffs[index + 1]):.4f}")
    return ", ".join(parts) or "-"


def extrapolate(solves, *, abs_floor: float = compare.ABS_FLOOR) -> DisplacementTable:
    """One :class:`DisplacementTable` from a refinement sequence, Richardson per component.

    ``solves`` is a list of :class:`plate_sestra_runner.PlateSolve`, coarse to fine. Each
    component of each probe is extrapolated independently, with the order read off its own three
    values -- not borrowed from the deflection's -- because a component converging at a different
    rate is exactly what should be visible rather than smoothed over.

    A component whose magnitude never exceeds ``abs_floor`` is carried through at its finest value
    instead of extrapolated. That is not a convenience: three components of this model are zero by
    construction (``u1`` and ``u2`` at the midsurface, ``ur3`` for a flat plate) and their
    sequences are float noise at 1e-20 to 1e-34, from which :func:`plate_hand_check.observed_order`
    would compute a meaningless rate and :func:`plate_hand_check.richardson` would then amplify
    it. They are still *compared*, by :func:`compare.compare`'s absolute rule.

    Raises :class:`NotASequence` when the list is not one solver's one variant coarse to fine, and
    :class:`plate_hand_check.NotConverging` when a **significant** component does not form a
    converging sequence -- naming the probe and the component, because "something did not
    converge" over 42 components is not a report. Both are loud failures on purpose: the second is
    what a run whose mesh did not actually change, or whose supports moved between densities,
    looks like.
    """
    _assert_one_sequence(solves)
    finest = solves[-1]
    tables = [solve.table for solve in solves]

    displacements: dict[str, tuple[float, float, float, float, float, float]] = {}
    for probe in sorted(finest.table.displacements):
        row = []
        for component in COMPONENTS:
            values = [table.component(probe, component) for table in tables]
            if max(abs(v) for v in values) <= abs_floor:
                row.append(values[-1])
                continue
            try:
                row.append(plate_hand_check.richardson(values).extrapolated)
            except plate_hand_check.NotConverging as exc:
                raise plate_hand_check.NotConverging(
                    f"{finest.table.solver}: {probe}.{component} does not converge over "
                    f"{tuple(solve.mesh_size for solve in solves)}: {exc}"
                ) from exc
        displacements[probe] = tuple(row)  # type: ignore[arg-type]

    return DisplacementTable(
        solver=finest.table.solver,
        solver_version=finest.table.solver_version,
        model=finest.table.model,
        load_case=finest.table.load_case,
        displacements=displacements,
        node_ids=dict(finest.table.node_ids),
        node_coords=dict(finest.table.node_coords),
        source="Richardson extrapolation of {0} at seeds {1}".format(
            finest.source or finest.table.source, ", ".join(str(solve.mesh_size) for solve in solves)
        ),
    )


def convergence_report(solves, *, closed_form: float | None = None) -> ConvergenceReport:
    """Every component's sequence for one solver and one variant, plus the printed table.

    ``closed_form`` defaults to the right one for the variant -- the bare or the stiffened
    deflection -- so a report cannot end up printing a residual against the other variant's.
    """
    _assert_one_sequence(solves)
    finest = solves[-1]
    if closed_form is None:
        closed_form = (
            plate_hand_check.stiffened_deflection() if finest.stiffened else plate_hand_check.bare_deflection()
        )
    report = ConvergenceReport(
        solver=finest.table.solver,
        solver_version=finest.table.solver_version,
        variant="stiffened" if finest.stiffened else "bare",
        mesh_sizes=tuple(solve.mesh_size for solve in solves),
        node_counts=tuple(solve.node_count for solve in solves),
        closed_form=closed_form,
    )
    tables = [solve.table for solve in solves]
    for probe in sorted(finest.table.displacements):
        for component in COMPONENTS:
            values = tuple(table.component(probe, component) for table in tables)
            if max(abs(v) for v in values) <= compare.ABS_FLOOR:
                report.components.append(ComponentConvergence(probe, component, values, None, values[-1]))
                continue
            conv = plate_hand_check.richardson(values)
            report.components.append(ComponentConvergence(probe, component, values, conv, conv.extrapolated))
    return report


def assert_cylindrical(table: DisplacementTable, *, rel_tol: float = CYLINDRICAL_REL_TOL) -> float:
    """Raise :class:`NotCylindrical` unless the three mid-span probes carry the same ``u3``.

    Returns the measured spread, relative, so a caller can report it on a pass -- which is worth
    doing: the bare strip's is 0.0 and the stiffened strip's is 8.1e-05, and that difference is
    itself the physics of the case.

    This is the check that the ``u2 = 0, ur1 = 0`` constraint on both long edges survived
    translation. Without it the strip curves anticlastically and ``D`` is the wrong stiffness by
    up to 9% -- and nothing else in the comparison would notice, because both solvers would be
    given the same wrong boundary and would agree with each other beautifully. That is the same
    class of failure :func:`compare.assert_has_signal` exists for: agreement is not correctness.
    """
    values = []
    for probe in plate_model.CYLINDRICAL_PROBES:
        if probe not in table.displacements:
            raise NotCylindrical(
                f"{table.solver} has no probe {probe!r}, so whether the strip is in cylindrical "
                f"bending cannot be established; it carries {sorted(table.displacements)}."
            )
        values.append(table.component(probe, "u3"))
    peak = max(abs(v) for v in values)
    if peak <= compare.ABS_FLOOR:
        raise NotCylindrical(
            f"{table.solver}: all three mid-span probes are at or below the absolute floor "
            f"({peak:.3e}), so there is no deflection whose uniformity could be checked. An "
            f"unloaded plate is uniform across its width too."
        )
    spread = (max(values) - min(values)) / peak
    if spread > rel_tol:
        pairs = ", ".join(f"{probe}={table.component(probe, 'u3'):.9e}" for probe in plate_model.CYLINDRICAL_PROBES)
        raise NotCylindrical(
            f"{table.solver}: mid-span deflection varies by {spread:.3e} across the strip's width "
            f"({pairs}), above {rel_tol:.1e}. The strip is then not in cylindrical bending, and "
            f"D = E t^3 / (12 (1 - nu^2)) is the wrong stiffness for it -- by up to 9%. Check that "
            f"the u2 = 0, ur1 = 0 constraint on both long edges reached the deck; both solvers "
            f"given the same wrong boundary would still agree with each other."
        )
    return spread


def assert_stiffener_present(
    bare: DisplacementTable,
    stiffened: DisplacementTable,
    *,
    rel_tol: float = plate_hand_check.RATIO_REL_TOL,
) -> float:
    """Raise :class:`StiffenerMissing` unless the two tables' stiffness ratio is the predicted one.

    Returns the measured ratio. The prediction is
    :func:`plate_hand_check.stiffness_ratio` = ``EI_plate / (EI_plate + EI_bar)`` = 0.376156,
    derived from two second moments and a modulus with nothing from either solver in it. Measured
    on the extrapolated tables: 0.376220 (Sestra) and 0.376209 (Abaqus), within 1.72e-04 and
    1.42e-04 -- and the strip is 2.658x stiffer with the bar than without it.

    This is the plate case's answer to the portal frame's "0 vs 0, agree": the way a stiffened
    shell model goes wrong is that the stiffener is *there and carrying nothing*, which no table of
    displacements looks odd about. The three ways it happens all land near 1.000 -- a bar with
    coincident unmerged nodes (which ``mergeType=SEPARATE`` produces in CAE, measured), a bar
    built as an ordinary edge rather than a ``Stringer`` (no beam elements at all, measured), and
    the bare model solved twice -- and a bar whose section was rotated 90 degrees lands at 0.924,
    because its ``I`` falls from 7.59e-08 to 3.75e-09 and ``EI_bar`` with it, from 15947 to 788 N m2.
    All four are more than 1.4x from 0.376, against a tolerance of 1e-03.
    """
    probe = plate_model.DEFLECTION_PROBE
    bare_value = bare.component(probe, "u3")
    stiff_value = stiffened.component(probe, "u3")
    if abs(bare_value) <= compare.ABS_FLOOR:
        raise StiffenerMissing(
            f"the bare table's {probe}.u3 is {bare_value:.3e}, at or below the absolute floor, so "
            f"there is no ratio to take. An unloaded bare strip would make any stiffened result "
            f"look infinitely stiff."
        )
    ratio = stiff_value / bare_value
    expected = plate_hand_check.stiffness_ratio()
    rel = abs(ratio / expected - 1.0)
    if rel > rel_tol:
        raise StiffenerMissing(
            f"the stiffened strip deflects {ratio:.6f} of the bare one ({stiff_value:.9e} against "
            f"{bare_value:.9e}), and the parallel-spring closed form says "
            f"{expected:.6f} -- {rel:.3e} away, above {rel_tol:.1e}. EI_plate is "
            f"{plate_hand_check.plate_ei():.6f} and EI_bar {plate_hand_check.bar_ei():.6f} N m2, so "
            f"a ratio near 1.0 means the bar is carrying nothing: unmerged nodes, an ordinary edge "
            f"instead of a Stringer, or the bare model solved twice. A ratio near 0.92 means its "
            f"section is rotated 90 degrees (I falls from 7.59e-08 to 3.75e-09)."
        )
    return ratio


def assert_reaction_total(solve, *, rel_tol: float = REACTION_REL_TOL) -> float:
    """Raise :class:`ReactionMismatch` unless a solve's reaction total is ``-q L b``.

    Returns the relative residual. The one scalar that says the *whole* load arrived, and the only
    one that comes from the solver's own bookkeeping rather than from adapy's -- comparing adapy's
    applied load against adapy's own arithmetic would prove nothing. It matters here more than it
    did for the frame because the two sides are given the load in different forms: a pressure on
    the Abaqus side, the exact consistent nodal vector on the Sestra one (adapy's Sesam writer has
    no distributed-load record at all). If those two were not the same load, this is where it would
    show, on both sides at once.
    """
    expected = plate_model.expected_load_total()
    measured = solve.reaction_total[2]
    # The support reaction opposes the applied load, so it is +q L b against a negative applied
    # total. Compared by magnitude and sign together, not by magnitude alone: a reaction with the
    # same sign as the load would mean the supports are pushing the plate down.
    rel = abs(measured + expected) / abs(expected)
    if rel > rel_tol:
        raise ReactionMismatch(
            f"{solve.table.solver} ({solve.label}) reacted {solve.reaction_total!r} N against an "
            f"applied {expected!r} N in z, {rel:.3e} away and above {rel_tol:.1e}. A load short by "
            f"one node's tributary area moves the mid-span deflection by far less than the "
            f"discretisation residual and would pass every displacement check here."
        )
    return rel


def _assert_one_sequence(solves) -> None:
    """Raise unless ``solves`` is one solver's one variant at strictly decreasing seeds.

    Checked because the whole extrapolation is arithmetic on a list, and a list assembled in the
    wrong order, or with a stiffened solve among the bare ones, would extrapolate happily and
    produce a number to compare.
    """
    if len(solves) < 3:
        raise NotASequence(
            f"an extrapolation needs three meshes; got {len(solves)}. plate_model.MESH_SIZES has "
            f"three, a factor of two apart, for exactly this reason -- two can only confirm a rate "
            f"that was assumed."
        )
    solvers = sorted({solve.table.solver for solve in solves})
    variants = sorted({bool(solve.stiffened) for solve in solves})
    models = sorted({solve.table.model for solve in solves})
    if len(solvers) != 1 or len(variants) != 1 or len(models) != 1:
        raise NotASequence(
            f"a refinement sequence must be one solver and one variant: got solvers {solvers}, "
            f"stiffened {variants}, models {models}. Mixing them would extrapolate across two "
            f"different structures and produce a number that is neither."
        )
    sizes = [solve.mesh_size for solve in solves]
    if any(sizes[i + 1] >= sizes[i] for i in range(len(sizes) - 1)):
        raise NotASequence(
            f"the sequence {tuple(sizes)} is not strictly coarse-to-fine. The extrapolation takes "
            f"the last entry as the finest and the refinement ratio as "
            f"{plate_hand_check.REFINEMENT}, so an out-of-order list extrapolates away from the "
            f"limit rather than towards it."
        )


def format_closed_forms() -> str:
    """The closed forms and the section they came from, printed once at the top of a report."""
    predictions = plate_hand_check.predictions()
    return "\n".join(
        [
            f"strip: {plate_model.STRIP_LENGTH} x {plate_model.STRIP_WIDTH} m, "
            f"t = {plate_model.PLATE_THICKNESS} m, {plate_model.MATERIAL_NAME}, "
            f"q = {plate_model.PRESSURE} Pa",
            "supports: " + "; ".join(f"{name} {why}" for name, _dofs, why in plate_model.EDGE_SUPPORTS),
            f"stiffener: {plate_model.BAR_WIDTH} x {plate_model.BAR_HEIGHT} m flat bar on the centreline, "
            f"centroid on the plate's mid-surface",
            f"D = E t^3 / (12 (1 - nu^2))       = {predictions['D']:.9f} N m",
            f"5 q L^4 / (384 D)                 = {predictions['bare_deflection']:.12f} m   (bare)",
            f"q L^3 / (24 D)                    = {predictions['support_rotation']:.12f} rad (support rotation)",
            f"EI_plate = D b                    = {predictions['EI_plate']:.6f} N m2",
            f"EI_bar   = E I_bar                = {predictions['EI_bar']:.6f} N m2",
            f"5 (q b) L^4 / (384 sum EI)        = {predictions['stiffened_deflection']:.12f} m   (stiffened)",
            f"EI_plate / sum EI                 = {predictions['stiffness_ratio']:.12f}     (stiffness ratio)",
        ]
    )
