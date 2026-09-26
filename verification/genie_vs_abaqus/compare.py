"""Compare two solvers' displacement tables. Tolerances, and what the result means.

What this comparison can prove
==============================

That **one adapy concept model translates consistently into two independent solvers**. Both
decks are derived from the same :func:`model.build_portal_frame` object, so if Sestra and
Abaqus land on the same displacements then the geometry, the section properties, the
material, the supports and the loads all survived both translations with the same meaning.
That is a genuinely useful thing to know and it is not knowable any other way: a writer can
produce a deck that a solver accepts and solves happily while quietly halving a load or
rotating a section.

Combined with :mod:`hand_check`, it also proves the answer is *right* and not merely
agreed-upon. Those are separate claims and this package keeps them separate: the closed-form
check is what catches an error both writers make identically.

What it cannot prove
====================

**That the two solvers agree exactly. They will not, and a comparison built on expecting
that is a comparison that will be silenced.** The two use different beam elements:

* Sestra's ``BEAS`` is a two-node beam whose stiffness matrix is the *exact* solution for a
  prismatic shear-flexible (Timoshenko) beam. It has no discretisation error on a
  piecewise-linear moment field, which is why it matched the closed form to 3.6e-4 on this
  frame with only six elements per member.
* Abaqus's ``B31`` is a two-node *linearly interpolated* shear-flexible beam. It is not
  exact in bending; its error falls as ``1/n^2`` in the elements per member. ``B33`` is a
  cubic Euler-Bernoulli beam, which is exact in bending but drops transverse shear
  altogether and will therefore sit on the *other* side of the Sestra answer.

So the element choice on the Abaqus side moves the expected agreement, and the direction of
the residual is informative rather than noise.

**That the meshes coincide.** They do not. See :mod:`displacements` -- correspondence is
established by position at points both meshers are forced to seed, never by node id.

**Anything about a load case this package does not model.** Fixed supports and nodal point
forces only; see the gap list in :mod:`sestra_runner` for what adapy cannot write into a
Sesam deck (prescribed displacements, distributed element loads).

**Anything about stresses or section forces.** Only displacements are compared. Displacement
is the integral of the whole model's behaviour and so the most sensitive single scalar to a
translation defect, but two solvers can agree on displacement and disagree on how they
recover a stress from it.

Expected order of agreement, and the tolerance
==============================================

:data:`REL_TOL` is **1e-2 (1%)**, and it is a budget rather than a number tuned until a test
passed -- it was fixed, with this justification, before the Abaqus half existed, and the
error sources were sized from the closed form:

==============================================================  =======  ===============
source                                                          budget   basis
==============================================================  =======  ===============
``B31`` linear-interpolation discretisation, 6-8 elem/member     0.5%    falls as 1/n^2
transverse shear stiffness definition (tube shear area)          0.2%    shear is 0.63%
                                                                         of the sway, so
                                                                         even a 30%
                                                                         disagreement in
                                                                         shear area moves
                                                                         the answer 0.19%
section property arithmetic (adapy's integrated ``I`` vs         0.01%   measured 4e-5
Abaqus's closed-form ``PIPE``)                                           relative
==============================================================  =======  ===============

That sums to about 0.7%; 1% leaves margin without being useless. The test of a tolerance is
whether it still catches the failures it exists for, and every translation defect that
matters is an order of magnitude larger: a wrong second moment of area, a dropped or halved
load, a pinned instead of fixed base, a section rotated 90 degrees on a non-symmetric
profile, a missing joint merge. None of those lands inside 1%.

If the Abaqus half uses ``B33`` instead of ``B31``, expect it to come in ~0.6% *below*
Sestra (no shear flexibility) and to still pass -- and read the per-point table, because the
sign of the residual being the same at every probe is the signature of a formulation
difference, whereas a translation defect is usually localised.

:data:`ABS_FLOOR` is **1e-6 m**, below which a component is compared absolutely rather than
relatively. Three components of this model are zero by symmetry (all out-of-plane motion)
and a relative comparison of two zeros is meaningless. 1 um is 4e-5 of the 24.7 mm signal
and about 340x the single-precision storage resolution of a Sesam ``.SIN`` (float32 eps on
a 24.7 mm value is ~2.9e-9 m), so it is comfortably above numerical noise and comfortably
below anything physical.

The failure mode this module is built against
=============================================

A comparator that reports "0 vs 0, agree". Three rules prevent it, and all three raise
rather than warn:

1. :func:`assert_same_probes` -- the two tables must name the *same* probe set. A point
   present on one side and absent on the other is a fatal error, not a point to skip.
2. :func:`assert_has_signal` -- each table must contain at least one component above
   :data:`SIGNAL_FLOOR`. An all-zero table means the loads never reached the solver, and it
   would otherwise agree perfectly with another all-zero table.
3. An empty table, or one with fewer than :data:`MIN_PROBES` points, is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .displacements import COMPONENTS, DisplacementTable

#: Relative tolerance on a displacement component whose magnitude exceeds
#: :data:`ABS_FLOOR`. See the module docstring for the budget this comes from.
REL_TOL = 1.0e-2

#: Absolute floor, metres (and radians, for the rotation components). Below this a
#: component is treated as zero and checked absolutely, not relatively.
ABS_FLOOR = 1.0e-6

#: A table must carry at least one component above this to count as a result at all.
#: 1e-4 m -- 250x below the expected 24.7 mm sway, so it cannot be tripped by a genuinely
#: small but real response, and 100x above :data:`ABS_FLOOR`.
SIGNAL_FLOOR = 1.0e-4

#: Fewer probes than this and the comparison is not worth reporting.
MIN_PROBES = 3


class ProbeSetMismatch(AssertionError):
    """The two tables do not name the same probe points."""


class NoSignal(AssertionError):
    """A table's displacements are all at or below :data:`SIGNAL_FLOOR`.

    Almost always means the loads did not reach the solver. Fatal, because an all-zero
    table compares perfectly against another all-zero table.
    """


@dataclass(frozen=True)
class ComponentDiff:
    """One component at one probe, on both sides."""

    probe: str
    component: str
    value_a: float
    value_b: float
    #: Whether both sides are at or below :data:`ABS_FLOOR`, i.e. zero by symmetry.
    negligible: bool
    #: ``|a - b|``, metres or radians.
    abs_diff: float
    #: ``|a - b| / max(|a|, |b|)``, or ``0.0`` when :attr:`negligible`.
    rel_diff: float
    ok: bool

    @property
    def verdict(self) -> str:
        if self.negligible:
            return "zero" if self.ok else "FAIL(zero)"
        return "ok" if self.ok else "FAIL"


@dataclass
class ComparisonReport:
    """Every component of every probe, plus the pass/fail roll-up."""

    label_a: str
    label_b: str
    rel_tol: float
    abs_floor: float
    diffs: list[ComponentDiff] = field(default_factory=list)

    @property
    def failures(self) -> list[ComponentDiff]:
        return [d for d in self.diffs if not d.ok]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def worst(self) -> ComponentDiff | None:
        """The significant component with the largest relative difference."""
        significant = [d for d in self.diffs if not d.negligible]
        if not significant:
            return None
        return max(significant, key=lambda d: d.rel_diff)

    def format_table(self) -> str:
        head = (
            f"{'probe':<13} {'comp':<5} {self.label_a:>15} {self.label_b:>15} "
            f"{'abs diff':>12} {'rel diff':>10}  verdict"
        )
        lines = [head, "-" * len(head)]
        for d in self.diffs:
            rel = "-" if d.negligible else f"{d.rel_diff:.3e}"
            lines.append(
                f"{d.probe:<13} {d.component:<5} {d.value_a:>15.6e} {d.value_b:>15.6e} "
                f"{d.abs_diff:>12.3e} {rel:>10}  {d.verdict}"
            )
        worst = self.worst
        lines.append("-" * len(head))
        lines.append(
            f"tolerance: rel {self.rel_tol:.1e} above an absolute floor of {self.abs_floor:.1e}; "
            f"{len(self.diffs)} components compared, {len(self.failures)} failed"
        )
        if worst is not None:
            lines.append(
                f"worst significant component: {worst.probe}.{worst.component} "
                f"rel {worst.rel_diff:.3e} ({worst.value_a:.6e} vs {worst.value_b:.6e})"
            )
        return "\n".join(lines)


def assert_same_probes(table_a: DisplacementTable, table_b: DisplacementTable) -> None:
    """Raise unless both tables name exactly the same probes.

    This is rule 1 against the "0 vs 0, agree" failure: comparing the intersection of two
    probe sets is how a comparison ends up reporting agreement on the two points that
    happened to survive.
    """
    names_a, names_b = set(table_a.displacements), set(table_b.displacements)
    if names_a != names_b:
        only_a = sorted(names_a - names_b)
        only_b = sorted(names_b - names_a)
        raise ProbeSetMismatch(
            f"the two result sets do not cover the same probe points, so there is nothing "
            f"honest to compare. Only in {table_a.solver}: {only_a or '(none)'}. "
            f"Only in {table_b.solver}: {only_b or '(none)'}. Both sides must sample all of "
            f"model.PROBE_POINTS; a missing point is a defect in the runner that produced it."
        )
    if len(names_a) < MIN_PROBES:
        raise ProbeSetMismatch(
            f"only {len(names_a)} probe point(s) in both tables ({sorted(names_a)}); " f"MIN_PROBES is {MIN_PROBES}."
        )


def assert_has_signal(table: DisplacementTable, *, signal_floor: float = SIGNAL_FLOOR) -> None:
    """Raise unless ``table`` contains at least one component above ``signal_floor``.

    Rule 2. An all-zero table is the single most likely way for this comparison to pass
    while proving nothing: it is what a solver produces when the load block never made it
    into the deck, and two of them agree to machine precision.
    """
    peak = 0.0
    for values in table.displacements.values():
        for value in values:
            peak = max(peak, abs(value))
    if peak <= signal_floor:
        raise NoSignal(
            f"{table.solver} ({table.source or 'no source recorded'}) reports a peak "
            f"displacement of {peak:.3e} across every probe and component, at or below the "
            f"signal floor of {signal_floor:.1e}. That is an unloaded model, not a result. "
            f"Check that the load block reached the deck before comparing anything."
        )


def compare(
    table_a: DisplacementTable,
    table_b: DisplacementTable,
    *,
    rel_tol: float = REL_TOL,
    abs_floor: float = ABS_FLOOR,
    components: tuple[str, ...] = COMPONENTS,
    require_signal: bool = True,
) -> ComparisonReport:
    """Per-point, per-component comparison of two solvers' displacements.

    Raises :class:`ProbeSetMismatch` if the probe sets differ and :class:`NoSignal` if either
    side is all zeros. Neither is a soft failure: the report this returns is only meaningful
    once both hold.

    Comparison rule per component, with ``m = max(|a|, |b|)``:

    * ``m <= abs_floor``: both are zero to within the floor. Passes, marked ``negligible``,
      and no relative difference is computed -- there is nothing to divide by.
    * otherwise: ``|a - b| / m <= rel_tol``.

    Dividing by the larger magnitude rather than by one side keeps the measure symmetric, so
    swapping the arguments cannot change the verdict.
    """
    assert_same_probes(table_a, table_b)
    if require_signal:
        assert_has_signal(table_a)
        assert_has_signal(table_b)

    label_a = f"{table_a.solver} {table_a.solver_version}".strip()
    label_b = f"{table_b.solver} {table_b.solver_version}".strip()
    report = ComparisonReport(label_a=label_a, label_b=label_b, rel_tol=rel_tol, abs_floor=abs_floor)

    for probe in sorted(table_a.displacements):
        for comp in components:
            a = table_a.component(probe, comp)
            b = table_b.component(probe, comp)
            abs_diff = abs(a - b)
            peak = max(abs(a), abs(b))
            negligible = peak <= abs_floor
            if negligible:
                rel_diff = 0.0
                ok = abs_diff <= abs_floor
            else:
                rel_diff = abs_diff / peak
                ok = rel_diff <= rel_tol
            report.diffs.append(
                ComponentDiff(
                    probe=probe,
                    component=comp,
                    value_a=a,
                    value_b=b,
                    negligible=negligible,
                    abs_diff=abs_diff,
                    rel_diff=rel_diff,
                    ok=ok,
                )
            )
    return report


@dataclass(frozen=True)
class HandCheckRow:
    """One solver's sway beside one closed-form prediction."""

    solver: str
    formulation: str
    solver_value: float
    closed_form: float
    rel_diff: float
    tol: float
    #: Whether the solver matches *this* formulation within ``tol``. Not the overall
    #: verdict -- see :attr:`HandCheckReport.ok`, which uses the bracket.
    matches: bool


