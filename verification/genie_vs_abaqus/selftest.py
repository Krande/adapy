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

The **plate** case adds four groups of its own, because a shell comparison goes wrong in ways a
beam comparison cannot (:func:`check_plate_closed_forms`,
:func:`check_plate_convergence`, :func:`check_plate_loud_failures`,
:func:`check_plate_agreement`):

* a table built from a mesh with **no shells in it** -- all the numbers, none of the physics;
* a probe on no node of the plate mesh, and two coincident nodes at one;
* the **stiffener missing from one side**, measured as a stiffness ratio far from
  ``EI_plate / (EI_plate + EI_bar)`` rather than as an element count -- including the case where
  it is present but its section is rotated 90 degrees, which is a ratio of 0.924 against 0.376;
* a strip that is **not in cylindrical bending**, which makes the closed form the wrong one by up
  to 9% while leaving every number plausible;
* a reaction total that is not the load;
* a refinement sequence that is not one: two meshes, mixed variants, out of order, or a component
  that does not converge -- each of which would otherwise be extrapolated into a number and then
  compared;
* and the plate probe set compared against the frame's, which must be a hard error rather than an
  empty intersection.

The plate groups are checked against **measured** solver output, carried here as the constants
``_SESTRA_BARE`` .. ``_ABAQUS_STIFF`` and ``_MID_DEFLECTION_SEQUENCES``, so the tolerance the
package ships is pinned against the residuals a correct translation actually leaves -- not only
against multiples of itself. That last distinction is why
:func:`check_plate_convergence` asserts that the *coarsest* mesh pair would **fail** at
:data:`plate_compare.PLATE_REL_TOL`: it is what says the extrapolation is doing the work and the
tolerance is not merely generous.

