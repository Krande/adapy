"""Self-test of the comparison framework. No solver, no licence, runs anywhere.

A comparator that reports "0 vs 0, agree" is the failure this package is built against, and
a guard that has never been seen to fire is not a guard. So these checks run the comparator
against deliberately broken inputs and assert that it *raises*:

* a probe present on one side and absent on the other;
* an all-zero table on either side;
* a table with too few probes;
* a probe point the mesh does not seed;
* two coincident nodes at a probe.

Plus the positive cases: a table that agrees passes, and one perturbed just past
:data:`compare.REL_TOL` fails at the right components.

Run it after any change to :mod:`compare` or :mod:`displacements`::

    python -m verification.genie_vs_abaqus.selftest
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

import numpy as np

from . import compare, model
from .displacements import (
    AmbiguousProbe,
    DisplacementTable,
    ProbeNotFound,
    sample_fea_result,
)

#: A plausible Sestra-shaped table: the measured portal frame answer, rounded.
_REFERENCE = {
    "BASE_L": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "BASE_R": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "COL_L_MID": (1.016948e-02, 0.0, 7.335531e-06, 0.0, 5.415976e-03, 0.0),
    "COL_R_MID": (1.016948e-02, 0.0, -7.335531e-06, 0.0, 5.415976e-03, 0.0),
    "TOP_L": (2.468646e-02, 0.0, 1.467106e-05, 0.0, 2.898336e-03, 0.0),
    "TOP_R": (2.468646e-02, 0.0, -1.467106e-05, 0.0, 2.898336e-03, 0.0),
    "GIRDER_QTR": (2.468646e-02, 0.0, -2.154147e-03, 0.0, -3.438878e-04, 0.0),
    "GIRDER_MID": (2.468646e-02, 0.0, 0.0, 0.0, -1.424629e-03, 0.0),
    "GIRDER_3QTR": (2.468646e-02, 0.0, 2.154147e-03, 0.0, -3.438878e-04, 0.0),
}


def _table(solver: str, displacements: dict) -> DisplacementTable:
    return DisplacementTable(
        solver=solver,
        solver_version="selftest",
        model="portal_frame",
        load_case=model.LOAD_CASE,
        displacements={k: tuple(v) for k, v in displacements.items()},
        node_ids={k: i for i, k in enumerate(displacements, start=1)},
        source=f"selftest::{solver}",
    )


def _scaled(factor: float) -> dict:
    return {k: tuple(v * factor for v in values) for k, values in _REFERENCE.items()}


@dataclass
class _FakeNodes:
    identifiers: np.ndarray
    coords: np.ndarray


@dataclass
class _FakeMesh:
    nodes: _FakeNodes


class _FakeField:
    name = "RVNODDIS"
    step = 1
    components = ("U1", "U2", "U3", "U4", "U5", "U6")

    def __init__(self, values):
        self.values = np.asarray(values, dtype=float)


class _FakeResult:
    """Minimum duck-type that :func:`displacements.sample_fea_result` consumes."""

    results_file_path = "selftest://fake"

    def __init__(self, ids, coords, values):
        self.mesh = _FakeMesh(_FakeNodes(np.asarray(ids), np.asarray(coords, dtype=float)))
        self._field = _FakeField(values)

    def get_results_grouped_by_field_value(self):
        return {"RVNODDIS": [self._field]}


def _expect_raise(what: str, exc_type, fn) -> bool:
    try:
        fn()
    except exc_type as exc:
        first_line = str(exc).strip().splitlines()[0]
        print(f"  PASS  {what}: raised {type(exc).__name__}: {first_line[:110]}")
        return True
    except Exception as exc:  # noqa: BLE001 - reporting an unexpected type is the point
        print(f"  FAIL  {what}: raised {type(exc).__name__}, expected {exc_type.__name__}: {exc}")
        return False
    print(f"  FAIL  {what}: did not raise {exc_type.__name__} -- the guard is not working")
    return False


def _expect(what: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {what}{(': ' + detail) if detail else ''}")
    return condition


def check_loud_failures() -> list[bool]:
    print("loud-failure guards (each must raise):")
    ref = _table("sestra", _REFERENCE)
    results = []

    dropped = dict(_REFERENCE)
    dropped.pop("GIRDER_QTR")
    results.append(
        _expect_raise(
            "a probe missing from one side",
            compare.ProbeSetMismatch,
            lambda: compare.compare(ref, _table("abaqus", dropped)),
        )
    )

    results.append(
        _expect_raise(
            "an all-zero table on side B",
            compare.NoSignal,
            lambda: compare.compare(ref, _table("abaqus", _scaled(0.0))),
        )
    )
    results.append(
        _expect_raise(
            "an all-zero table on side A",
            compare.NoSignal,
            lambda: compare.compare(_table("sestra", _scaled(0.0)), ref),
        )
    )
    results.append(
        _expect_raise(
            "two all-zero tables (the 0-vs-0-agree failure mode)",
            compare.NoSignal,
            lambda: compare.compare(_table("sestra", _scaled(0.0)), _table("abaqus", _scaled(0.0))),
        )
    )

    two = {k: _REFERENCE[k] for k in ("TOP_L", "TOP_R")}
    results.append(
        _expect_raise(
            "fewer probes than MIN_PROBES",
            compare.ProbeSetMismatch,
            lambda: compare.compare(_table("sestra", two), _table("abaqus", two)),
        )
    )

    # Coordinate matching: a probe the mesh does not seed, and a duplicated node.
    ids = [1, 2]
    coords = [(0.0, 0.0, 0.0), (0.0, 0.0, 6.0)]
    values = [[1, 0, 0, 0, 0, 0, 0], [2, 0.0246, 0, 0, 0, 0, 0]]
    unseeded = _FakeResult(ids, coords, values)
    results.append(
        _expect_raise(
            "a probe point with no node",
            ProbeNotFound,
            lambda: sample_fea_result(
                unseeded,
                model.PROBE_POINTS,
                solver="fake",
                solver_version="selftest",
                model_name="portal_frame",
                load_case=model.LOAD_CASE,
            ),
        )
    )

    dup = _FakeResult(
        [1, 2],
        [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)],
        [[1, 0, 0, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0, 0]],
    )
    results.append(
        _expect_raise(
            "two coincident nodes at a probe (an unmerged joint)",
            AmbiguousProbe,
            lambda: sample_fea_result(
                dup,
                model.PROBE_POINTS[:1],
                solver="fake",
                solver_version="selftest",
                model_name="portal_frame",
                load_case=model.LOAD_CASE,
            ),
        )
    )
    return results


def check_agreement() -> list[bool]:
    print("\nagreement and disagreement (the comparator must discriminate):")
    ref = _table("sestra", _REFERENCE)
    results = []

    identical = compare.compare(ref, _table("abaqus", _REFERENCE))
    results.append(_expect("identical tables agree", identical.ok, f"{len(identical.diffs)} components"))

    # Just inside the tolerance.
    inside = compare.compare(ref, _table("abaqus", _scaled(1.0 + 0.5 * compare.REL_TOL)))
    results.append(
        _expect(
            f"a {50 * compare.REL_TOL:.1f}% perturbation is inside rel_tol={compare.REL_TOL:.1e}",
            inside.ok,
            f"worst rel {inside.worst.rel_diff:.3e}" if inside.worst else "",
        )
    )

    # Just outside.
    outside = compare.compare(ref, _table("abaqus", _scaled(1.0 + 2.0 * compare.REL_TOL)))
    results.append(
        _expect(
            f"a {200 * compare.REL_TOL:.1f}% perturbation fails",
            not outside.ok,
            (
                f"{len(outside.failures)} failing components, worst rel " f"{outside.worst.rel_diff:.3e}"
                if outside.worst
                else ""
            ),
        )
    )

    # A single localised defect must be caught, and must not drag the other probes down --
    # that is what makes a per-point table diagnostic rather than just a verdict.
    localised = dict(_REFERENCE)
    localised["GIRDER_QTR"] = tuple(v * 1.5 if i == 2 else v for i, v in enumerate(_REFERENCE["GIRDER_QTR"]))
    local_report = compare.compare(ref, _table("abaqus", localised))
    failing_probes = {d.probe for d in local_report.failures}
    results.append(
        _expect(
            "a single perturbed component fails at exactly that probe",
            failing_probes == {"GIRDER_QTR"} and len(local_report.failures) == 1,
            f"failing probes {sorted(failing_probes)}",
        )
    )

    # Zero-by-symmetry components must pass, not divide by zero.
    zero_diffs = [d for d in identical.diffs if d.negligible]
    results.append(
        _expect(
            "zero-by-symmetry components are marked negligible, not divided by zero",
            len(zero_diffs) > 0 and all(d.ok and d.rel_diff == 0.0 for d in zero_diffs),
            f"{len(zero_diffs)} of {len(identical.diffs)} components",
        )
    )

    # And a genuine difference hiding under the floor must still fail if it exceeds it.
    creeping = dict(_REFERENCE)
    creeping["BASE_L"] = (1.0e-3, 0.0, 0.0, 0.0, 0.0, 0.0)
    creep_report = compare.compare(ref, _table("abaqus", creeping))
    results.append(
        _expect(
            "a support that moved 1 mm on one side only is caught",
            not creep_report.ok and any(d.probe == "BASE_L" for d in creep_report.failures),
            f"{len(creep_report.failures)} failing components",
        )
    )
    return results


def check_hand_check() -> list[bool]:
    print("\nhand check (the closed form must agree with itself and reject a wrong value):")
    from . import hand_check as hc

    results = []
    predictions = hc.portal_frame_predictions()
    eb = predictions["euler-bernoulli"]
    timo = predictions["timoshenko"]
    results.append(
        _expect(
            "shear flexibility raises the sway",
            timo.delta > eb.delta,
            f"{100 * (timo.delta / eb.delta - 1):.3f}% higher",
        )
    )

    props = model.section_properties()
    # Limit check: a rigid girder reduces to two fixed-fixed columns in parallel.
    rigid = hc.sway_euler_bernoulli(
        p_total=model.P_TOTAL,
        height=model.HEIGHT,
        span=model.SPAN,
        e_mod=props["E"],
        i_column=props["Iy"],
        i_girder=props["Iy"] * 1.0e9,
    )
    expected_rigid = model.P_TOTAL * model.HEIGHT**3 / (24 * props["E"] * props["Iy"])
    results.append(
        _expect(
            "rigid girder -> P h^3 / (24 E I), two fixed-fixed columns in parallel",
            abs(rigid.delta / expected_rigid - 1) < 1e-6,
            f"{rigid.delta:.6e} vs {expected_rigid:.6e}",
        )
    )

    # Limit check: no girder -> two cantilevers each carrying P/2.
    soft = hc.sway_euler_bernoulli(
        p_total=model.P_TOTAL,
        height=model.HEIGHT,
        span=model.SPAN,
        e_mod=props["E"],
        i_column=props["Iy"],
        i_girder=props["Iy"] * 1.0e-9,
    )
    expected_soft = model.P_TOTAL * model.HEIGHT**3 / (6 * props["E"] * props["Iy"])
    results.append(
        _expect(
            "no girder -> P h^3 / (6 E I), two cantilevers",
            abs(soft.delta / expected_soft - 1) < 1e-6,
            f"{soft.delta:.6e} vs {expected_soft:.6e}",
        )
    )

    good = compare.hand_check(_table("sestra", _REFERENCE))
    results.append(
        _expect(
            "the measured Sestra sway is inside the admissible bracket",
            good.ok,
            f"nearest {good.nearest.formulation} at rel {good.nearest.rel_diff:.3e}",
        )
    )
    results.append(
        _expect(
            "and Sestra is identified as a shear-flexible beam",
            good.nearest.formulation == "timoshenko" and good.nearest.matches,
        )
    )

    # A shear-rigid element (Abaqus B33) lands at the Euler-Bernoulli end. It must PASS the
    # bracket -- policing against the Timoshenko value alone would fail a correct solver,
    # which is the trap the bracket exists to avoid.
    shear_rigid = compare.hand_check(_table("abaqus", _scaled(eb.delta / timo.delta)))
    results.append(
        _expect(
            "a shear-rigid (Euler-Bernoulli) solver passes the bracket",
            shear_rigid.ok,
            f"nearest {shear_rigid.nearest.formulation} at rel {shear_rigid.nearest.rel_diff:.3e}",
        )
    )

    # A pinned-base translation error roughly doubles the sway; the bracket must reject it.
    results.append(
        _expect(
            "a sway twice the closed form is outside the bracket",
            not compare.hand_check(_table("sestra", _scaled(2.0))).ok,
        )
    )
    # And a 2% error -- small, but larger than any beam formulation can explain.
    results.append(
        _expect(
            "a 2% sway error is outside the bracket (formulation cannot explain it)",
            not compare.hand_check(_table("sestra", _scaled(1.02))).ok,
        )
    )
    return results


def check_solver_section_idealisation() -> list[bool]:
    """The section the *solver* integrates is not always the one the model holds.

    Abaqus' ``section=PIPE`` treats the wall as a line. That is 0.276% low on this frame's tube,
    which is larger than the 0.2% of slack at each end of the admissible bracket, so a correct
    Abaqus answer fails the hand check if the closed form is fed adapy's exact annulus. These
    checks pin both halves: the corrected comparison passes, the uncorrected one does *not*, and
    the override still cannot absorb a genuine error.
    """
    print("\nsolver section idealisation (the closed form must use the solver's own section):")
    from . import hand_check as hc

    results = []
    adapy_inertia = model.section_properties()["Iy"]
    pipe_inertia = model.abaqus_pipe_inertia()

    # Measured against the kernel on a cantilever under a pure end moment; the licensed
    # test_the_abaqus_pipe_sections_second_moment_is_the_thin_walled_one is what holds it there.
    results.append(
        _expect(
            "the thin-walled formula reproduces Abaqus' measured effective I (2.693525e-05)",
            abs(pipe_inertia / 2.693525e-05 - 1) < 1e-05,
            f"{pipe_inertia:.6e}, rel {abs(pipe_inertia / 2.693525e-05 - 1):.2e}",
        )
    )
    results.append(
        _expect(
            "and it is 0.276% below the model's exact annulus",
            abs(pipe_inertia / adapy_inertia - 0.99724) < 1e-05,
            f"{pipe_inertia / adapy_inertia:.6f} of {adapy_inertia:.6e}",
        )
    )

    # The measured B32 sway. Feeding the closed form the section Abaqus integrates admits it;
    # feeding the model's own section does not. Without the override there is no way to pass
    # both this and the "2% error is rejected" check below, which is the point.
    measured_b32 = _table("abaqus", _scaled(2.474589e-02 / 2.468646e-02))
    corrected = compare.hand_check(measured_b32, inertia=pipe_inertia)
    results.append(
        _expect(
            "the measured B32 sway is inside the bracket for the section Abaqus integrates",
            corrected.ok,
            f"nearest {corrected.nearest.formulation} at rel {corrected.nearest.rel_diff:.3e}",
        )
    )
    results.append(
        _expect(
            "and it is identified as shear-flexible, at a far tighter rel than the bracket",
            corrected.nearest.formulation == "timoshenko" and corrected.nearest.rel_diff < 1e-04,
            f"rel {corrected.nearest.rel_diff:.3e}",
        )
    )
    uncorrected = compare.hand_check(measured_b32)
    results.append(
        _expect(
            "the same sway against the model's own section falls outside it -- the 0.077% miss",
            not uncorrected.ok,
            f"bracket top {uncorrected.bracket[1]:.6e} vs {uncorrected.solver_value:.6e}",
        )
    )

    # The override shifts which section is being checked against; it must not loosen the check.
    # A 2% error is beyond any beam theory and stays rejected with the pipe section in hand.
    results.append(
        _expect(
            "a 2% sway error is still rejected with the solver's own section",
            not compare.hand_check(_table("abaqus", _scaled(1.02)), inertia=pipe_inertia).ok,
        )
    )
    # The override must not be a loosening device. The bracket is in fact very slightly *narrower*
    # for the thin-walled section, and for a reason worth recording: the gap between the two beam
    # theories is set by phi = 12 E I / (G As L^2), which is proportional to I, so a smaller I
    # means shear matters slightly less and the two theories sit closer -- 0.6345% apart instead
    # of 0.6363%. Anyone "fixing" this class of miss by widening a tolerance instead would make
    # the bracket wider, and this check is what fires.
    pipe_width = corrected.bracket[1] / corrected.bracket[0]
    exact_width = uncorrected.bracket[1] / uncorrected.bracket[0]
    results.append(
        _expect(
            "the bracket is no wider for the pipe section than for the exact one",
            pipe_width <= exact_width,
            f"{pipe_width:.9f} vs {exact_width:.9f} (narrower by {exact_width - pipe_width:.2e}, "
            f"because phi scales with I)",
        )
    )
    # The report must say which section it used, or a pass hides what it was a pass against.
    results.append(
        _expect(
            "the printed report names the section the closed forms were evaluated with",
            f"{pipe_inertia:.6e}" in compare.format_hand_check(corrected),
        )
    )

    # Sestra needs no override: adapy writes its exact Iy straight into GBEAMG.
    results.append(
        _expect(
            "Sestra's default is the model's own section, unchanged",
            compare.hand_check(_table("sestra", _REFERENCE)).inertia == adapy_inertia,
        )
    )
    # Only bending differs between the two section definitions: 2 pi rm t and pi (ro^2 - ri^2) are
    # the same number algebraically, so the areas -- and with them the axial term -- are identical.
    ro, t = 0.1, 0.01
    results.append(
        _expect(
            "the thin-walled and exact sections have the same area, so only bending moves",
            abs(2 * math.pi * (ro - t / 2) * t / model.section_properties()["area"] - 1) < 1e-09,
            f"thin-wall {2 * math.pi * (ro - t / 2) * t:.9e} vs model {model.section_properties()['area']:.9e}",
        )
    )
    results.append(
        _expect(
            "and the formula is being asked about this model's actual section",
            abs(hc.thin_walled_pipe_inertia(radius=ro, thickness=t) / pipe_inertia - 1) < 1e-12,
            f"OD{2 * ro * 1000:.0f}x{t * 1000:.0f}",
        )
    )
    return results


def main() -> int:
    results = check_loud_failures() + check_agreement() + check_hand_check() + check_solver_section_idealisation()
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