@dataclass
class HandCheckReport:
    """A solver's sway against the admissible band spanned by both closed forms.

    The verdict is the bracket, not any single formulation: see
    :func:`hand_check.admissible_bracket` and the reasoning in :mod:`hand_check`'s
    docstring. :attr:`nearest` names the beam theory the solver actually behaves like, which
    is diagnostic information worth having even on a pass.
    """

    solver: str
    probe: str
    solver_value: float
    rows: list[HandCheckRow]
    bracket: tuple[float, float]
    rel_tol: float
    #: The second moment both closed forms were evaluated with -- the one *this solver*
    #: integrates, which is not always the one the model holds. Reported so a passing check
    #: cannot hide which section it was a check against.
    inertia: float = 0.0
    #: Where that number came from, for the printed report.
    inertia_note: str = ""

    @property
    def ok(self) -> bool:
        low, high = self.bracket
        return low <= self.solver_value <= high

    @property
    def nearest(self) -> HandCheckRow:
        return min(self.rows, key=lambda r: r.rel_diff)

    @property
    def matched(self) -> list[str]:
        """Formulations this solver matches within :attr:`rel_tol`."""
        return [r.formulation for r in self.rows if r.matches]


def hand_check(
    table: DisplacementTable,
    *,
    probe: str | None = None,
    inertia: float | None = None,
    inertia_note: str = "",
) -> HandCheckReport:
    """Check a table's sway against the closed forms in :mod:`hand_check`.

    Passes when the measured sway lies inside the admissible bracket -- between the
    Euler-Bernoulli and Timoshenko values, widened by
    :data:`hand_check.HAND_CHECK_REL_TOL`. A shear-flexible element (Sestra ``BEAS``, Abaqus
    ``B31``) will sit at the Timoshenko end, a shear-rigid one (``B33``) at the
    Euler-Bernoulli end, and both are correct; the report says which.

    ``inertia`` is the second moment *this solver* integrates. Pass it whenever the solver
    idealises the section -- Abaqus' ``section=PIPE`` treats the wall as a line, 0.276% low on a
    tube, which is larger than the bracket's slack and would fail a correct translation. ``None``
    means the model's own exact value. The bracket still spans only the two beam theories either
    way, so this cannot be used to absorb a real error: a 2% sway is outside it whichever
    ``inertia`` is given. See :mod:`hand_check`'s docstring for the two-checks-two-questions
    split, and :func:`model.abaqus_pipe_inertia` for the value to pass here.
    """
    from . import hand_check as hc
    from . import model

    probe = probe or model.SWAY_PROBE
    if probe not in table.displacements:
        raise ProbeSetMismatch(
            f"{table.solver} has no probe '{probe}' to hand-check; it carries " f"{sorted(table.displacements)}."
        )
    measured = table.component(probe, "u1")
    predictions = hc.portal_frame_predictions(inertia=inertia)

    rows = []
    for name, prediction in predictions.items():
        rel = abs(measured - prediction.delta) / abs(prediction.delta)
        rows.append(
            HandCheckRow(
                solver=table.solver,
                formulation=name,
                solver_value=measured,
                closed_form=prediction.delta,
                rel_diff=rel,
                tol=hc.HAND_CHECK_REL_TOL,
                matches=rel <= hc.HAND_CHECK_REL_TOL,
            )
        )
    return HandCheckReport(
        solver=table.solver,
        probe=probe,
        solver_value=measured,
        rows=rows,
        bracket=hc.admissible_bracket(predictions),
        rel_tol=hc.HAND_CHECK_REL_TOL,
        inertia=model.section_properties()["Iy"] if inertia is None else inertia,
        inertia_note=inertia_note or ("the model's own exact section" if inertia is None else ""),
    )