Run it after any change to :mod:`compare`, :mod:`displacements` or the plate modules::

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

    # The two checks above are written as multiples of compare.REL_TOL, which makes them
    # self-consistent and blind to the one thing they look like they are checking: the *value* of
    # the tolerance. Found by mutation -- raising REL_TOL from 1e-2 to 1e-1 leaves every check in
    # this file passing, while the real comparison would then call a 5% disagreement between two
    # solvers a match. These next three are stated in absolute terms so they cannot drift with it.
    results.append(
        _expect(
            "a flat 2% disagreement fails, whatever REL_TOL has been set to",
            not compare.compare(ref, _table("abaqus", _scaled(1.02))).ok,
        )
    )
    results.append(
        _expect(
            "and a flat 0.1% disagreement passes, so the tolerance is not merely tiny",
            compare.compare(ref, _table("abaqus", _scaled(1.001))).ok,
        )
    )
    # The band REL_TOL has to live in, from both ends and with the measurements that set them.
    # Below 4.945e-03 it rejects the correct Sestra/Abaqus pair, whose worst significant residual
    # is GIRDER_3QTR.r2 at that value -- a shear-rigid/shear-flexible difference at a point where
    # cancellation amplifies it elevenfold, not a defect. Above 2e-2 it starts admitting genuine
    # disagreement: the B31-at-1.0m element error is 7.4e-03 and must stay visible.
    results.append(
        _expect(
            "REL_TOL sits above the measured worst residual and below a defect-admitting value",
            4.945e-03 < compare.REL_TOL <= 2.0e-02,
            f"{compare.REL_TOL:.1e}",
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


# --------------------------------------------------------------------------------- the plate case
#
# Measured, on this branch, at the three seeds of plate_model.MESH_SIZES: Sestra V11.3-00 (FQUS,
# 128 / 512 / 2048 elements) against Abaqus 2025 (S4R, the same counts on the same structured grid,
# 165 / 585 / 2193 nodes both sides). The tables below are the *Richardson extrapolants* of those
# three solves, per probe, and only the two components that carry signal: u3 and the in-plane
# bending rotation ur2. The other four are zero by construction -- u1 and u2 on the mid-surface of a
# plate in pure bending, ur1 and ur3 on a flat strip in cylindrical bending -- and were measured at
# 1e-20 or smaller on the Abaqus side and exactly 0.0 on the Sestra one, both far below
# compare.ABS_FLOOR. They are carried as 0.0 here and still compared, by compare's absolute rule.

_SESTRA_BARE = {
    "X0_MID": (0.0, 0.13866667449474335),
    "QTR": (-0.12350000441074371, 0.0953333444106019),
    "MID": (-0.1733333319425583, 0.0),
    "TQTR": (-0.12350000441074371, -0.0953333444106019),
    "X1_MID": (0.0, -0.13866667449474335),
    "MID_Y0": (-0.1733333319425583, 0.0),
    "MID_YB": (-0.1733333319425583, 0.0),
}

_ABAQUS_BARE = {
    "X0_MID": (0.0, 0.13866664800897122),
    "QTR": (-0.12350223379024272, 0.09533332454092741),
    "MID": (-0.1733363138306436, 0.0),
    "TQTR": (-0.12350223379024272, -0.09533332454092741),
    "X1_MID": (0.0, -0.13866664800897122),
    "MID_Y0": (-0.1733363138306436, 0.0),
    "MID_YB": (-0.1733363138306436, 0.0),
}

_SESTRA_STIFF = {
    "X0_MID": (0.0, 0.052147124384695605),
    "QTR": (-0.046463328796517725, 0.035856595095737465),
    "MID": (-0.06521147187610148, 0.0),
    "TQTR": (-0.046463328796517725, -0.035856595095737465),
    "X1_MID": (0.0, -0.052147124384695605),
    "MID_Y0": (-0.06521675048214572, 0.0),
    "MID_YB": (-0.06521675048214572, 0.0),
}

_ABAQUS_STIFF = {
    "X0_MID": (0.0, 0.052148039418603236),
    "QTR": (-0.04646270586575523, 0.03585710876921818),
    "MID": (-0.06521064275691632, 0.0),
    "TQTR": (-0.04646270586575523, -0.03585710876921818),
    "X1_MID": (0.0, -0.052148039418603236),
    "MID_Y0": (-0.06521595234073391, 0.0),
    "MID_YB": (-0.06521595234073391, 0.0),
}

#: Mid-span ``u3`` at seeds 0.125 / 0.0625 / 0.03125, coarse to fine. The sequences the order and
#: the extrapolants are read off, carried at full precision so the arithmetic in
#: :mod:`plate_hand_check` is checked against the numbers it was budgeted from.
_MID_DEFLECTION_SEQUENCES = {
    ("sestra", False): (-0.1731979101896286, -0.17329947650432587, -0.17332486808300018),
    ("abaqus", False): (-0.17306548357009888, -0.17326860129833221, -0.17331938445568085),
    ("sestra", True): (-0.06516356766223907, -0.06520097702741623, -0.06520917266607285),
    ("abaqus", True): (-0.06511149555444717, -0.06518637388944626, -0.06520470231771469),
}

#: The measured cross-solver residuals, so a tolerance can be pinned against them absolutely.
#: ``extrapolated`` is the worst significant relative difference between the two extrapolated
#: tables, ``coarsest`` the worst at the coarsest of the three meshes. Both are ``QTR.u3`` on the
#: bare strip; on the stiffened one the extrapolated worst is ``X0_MID.r2`` at 1.7547e-05.
_MEASURED_CROSS_SOLVER = {"extrapolated": 1.8051e-05, "finest": 7.2082e-05, "coarsest": 8.3946e-04}


def _plate_table(solver: str, values: dict, *, stiffened: bool = False) -> DisplacementTable:
    """A :class:`DisplacementTable` from ``{probe: (u3, r2)}``, zeros elsewhere.

    ``model`` names the variant, as :func:`plate_sestra_runner.model_name` does, so a check that
    accidentally compared the bare table against the stiffened one would be visible in the report
    rather than only in the numbers.
    """
    from . import plate_model, plate_sestra_runner

    return DisplacementTable(
        solver=solver,
        solver_version="selftest",
        model=plate_sestra_runner.model_name(stiffened),
        load_case=plate_model.LOAD_CASE,
        displacements={name: (0.0, 0.0, u3, 0.0, r2, 0.0) for name, (u3, r2) in values.items()},
        node_ids={name: index for index, name in enumerate(sorted(values), start=1)},
        source=f"selftest::plate::{solver}",
    )


def _plate_solve(table: DisplacementTable, *, mesh_size: float, stiffened: bool, reaction: float | None = None):
    """A :class:`plate_sestra_runner.PlateSolve` around a table, with the right reaction by default."""
    from . import plate_model, plate_sestra_runner

    total = -plate_model.expected_load_total() if reaction is None else reaction
    return plate_sestra_runner.PlateSolve(
        table=table,
        mesh_size=mesh_size,
        stiffened=stiffened,
        reaction_total=(0.0, 0.0, total),
        node_count=0,
        element_counts={},
        source=table.source,
    )


def _plate_sequence(solver: str, stiffened: bool, *, sizes=None) -> list:
    """The three measured solves for one solver and one variant, coarse to fine.

    Only ``MID.u3`` varies with the mesh in this reconstruction; every other component is held at
    its extrapolated value. That is enough for what the sequence guards check -- the order, the
    extrapolation, the ordering rules -- and it keeps the constants above to the two components
    that carry signal.
    """
    from . import plate_model

    reference = {
        ("sestra", False): _SESTRA_BARE,
        ("abaqus", False): _ABAQUS_BARE,
        ("sestra", True): _SESTRA_STIFF,
        ("abaqus", True): _ABAQUS_STIFF,
    }[(solver, stiffened)]
    sequence = _MID_DEFLECTION_SEQUENCES[(solver, stiffened)]
    sizes = plate_model.MESH_SIZES if sizes is None else sizes
    solves = []
    for size, mid in zip(sizes, sequence):
        values = dict(reference)
        values["MID"] = (mid, reference["MID"][1])
        solves.append(
            _plate_solve(_plate_table(solver, values, stiffened=stiffened), mesh_size=size, stiffened=stiffened)
        )
    return solves


@dataclass
class _FakeNode:
    id: int
    p: tuple[float, float, float]
    refs: tuple = ()

    @property
    def x(self) -> float:
        return self.p[0]

    @property
    def y(self) -> float:
        return self.p[1]

    @property
    def z(self) -> float:
        return self.p[2]


@dataclass
class _FakeElem:
    id: int
    type: str
    nodes: list


class _FakeNodeStore(list):
    """``fem.nodes`` with the one method :func:`plate_model.assert_probes_are_seeded` calls."""

    def get_by_volume(self, p, tol=1e-6):
        return [n for n in self if max(abs(a - b) for a, b in zip(n.p, p)) <= tol]


@dataclass
class _FakeFem:
    nodes: _FakeNodeStore
    elements: list


def _unit_grid(nx: int, ny: int, *, kind: str = "ShellShapes.QUAD", dx: float = 1.0, dy: float = 1.0) -> _FakeFem:
    """An ``nx`` x ``ny`` grid of ``dx`` x ``dy`` quads, for the consistent-load arithmetic.

    Unit cells by default, so the tributary areas come out 0.25 at a corner, 0.5 on an edge and
    1.0 in the interior -- the three numbers ``integral(N_i) dA = A / 4`` predicts, checkable by
    eye. ``dx`` and ``dy`` exist because unit cells are *also* the one case in which an
    implementation that assumed ``mesh_size**2`` instead of measuring the element would give the
    right answer. Found by mutation: replacing the shoelace area with ``1.0`` left every check on
    a unit grid passing.
    """
    nodes = _FakeNodeStore()
    index = {}
    node_id = 1
    for i in range(nx + 1):
        for j in range(ny + 1):
            node = _FakeNode(node_id, (i * dx, j * dy, 0.0))
            index[(i, j)] = node
            nodes.append(node)
            node_id += 1
    elements = []
    element_id = 1
    for i in range(nx):
        for j in range(ny):
            corners = [index[(i, j)], index[(i + 1, j)], index[(i + 1, j + 1)], index[(i, j + 1)]]
            element = _FakeElem(element_id, kind, corners)
            for corner in corners:
                corner.refs = tuple(corner.refs) + (element,)
            elements.append(element)
            element_id += 1
    return _FakeFem(nodes, elements)


def check_plate_closed_forms() -> list[bool]:
    """The three plate closed forms, and the convergence arithmetic, against independent statements.

    Every check here is an identity or a limit, computed from the literals of the problem rather
    than from the functions being checked -- which is what makes this something other than the code
    agreeing with itself.
    """
    print("\nplate closed forms and convergence arithmetic:")
    from . import plate_hand_check as phc
    from . import plate_model as pm

    results = []
    props = pm.section_properties()
    d_by_hand = props["E"] * pm.PLATE_THICKNESS**3 / (12.0 * (1.0 - props["nu"] ** 2))
    results.append(
        _expect(
            "D = E t^3 / (12 (1 - nu^2)) is 19230.769230769 N m for this strip",
            abs(phc.plate_stiffness() / d_by_hand - 1) < 1e-15
            and abs(phc.plate_stiffness() - 19230.769230769234) < 1e-9,
            f"{phc.plate_stiffness():.9f}",
        )
    )
    results.append(
        _expect(
            "5 q L^4 / (384 D) is 0.1733333333333333 m",
            abs(phc.bare_deflection() - 0.1733333333333333) < 1e-15,
            f"{phc.bare_deflection():.15f}",
        )
    )
    # An identity between the two bare closed forms: theta / w = (q L^3 / 24 D) / (5 q L^4 / 384 D)
    # = 3.2 / L. Neither function knows about the other, so this catches a typo in either.
    results.append(
        _expect(
            "the support rotation and the deflection are in the ratio 3.2 / L, as the two forms require",
            abs(phc.support_rotation() / phc.bare_deflection() - 3.2 / pm.STRIP_LENGTH) < 1e-14,
            f"{phc.support_rotation() / phc.bare_deflection():.12f} vs {3.2 / pm.STRIP_LENGTH:.12f}",
        )
    )
    # The bar's second moment must be adapy's own, not a retyped one: that is the whole reason
    # section_properties reads it off a Section.
    by_hand = props["E"] * pm.BAR_WIDTH * pm.BAR_HEIGHT**3 / 12.0
    results.append(
        _expect(
            "EI_bar is E a b^3 / 12 from adapy's own FB section properties",
            abs(phc.bar_ei() / by_hand - 1) < 1e-12 and abs(phc.bar_ei() - 15946.874999999998) < 1e-9,
            f"{phc.bar_ei():.6f}, adapy Iy {props['bar_Iy']:.9e}",
        )
    )
    # The parallel-spring identity: w_stiff / w_bare must be exactly EI_plate / sum EI.
    results.append(
        _expect(
            "w_stiff / w_bare is exactly EI_plate / (EI_plate + EI_bar)",
            abs(phc.stiffened_deflection() / phc.bare_deflection() / phc.stiffness_ratio() - 1) < 1e-14,
            f"{phc.stiffened_deflection() / phc.bare_deflection():.12f} vs {phc.stiffness_ratio():.12f}",
        )
    )
    results.append(
        _expect(
            "and the bar makes the strip 2.658x stiffer",
            abs(1.0 / phc.stiffness_ratio() - 2.6584) < 1e-3,
            f"{1.0 / phc.stiffness_ratio():.4f}x",
        )
    )

    # An exactly second-order sequence: v(h) = L + C h^2 on h, h/2, h/4. The order must come back
    # 2.0 and the extrapolant must be L to machine precision. This is the arithmetic the whole
    # comparison rests on, checked where the answer is known rather than only on solver output.
    limit, coefficient = 0.5, 3.0
    synthetic = tuple(limit + coefficient * (1.0 / 2**k) ** 2 for k in range(3))
    conv = phc.richardson(synthetic)
    results.append(
        _expect(
            "an exact h^2 sequence reads back order 2.0 and extrapolates to its own limit",
            abs(conv.order - 2.0) < 1e-12 and abs(conv.extrapolated - limit) < 1e-14,
            f"order {conv.order:.12f}, extrapolant {conv.extrapolated:.15f} vs {limit}",
        )
    )
    cubic = tuple(limit + coefficient * (1.0 / 2**k) ** 3 for k in range(3))
    results.append(
        _expect(
            "an exact h^3 sequence reads back order 3.0, so the order is measured and not assumed",
            abs(phc.observed_order(cubic) - 3.0) < 1e-12,
            f"order {phc.observed_order(cubic):.12f}",
        )
    )
    # And the order is *used*: extrapolating the cubic sequence with the quadratic denominator
    # would leave a residual, so a richardson() that ignored the measured order would fail here.
    results.append(
        _expect(
            "the measured order is the one the extrapolation divides by",
            abs(phc.richardson(cubic).extrapolated - limit) < 1e-14,
            f"{phc.richardson(cubic).extrapolated:.15f} vs {limit}",
        )
    )
    # A first-order sequence: v = L + C h on h, h/2, h/4. Its error ratio is 2, so it is a
    # perfectly good sequence that simply is not second order -- and ORDER_BAND is what refuses
    # it. Both differences are positive and neither is zero, so no other clause can catch it.
    linear = tuple(limit + coefficient * (1.0 / 2**k) for k in range(3))
    results.append(
        _expect_raise(
            "a first-order sequence is refused rather than extrapolated as though it were h^2",
            phc.NotConverging,
            lambda: phc.richardson(linear),
        )
    )
    # And a sequence that turns around whose |d1/d2| is 4.0 -- inside ORDER_BAND, so only the
    # sign clause can reject it. Found by mutation: a turn-around at |d1/d2| = 2 is caught by the
    # band instead, and the check then passed with the sign clause deleted.
    turning = (1.0, 1.05, 1.0375)
    results.append(
        _expect_raise(
            "a sequence that turns around is refused even when its error ratio looks like h^2",
            phc.NotConverging,
            lambda: phc.richardson(turning),
        )
    )

    # The consistent nodal load, on a grid whose answer is arithmetic: unit cells, so q A / 4 gives
    # 0.25 at a corner, 0.5 on an edge, 1.0 inside.
    groups = pm.consistent_nodal_loads(_unit_grid(2, 2))
    areas = [group.area for group in groups]
    results.append(
        _expect(
            "the consistent nodal load of a uniform pressure is q A / 4 per node: 0.25 / 0.5 / 1.0",
            areas == [0.25, 0.5, 1.0],
            f"{areas}",
        )
    )
    total_area = sum(group.area * len(group.node_ids) for group in groups)
    results.append(
        _expect(
            "and those tributary areas sum to the loaded area exactly",
            abs(total_area - 4.0) < 1e-15,
            f"{total_area!r} over a 2 x 2 grid of unit cells",
        )
    )
    # On NON-unit cells, which is the case that separates measuring each element from assuming
    # mesh_size**2. 0.5 x 0.25 cells: A = 0.125, so 0.03125 / 0.0625 / 0.125 and a total of 0.5.
    rectangular = pm.consistent_nodal_loads(_unit_grid(2, 2, dx=0.5, dy=0.25))
    rect_areas = [group.area for group in rectangular]
    rect_total = sum(group.area * len(group.node_ids) for group in rectangular)
    results.append(
        _expect(
            "the element area is measured, not assumed: 0.5 x 0.25 cells give 0.03125 / 0.0625 / 0.125",
            rect_areas == [0.03125, 0.0625, 0.125] and abs(rect_total - 0.5) < 1e-15,
            f"{rect_areas}, total {rect_total!r} against a 1.0 x 0.5 m loaded area",
        )
    )
    results.append(
        _expect(
            "a force is negative, so the pressure pushes against the plate's +z normal",
            all(group.force < 0.0 for group in groups),
            f"{[round(group.force, 6) for group in groups]}",
        )
    )
    return results


def check_plate_convergence() -> list[bool]:
    """The measured sequences: their order, their extrapolants, and the tolerance they set.

    This is the group that makes :data:`plate_compare.PLATE_REL_TOL` a measurement. Everything in
    it is the output of twelve real solves, carried in the constants above.
    """
    print("\nplate mesh convergence (measured, Sestra FQUS vs Abaqus S4R at three seeds):")
    from . import plate_compare as pcmp
    from . import plate_hand_check as phc

    results = []
    extrapolants = {}
    for solver in ("sestra", "abaqus"):
        for stiffened in (False, True):
            sequence = _MID_DEFLECTION_SEQUENCES[(solver, stiffened)]
            conv = phc.richardson(sequence)
            extrapolants[(solver, stiffened)] = conv
            low, high = phc.ORDER_BAND
            results.append(
                _expect(
                    f"{solver} {'stiffened' if stiffened else 'bare'} converges at second order",
                    low <= conv.order <= high and abs(conv.order - 2.0) < 0.25,
                    f"order {conv.order:.4f}, extrapolant {conv.extrapolated:.12e}",
                )
            )
            closed = phc.stiffened_deflection() if stiffened else phc.bare_deflection()
            tol = phc.STIFFENED_REL_TOL if stiffened else phc.BARE_REL_TOL
            rel = abs(abs(conv.extrapolated) / closed - 1.0)
            results.append(
                _expect(
                    f"and its extrapolant is within {tol:.0e} of the closed form",
                    rel <= tol,
                    f"rel {rel:.3e} of {closed:.12f}",
                )
            )

    # Every sequence approaches the closed form from below -- both elements are too soft at a
    # finite mesh -- and the finest mesh is the nearest of the three. A sequence that got worse
    # with refinement would still extrapolate, so this is checked separately.
    for solver in ("sestra", "abaqus"):
        for stiffened in (False, True):
            sequence = _MID_DEFLECTION_SEQUENCES[(solver, stiffened)]
            magnitudes = [abs(v) for v in sequence]
            results.append(
                _expect(
                    f"{solver} {'stiffened' if stiffened else 'bare'}: refining the mesh softens it "
                    f"monotonically, so both elements are approaching from below",
                    magnitudes[0] < magnitudes[1] < magnitudes[2],
                    f"{[f'{v:.10f}' for v in magnitudes]}",
                )
            )

    # The bare extrapolation buys two orders of magnitude on Sestra and one on Abaqus. Asserted only
    # for the bare variant: on the stiffened one the closed form itself is 1.6e-04 out (the
    # transverse span between the bar and the long edges), so the extrapolant is legitimately
    # *further* from it than the finest mesh -- see plate_hand_check's docstring.
    for solver in ("sestra", "abaqus"):
        conv = extrapolants[(solver, False)]
        closed = phc.bare_deflection()
        finest = abs(abs(conv.values[-1]) / closed - 1.0)
        extrapolated = abs(abs(conv.extrapolated) / closed - 1.0)
        results.append(
            _expect(
                f"{solver} bare: extrapolating gets closer to the closed form than the finest mesh",
                extrapolated < finest,
                f"{extrapolated:.3e} against {finest:.3e}",
            )
        )

    # The tolerance, from both ends, in absolute terms -- so it cannot drift with itself. Below
    # 1.81e-05 it rejects the correct pair; at or above the coarsest mesh's 8.39e-04 it would admit
    # two solvers that disagree by their whole discretisation error.
    results.append(
        _expect(
            "PLATE_REL_TOL sits above the measured worst extrapolated residual and well below the "
            "coarse-mesh disagreement",
            _MEASURED_CROSS_SOLVER["extrapolated"] < pcmp.PLATE_REL_TOL < _MEASURED_CROSS_SOLVER["coarsest"],
            f"{_MEASURED_CROSS_SOLVER['extrapolated']:.3e} < {pcmp.PLATE_REL_TOL:.1e} < "
            f"{_MEASURED_CROSS_SOLVER['coarsest']:.3e}",
        )
    )
    results.append(
        _expect(
            "and it is at least 5x the worst residual a correct translation leaves",
            pcmp.PLATE_REL_TOL >= 5.0 * _MEASURED_CROSS_SOLVER["extrapolated"],
            f"{pcmp.PLATE_REL_TOL / _MEASURED_CROSS_SOLVER['extrapolated']:.1f}x",
        )
    )
    # The point of the whole design: at the coarsest mesh the two solvers do NOT agree to
    # PLATE_REL_TOL. If they did, the convergence study would be decoration.
    coarse_sestra = _plate_sequence("sestra", False)[0].table
    coarse_abaqus = _plate_sequence("abaqus", False)[0].table
    results.append(
        _expect(
            "the coarsest mesh pair FAILS at PLATE_REL_TOL -- the extrapolation is what closes it",
            not compare.compare(coarse_sestra, coarse_abaqus, rel_tol=pcmp.PLATE_REL_TOL).ok,
            f"worst rel {compare.compare(coarse_sestra, coarse_abaqus, rel_tol=pcmp.PLATE_REL_TOL).worst.rel_diff:.3e}",
        )
    )
    results.append(
        _expect(
            "and PLATE_MESH_REL_TOL admits it, which is why it is diagnostic and not the verdict",
            compare.compare(coarse_sestra, coarse_abaqus, rel_tol=pcmp.PLATE_MESH_REL_TOL).ok,
        )
    )

    # The support rotation: a second closed form, from the same three solves, that nothing was
    # tuned against.
    for solver in ("sestra", "abaqus"):
        table = _plate_table(solver, _SESTRA_BARE if solver == "sestra" else _ABAQUS_BARE)
        measured = abs(table.component("X0_MID", "r2"))
        rel = abs(measured / phc.support_rotation() - 1.0)
        results.append(
            _expect(
                f"{solver}'s extrapolated support rotation is q L^3 / (24 D) to better than 1e-06",
                rel < 1.0e-06,
                f"{measured:.12f} vs {phc.support_rotation():.12f}, rel {rel:.3e}",
            )
        )
    return results


def check_plate_agreement() -> list[bool]:
    """The two solvers' extrapolated tables against each other, and a defect that must not pass."""
    print("\nplate cross-solver agreement (the comparator must discriminate on shells too):")
    from . import plate_compare as pcmp
    from . import plate_hand_check as phc

    results = []
    for stiffened in (False, True):
        variant = "stiffened" if stiffened else "bare"
        sestra = _plate_table("sestra", _SESTRA_STIFF if stiffened else _SESTRA_BARE, stiffened=stiffened)
        abaqus = _plate_table("abaqus", _ABAQUS_STIFF if stiffened else _ABAQUS_BARE, stiffened=stiffened)
        report = compare.compare(sestra, abaqus, rel_tol=pcmp.PLATE_REL_TOL)
        results.append(
            _expect(
                f"the {variant} extrapolated tables agree at rel {pcmp.PLATE_REL_TOL:.1e}",
                report.ok,
                f"worst {report.worst.probe}.{report.worst.component} rel {report.worst.rel_diff:.3e}",
            )
        )
        # A 0.1% error at one probe: ten times the tolerance, a thousand times below anything the
        # case's headline defects do, and it must still be caught at exactly that probe.
        perturbed = dict(_SESTRA_STIFF if stiffened else _SESTRA_BARE)
        u3, r2 = perturbed["QTR"]
        perturbed["QTR"] = (u3 * 1.001, r2)
        local = compare.compare(
            sestra, _plate_table("abaqus", perturbed, stiffened=stiffened), rel_tol=pcmp.PLATE_REL_TOL
        )
        results.append(
            _expect(
                f"a 0.1% error at one {variant} probe fails at exactly that probe",
                {d.probe for d in local.failures} == {"QTR"} and len(local.failures) == 1,
                f"failing {sorted({d.probe for d in local.failures})}",
            )
        )
        results.append(
            _expect(
                f"the {variant} strip is in cylindrical bending: its width spread is within "
                f"{pcmp.CYLINDRICAL_REL_TOL:.0e}",
                pcmp.assert_cylindrical(sestra) <= pcmp.CYLINDRICAL_REL_TOL,
                f"spread {pcmp.assert_cylindrical(sestra):.3e} (Sestra), "
                f"{pcmp.assert_cylindrical(abaqus):.3e} (Abaqus)",
            )
        )

    for solver in ("sestra", "abaqus"):
        bare = _plate_table(solver, _SESTRA_BARE if solver == "sestra" else _ABAQUS_BARE)
        stiff = _plate_table(solver, _SESTRA_STIFF if solver == "sestra" else _ABAQUS_STIFF, stiffened=True)
        ratio = pcmp.assert_stiffener_present(bare, stiff)
        results.append(
            _expect(
                f"{solver}: the measured stiffness ratio is EI_plate / sum EI within " f"{phc.RATIO_REL_TOL:.0e}",
                abs(ratio / phc.stiffness_ratio() - 1.0) <= phc.RATIO_REL_TOL,
                f"{ratio:.9f} vs {phc.stiffness_ratio():.9f}, rel " f"{abs(ratio / phc.stiffness_ratio() - 1.0):.3e}",
            )
        )
    return results


def check_plate_boundary_semantics() -> list[bool]:
    """ "Simply supported" must mean the same thing in both decks. This is where it is written down.

    The Sestra deck gets :data:`plate_model.EDGE_SUPPORTS` as three ``Bc`` records, which
    ``write_bcs`` turns into ``BNBCD`` FIX codes. The Abaqus deck cannot -- the CAE writer resolves a
    support to a geometric *vertex* and a plate edge's interior nodes are not vertices
    (:func:`plate_abaqus_runner.reproduce_edge_support_refusal` reproduces the refusal) -- so the same
    three records are rendered into the appended driver through ``analysis.BC_KEYWORDS``.

    That makes the driver's text the single place the two decks could drift apart, so the expected
    ``DisplacementBC`` keywords are asserted here **literally** rather than rebuilt from
    ``EDGE_SUPPORTS``. Rebuilding them would make this check move with the very data it is checking:
    found by mutation, dropping ``ur1`` from the ``CYL`` entry -- which frees the long edges and makes
    ``D = E t^3 / (12 (1 - nu^2))`` the wrong stiffness by up to 9% -- was caught by nothing at all.

    The negative half matters as much as the positive: **no rotation is fixed on either supported
    edge**. ``ur2`` is what "simply supported" leaves free, and fixing it makes the strip 5x stiffer.
    """
    print("\nplate boundary-condition semantics (both decks must mean one thing):")
    from ada.cadit.cae.analysis import BC_KEYWORDS

    from . import plate_abaqus_runner as par
    from . import plate_model as pm

    results = []
    results.append(
        _expect(
            "BC_KEYWORDS is adapy's own dof 1..6 order, which the driver is generated through",
            tuple(BC_KEYWORDS) == ("u1", "u2", "u3", "ur1", "ur2", "ur3"),
            f"{tuple(BC_KEYWORDS)}",
        )
    )
    driver = par.support_driver()
    expected = {
        "SS_X0": "u1=0.0, u2=0.0, u3=0.0",
        "SS_X1": "u2=0.0, u3=0.0",
        "CYL": "u2=0.0, ur1=0.0",
    }
    for name, keywords in sorted(expected.items()):
        line = f"_m.DisplacementBC(name={name!r}, createStepName='Initial', region=_a.sets[{par._cae_set_name(name)!r}], {keywords})"
        results.append(
            _expect(
                f"the driver emits {name} as exactly '{keywords}'",
                line in driver,
                "found" if line in driver else f"MISSING: {line}",
            )
        )
    results.append(
        _expect(
            "no rotation about the width axis is fixed anywhere -- that is what makes it simply supported",
            "ur2=" not in driver and "ur3=" not in driver,
            "ur2 and ur3 free at both supported edges",
        )
    )
    results.append(
        _expect(
            "the three supports named in the model are the three the driver emits, and no more",
            driver.count("_m.DisplacementBC(") == len(pm.EDGE_SUPPORTS) == 3,
            f"{driver.count('_m.DisplacementBC(')} DisplacementBC calls for {len(pm.EDGE_SUPPORTS)} records",
        )
    )
    # The driver has to be syntactically valid before Abaqus is asked for a licence to find out.
    try:
        compile(driver, "plate_support_driver", "exec")
        compiles = True
        detail = f"{len(driver)} characters"
    except SyntaxError as exc:  # pragma: no cover - a regression in the template
        compiles = False
        detail = f"{type(exc).__name__}: {exc}"
    results.append(_expect("and the rendered driver compiles", compiles, detail))
    # The regions are geometry edges spanning the whole edge, not mesh nodes: that is what makes one
    # statement of the support serve all three mesh densities.
    results.append(
        _expect(
            "the supported edges are found as geometry edges by bounding box, not as mesh nodes",
            "getByBoundingBox" in driver and "instance.nodes" not in driver,
            "mesh-independent across the three densities",
        )
    )
    # And the load: the Sestra route gets the nodal vector, the Abaqus route the pressure. Neither
    # choice is free, and a route that silently changed would change what is being compared.
    results.append(
        _expect(
            "the two routes get the two load forms the two writers can carry, and nothing else",
            pm.LOAD_STYLES == {"sestra": "nodal", "abaqus": "pressure"} and pm.ROUTES == ("sestra", "abaqus"),
            f"{pm.LOAD_STYLES}",
        )
    )
    results.append(
        _expect_raise(
            "an unknown route is refused rather than defaulted",
            pm.PlateModelInvalid,
            lambda: pm.build_strip(0.125, stiffened=False, route="ansys"),
        )
    )
    return results


def check_plate_loud_failures() -> list[bool]:
    """Every plate guard, run against the input it exists for. Each must raise."""
    print("\nplate loud-failure guards (each must raise):")
    from . import plate_abaqus_runner  # noqa: F401 - imported to prove the module loads
    from . import plate_compare as pcmp
    from . import plate_hand_check as phc
    from . import plate_model as pm

    results = []

    # 1. A mesh with no shells in it. The whole case is plate bending; a beam mesh would produce a
    # full table of plausible numbers and a zero pressure load.
    beams_only = _unit_grid(2, 2, kind="LineShapes.LINE")
    results.append(
        _expect_raise(
            "a FEM with no shell elements",
            pm.PlateModelInvalid,
            lambda: pm.assert_has_shells(beams_only, mesh_size=0.125, stiffened=False),
        )
    )
    results.append(
        _expect(
            "and the same FEM yields no nodal load at all, rather than a small one",
            pm.consistent_nodal_loads(beams_only) == (),
            "a uniform pressure resolved onto no shells is a zero load vector",
        )
    )

    # 2. A probe the plate mesh does not seed, and two coincident nodes at one.
    sparse = _FakeResult([1, 2], [(0.0, 0.25, 0.0), (4.0, 0.25, 0.0)], [[1, 0, 0, 0, 0, 0, 0], [2, 0, 0, 0.1, 0, 0, 0]])
    results.append(
        _expect_raise(
            "a plate probe with no node",
            ProbeNotFound,
            lambda: sample_fea_result(
                sparse,
                pm.PROBE_POINTS,
                solver="fake",
                solver_version="selftest",
                model_name="plate_strip",
                load_case=pm.LOAD_CASE,
            ),
        )
    )
    doubled = _FakeResult(
        [1, 2],
        [(2.0, 0.25, 0.0), (2.0, 0.25, 0.0)],
        [[1, 0, 0, -0.17, 0, 0, 0], [2, 0, 0, -0.17, 0, 0, 0]],
    )
    results.append(
        _expect_raise(
            "two coincident nodes at a plate probe (an unmerged shell mesh)",
            AmbiguousProbe,
            lambda: sample_fea_result(
                doubled,
                (next(p for p in pm.PROBE_POINTS if p.name == "MID"),),
                solver="fake",
                solver_version="selftest",
                model_name="plate_strip",
                load_case=pm.LOAD_CASE,
            ),
        )
    )
    grid = _unit_grid(2, 2)
    results.append(
        _expect_raise(
            "a mesh that does not seed the strip's probe points",
            pm.PlateModelInvalid,
            lambda: pm.assert_probes_are_seeded(grid, mesh_size=1.0),
        )
    )

    # 3. The stiffener, measured as stiffness rather than counted. Three ways it goes wrong.
    bare = _plate_table("sestra", _SESTRA_BARE)
    results.append(
        _expect_raise(
            "the stiffener missing from one side (the stiffened run is the bare model)",
            pcmp.StiffenerMissing,
            lambda: pcmp.assert_stiffener_present(bare, _plate_table("abaqus", _SESTRA_BARE, stiffened=True)),
        )
    )
    # A bar whose section is rotated 90 degrees: I falls from a b^3 / 12 to b a^3 / 12, 20x, so
    # EI_bar goes from 15947 to 788 N m2 and the ratio from 0.376 to 0.924.
    rotated_ei = pm.section_properties()["E"] * pm.BAR_HEIGHT * pm.BAR_WIDTH**3 / 12.0
    rotated_ratio = phc.plate_ei() / (phc.plate_ei() + rotated_ei)
    rotated = {name: (u3 * rotated_ratio, r2 * rotated_ratio) for name, (u3, r2) in _SESTRA_BARE.items()}
    results.append(
        _expect_raise(
            f"a stiffener whose section is rotated 90 degrees (ratio {rotated_ratio:.4f}, not 0.3762)",
            pcmp.StiffenerMissing,
            lambda: pcmp.assert_stiffener_present(bare, _plate_table("abaqus", rotated, stiffened=True)),
        )
    )
    zeroed = {name: (0.0, 0.0) for name in _SESTRA_BARE}
    results.append(
        _expect_raise(
            "an all-zero bare table, from which no ratio can be taken",
            pcmp.StiffenerMissing,
            lambda: pcmp.assert_stiffener_present(
                _plate_table("sestra", zeroed), _plate_table("abaqus", _SESTRA_STIFF, stiffened=True)
            ),
        )
    )

    # 4. Cylindrical bending lost. The closed form is then the wrong one by up to 9% and every
    # number still looks like a plate under pressure.
    fanned = dict(_SESTRA_BARE)
    fanned["MID_Y0"] = (_SESTRA_BARE["MID_Y0"][0] * 1.01, 0.0)
    results.append(
        _expect_raise(
            "a 1% fan-out across the strip's width (the long-edge constraint lost)",
            pcmp.NotCylindrical,
            lambda: pcmp.assert_cylindrical(_plate_table("sestra", fanned)),
        )
    )
    results.append(
        _expect_raise(
            "three mid-span probes that are all zero -- an unloaded plate is uniform too",
            pcmp.NotCylindrical,
            lambda: pcmp.assert_cylindrical(_plate_table("sestra", zeroed)),
        )
    )

    # 5. The reaction total: the one number that comes from the solver's own bookkeeping.
    solve = _plate_solve(bare, mesh_size=0.03125, stiffened=False)
    results.append(
        _expect(
            "the correct reaction total passes",
            pcmp.assert_reaction_total(solve) <= pcmp.REACTION_REL_TOL,
            f"{solve.reaction_total!r} against {pm.expected_load_total()!r} N",
        )
    )
    results.append(
        _expect_raise(
            "half the load reacted",
            pcmp.ReactionMismatch,
            lambda: pcmp.assert_reaction_total(_plate_solve(bare, mesh_size=0.03125, stiffened=False, reaction=1000.0)),
        )
    )
    results.append(
        _expect_raise(
            "the whole load reacted with the wrong sign, which magnitude alone would admit",
            pcmp.ReactionMismatch,
            lambda: pcmp.assert_reaction_total(
                _plate_solve(bare, mesh_size=0.03125, stiffened=False, reaction=-2000.0)
            ),
        )
    )

    # 6. A refinement sequence that is not one. Each of these would otherwise be extrapolated into
    # a number and then compared against another solver's.
    # NotASequence and not NotConverging, deliberately: a list that mixes two variants also
    # happens to turn around, so a check accepting either could not tell whether the structural
    # guard was still there. Found by mutation -- deleting the _assert_one_sequence call left all
    # four of these passing while they expected NotConverging.
    sestra_bare_seq = _plate_sequence("sestra", False)
    results.append(
        _expect_raise(
            "an extrapolation over two meshes",
            pcmp.NotASequence,
            lambda: pcmp.extrapolate(sestra_bare_seq[:2]),
        )
    )
    results.append(
        _expect_raise(
            "a sequence in the wrong order (fine to coarse)",
            pcmp.NotASequence,
            lambda: pcmp.extrapolate(list(reversed(sestra_bare_seq))),
        )
    )
    results.append(
        _expect_raise(
            "a sequence mixing the bare and stiffened variants",
            pcmp.NotASequence,
            lambda: pcmp.extrapolate(sestra_bare_seq[:2] + _plate_sequence("sestra", True)[2:]),
        )
    )
    results.append(
        _expect_raise(
            "a sequence mixing two solvers",
            pcmp.NotASequence,
            lambda: pcmp.extrapolate(sestra_bare_seq[:2] + _plate_sequence("abaqus", False)[2:]),
        )
    )
    # A significant component that does not converge: the same value at every mesh means the mesh
    # never changed, which is exactly what a seeding bug looks like.
    flat = [
        _plate_solve(_plate_table("sestra", _SESTRA_BARE), mesh_size=size, stiffened=False) for size in pm.MESH_SIZES
    ]
    results.append(
        _expect_raise(
            "a significant component identical at all three meshes (the mesh never changed)",
            phc.NotConverging,
            lambda: pcmp.extrapolate(flat),
        )
    )
    # And it must say WHICH component, out of 42. Found by mutation: a bare re-raise loses the
    # address and the message becomes "the two finest values are identical" with nothing to act on.
    try:
        pcmp.extrapolate(flat)
        message = ""
    except phc.NotConverging as exc:
        message = str(exc)
    results.append(
        _expect(
            "and the failure names the probe and the component it was about",
            "MID.u3" in message and "sestra" in message,
            message.splitlines()[0][:110] if message else "(nothing raised)",
        )
    )

    # 7. The two cases must never be cross-compared: their probe sets are different structures.
    results.append(
        _expect_raise(
            "the plate probe set compared against the portal frame's",
            compare.ProbeSetMismatch,
            lambda: compare.compare(bare, _table("abaqus", _REFERENCE)),
        )
    )

    # 8. An unattached stiffener in adapy's own mesh, before a deck is written.
    detached = _unit_grid(4, 2)
    for node in detached.nodes:
        node.p = (node.p[0], node.p[1] * pm.STRIP_WIDTH / 2.0, 0.0)
    results.append(
        _expect_raise(
            "a stiffener line whose nodes are not shared by a shell and a beam element",
            pm.PlateModelInvalid,
            lambda: pm.assert_stiffener_shares_nodes(detached),
        )
    )
    return results


def main() -> int:
    results = (
        check_loud_failures()
        + check_agreement()
        + check_hand_check()
        + check_solver_section_idealisation()
        + check_plate_closed_forms()
        + check_plate_convergence()
        + check_plate_agreement()
        + check_plate_boundary_semantics()
        + check_plate_loud_failures()
    )
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