def format_hand_check(report: HandCheckReport) -> str:
    head = (
        f"{'solver':<10} {'closed form':<17} {'solver [m]':>14} {'closed [m]':>14} " f"{'rel':>10} {'tol':>9}  matches"
    )
    lines = [
        f"hand check at probe {report.probe}, global X translation",
        head,
        "-" * len(head),
    ]
    for row in report.rows:
        lines.append(
            f"{row.solver:<10} {row.formulation:<17} {row.solver_value:>14.6e} "
            f"{row.closed_form:>14.6e} {row.rel_diff:>10.3e} {row.tol:>9.1e}  "
            f"{'yes' if row.matches else 'no'}"
        )
    low, high = report.bracket
    nearest = report.nearest
    lines.append("-" * len(head))
    lines.append(
        f"closed forms evaluated with I = {report.inertia:.6e} m4"
        + (f" ({report.inertia_note})" if report.inertia_note else "")
    )
    lines.append(
        f"admissible bracket [{low:.6e}, {high:.6e}] (both closed forms +/- {report.rel_tol:.1e}): "
        f"{'PASS' if report.ok else 'FAIL'}"
    )
    lines.append(
        f"nearest formulation: {nearest.formulation} at rel {nearest.rel_diff:.3e}"
        + (f" -- behaves like a {_beam_family(nearest.formulation)} beam" if nearest.matches else "")
    )
    return "\n".join(lines)


def _beam_family(formulation: str) -> str:
    return "shear-flexible (Timoshenko)" if formulation == "timoshenko" else "shear-rigid (Euler-Bernoulli)"
