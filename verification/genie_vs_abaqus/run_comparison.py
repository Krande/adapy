"""CLI for the cross-solver comparisons. Run from the repository root.

Two cases, chosen with ``--case``. They share the exchange format (:mod:`displacements`) and the
comparator (:mod:`compare`) and nothing else, because they are asking different questions: the
portal frame compares two *beam* formulations that have no discretisation error left to speak of,
at one mesh, node for node; the plate strip compares two *shell* formulations that both converge
with mesh, at three densities, between their extrapolants. ``--case frame`` is the default and
everything below it is unchanged.

The plate case, both solvers and both variants -- six Sestra solves and six Abaqus ones::

    python -m verification.genie_vs_abaqus.run_comparison --case plate --work-dir D:/temp/plate

and re-reading those solves without spending a CAE token or a Sestra run on them again::

    python -m verification.genie_vs_abaqus.run_comparison --case plate --work-dir D:/temp/plate --reuse

Sestra side plus the hand check (this is what works today)::

    python -m verification.genie_vs_abaqus.run_comparison --work-dir D:/temp/genie_vs_abaqus

Cross-solver comparison, once an Abaqus displacement table exists::

    python -m verification.genie_vs_abaqus.run_comparison \
        --work-dir D:/temp/genie_vs_abaqus --abaqus-json path/to/abaqus_portal.json

Re-read a previous Sestra solve without re-solving::

    python -m verification.genie_vs_abaqus.run_comparison --sin D:/temp/.../portalR1.SIN

Emit a skeleton for the Abaqus half to fill in::

    python -m verification.genie_vs_abaqus.run_comparison --write-abaqus-template aba.json

Exit codes: ``0`` all checks passed, ``1`` a check failed, ``2`` the run could not be
performed at all (no Sestra, solver error, unreadable table). A failed *check* and a failed
*run* are different outcomes and a CI wrapper should treat them differently.
"""

from __future__ import annotations

import argparse
import sys

from . import abaqus_runner, compare, hand_check, model, sestra_runner
from .displacements import DisplacementTable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_comparison",
        description="Portal frame or plate strip: Sestra vs Abaqus vs closed form.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--case",
        choices=("frame", "plate"),
        default="frame",
        help="which comparison to run (default: frame, the portal frame; see the module docstring "
        "for why the plate case is a convergence study and the frame is not)",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="plate case only: read the artefacts of an earlier run out of --work-dir instead of "
        "solving again. Six CAE solves are six of the site's four tokens held in turn",
    )
    parser.add_argument(
        "--plate-rel-tol",
        type=float,
        default=None,
        help="plate case only: cross-solver tolerance on the extrapolated tables (default "
        "plate_compare.PLATE_REL_TOL, read off the convergence study -- override to explore, "
        "not to make a run pass)",
    )
    parser.add_argument(
        "--work-dir",
        default="temp/genie_vs_abaqus",
        help="where to write the Sesam deck and run Sestra (default: temp/genie_vs_abaqus)",
    )
    parser.add_argument("--case-name", default="portal", help="deck / analysis name (default: portal)")
    parser.add_argument(
        "--sin",
        help="read this existing .SIN instead of running Sestra (skips the solve entirely)",
    )
    parser.add_argument(
        "--abaqus-json",
        help="a DisplacementTable JSON from the Abaqus half; without it only Sestra and the hand check run",
    )
    parser.add_argument(
        "--sestra-json-out",
        help="also write the Sestra table to this path, so the Abaqus half can be compared "
        "against it later without re-solving",
    )
    parser.add_argument(
        "--write-abaqus-template",
        help="write a zero-filled DisplacementTable skeleton here and exit",
    )
    parser.add_argument(
        "--rel-tol",
        type=float,
        default=compare.REL_TOL,
        help=f"cross-solver relative tolerance (default {compare.REL_TOL:.1e}, budgeted in "
        f"compare's docstring -- override to explore, not to make a run pass)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.case == "plate":
        return plate_main(args)

    if args.write_abaqus_template:
        path = abaqus_runner.write_template_json(args.write_abaqus_template)
        print(f"wrote {path}")
        print("note: as written it will not pass -- compare.assert_has_signal rejects an all-zero table.")
        return 0

    print(_describe_model())

    try:
        if args.sin:
            print(f"reading existing Sestra result: {args.sin}")
            sestra_table = sestra_runner.sestra_displacements(args.sin)
        else:
            print(f"locating Sestra via adapy: {sestra_runner.sestra_exe()}")
            sestra_table = sestra_runner.run_and_sample(args.work_dir, case_name=args.case_name)
    except (sestra_runner.SestraNotInstalled, sestra_runner.SestraFailed) as exc:
        print(f"ERROR: the Sestra side could not be run: {exc}", file=sys.stderr)
        return 2

    print(f"\nSestra {sestra_table.solver_version} -- {sestra_table.source}")
    print(_format_table(sestra_table))

    if args.sestra_json_out:
        out = sestra_table.to_json(args.sestra_json_out)
        print(f"\nwrote {out}")

    exit_code = 0

    print()
    sestra_hand = compare.hand_check(sestra_table)
    print(compare.format_hand_check(sestra_hand))
    predictions = hand_check.portal_frame_predictions()
    timo = predictions["timoshenko"]
    print(
        f"shear parameters: phi_column={timo.phi_column:.6f}, phi_girder={timo.phi_girder:.6f}; "
        f"transverse shear raises the closed-form sway by "
        f"{100 * (timo.delta / predictions['euler-bernoulli'].delta - 1):.3f}%"
    )
    if not sestra_hand.ok:
        print(
            "HAND CHECK FAILED -- the Sestra sway is outside the band spanned by both closed forms, "
            "so this is not an element-formulation difference.",
            file=sys.stderr,
        )
        exit_code = 1

    if not args.abaqus_json:
        print(
            "\nno --abaqus-json given, so no cross-solver comparison was made. "
            "See abaqus_runner's docstring for what the Abaqus half must provide."
        )
        return exit_code

    try:
        abaqus_table = abaqus_runner.load_abaqus_table(args.abaqus_json)
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read the Abaqus table: {exc}", file=sys.stderr)
        return 2

    print(f"\nAbaqus {abaqus_table.solver_version} -- {abaqus_table.source}")
    print(_format_table(abaqus_table))

    print()
    try:
        report = compare.compare(sestra_table, abaqus_table, rel_tol=args.rel_tol)
    except (compare.ProbeSetMismatch, compare.NoSignal) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(report.format_table())

    print()
    # Abaqus' section=PIPE integrates the wall as a line, so the closed form must be given that
    # section and not adapy's exact annulus -- a different question from which beam theory the
    # element uses, and 0.276% wider than the bracket's slack. See hand_check's docstring.
    abaqus_hand = compare.hand_check(
        abaqus_table,
        inertia=model.abaqus_pipe_inertia(),
        inertia_note="thin-walled pi rm^3 t, the section Abaqus itself integrates",
    )
    print(compare.format_hand_check(abaqus_hand))
    adapy_inertia = model.section_properties()["Iy"]
    print(
        f"  (the model's own exact annulus is {adapy_inertia:.6e} m4, "
        f"{100 * (adapy_inertia / abaqus_hand.inertia - 1):.3f}% higher; feeding that here instead "
        f"would shift the bracket below Abaqus by 0.077% and fail a correct translation)"
    )
    if not abaqus_hand.ok:
        print(
            "HAND CHECK FAILED for Abaqus -- outside the band spanned by both closed forms.",
            file=sys.stderr,
        )
        exit_code = 1
    if sestra_hand.nearest.formulation != abaqus_hand.nearest.formulation:
        print(
            f"\nnote: the two solvers sit at opposite ends of the closed-form bracket "
            f"(Sestra nearest {sestra_hand.nearest.formulation}, Abaqus nearest "
            f"{abaqus_hand.nearest.formulation}). That is an element-formulation difference, not "
            f"a translation defect -- expected if one side uses a shear-rigid beam such as B33."
        )

    if not report.ok:
        print(
            f"\nCROSS-SOLVER COMPARISON FAILED: {len(report.failures)} component(s) outside {args.rel_tol:.1e}.",
            file=sys.stderr,
        )
        exit_code = 1
    else:
        print(f"\ncross-solver comparison passed at rel {args.rel_tol:.1e}.")

    return exit_code


def plate_main(args) -> int:
    """The plate case: three meshes per solver per variant, extrapolated, then compared.

    The order of what follows is the order of the argument it makes, and every step is a check
    that can fail on its own:

    1. the closed forms, printed from the model's own section so nothing here is retyped;
    2. per solver and per variant, the three-mesh sequence and its **measured** order --
       :func:`plate_compare.convergence_report`. A sequence that is not converging raises here
       rather than being extrapolated;
    3. each solve's reaction total against ``-q L b``, both solvers -- which is where the two
       different load forms (a pressure on the Abaqus side, the exact consistent nodal vector on
       the Sestra one) are shown to be the same load;
    4. the cylindrical-bending check on every table, which is what says the closed form with its
       ``1 - nu^2`` is the right one;
    5. the stiffness ratio on each solver's own pair, against ``EI_plate / sum EI``;
    6. the cross-solver comparison, between the **extrapolants**, at
       :data:`plate_compare.PLATE_REL_TOL`; and the same comparison at the finest mesh only, at
       the looser :data:`plate_compare.PLATE_MESH_REL_TOL`, printed for the diagnosis it gives.
    """
    from . import (
        plate_abaqus_runner,
        plate_compare,
        plate_hand_check,
        plate_model,
        plate_sestra_runner,
    )

    rel_tol = plate_compare.PLATE_REL_TOL if args.plate_rel_tol is None else args.plate_rel_tol
    print(plate_compare.format_closed_forms())
    print(
        f"\nmesh seeds {plate_model.MESH_SIZES}, {len(plate_model.PROBE_POINTS)} probes, "
        f"{'reusing existing artefacts' if args.reuse else 'solving'}"
    )

    solves: dict[tuple[str, bool], list] = {}
    try:
        for stiffened in (False, True):
            solves[("sestra", stiffened)] = _plate_sestra_sequence(
                plate_sestra_runner, args.work_dir, stiffened=stiffened, reuse=args.reuse
            )
    except (sestra_runner.SestraNotInstalled, sestra_runner.SestraFailed, OSError) as exc:
        print(f"ERROR: the Sestra side could not be run: {exc}", file=sys.stderr)
        return 2
    try:
        for stiffened in (False, True):
            solves[("abaqus", stiffened)] = _plate_abaqus_sequence(
                plate_abaqus_runner, args.work_dir, stiffened=stiffened, reuse=args.reuse
            )
    except (abaqus_runner.AbaqusNotInstalled, abaqus_runner.AbaqusFailed, OSError) as exc:
        print(f"ERROR: the Abaqus side could not be run: {exc}", file=sys.stderr)
        return 2

    exit_code = 0
    extrapolated: dict[tuple[str, bool], DisplacementTable] = {}
    for key in sorted(solves, key=lambda k: (k[1], k[0])):
        sequence = solves[key]
        report = plate_compare.convergence_report(sequence)
        print()
        print(report.format_table())
        for solve in sequence:
            residual = plate_compare.assert_reaction_total(solve)
            spread = plate_compare.assert_cylindrical(solve.table)
            print(
                f"  {solve.label:<22} reaction {tuple(round(v, 6) for v in solve.reaction_total)} "
                f"(rel {residual:.2e} of -q L b), width spread {spread:.3e}, "
                f"elements {solve.element_counts or '(not reported)'}"
            )
        extrapolated[key] = plate_compare.extrapolate(sequence)
        closed = plate_hand_check.stiffened_deflection() if key[1] else plate_hand_check.bare_deflection()
        tol = plate_hand_check.STIFFENED_REL_TOL if key[1] else plate_hand_check.BARE_REL_TOL
        measured = abs(extrapolated[key].component(plate_model.DEFLECTION_PROBE, "u3"))
        rel = abs(measured / closed - 1.0)
        verdict = "PASS" if rel <= tol else "FAIL"
        print(
            f"  hand check: extrapolant {measured:.12e} against {closed:.12e} -> rel {rel:.3e} "
            f"at tol {tol:.1e}  {verdict}"
        )
        if rel > tol:
            print(
                f"HAND CHECK FAILED for {key[0]} ({'stiffened' if key[1] else 'bare'}): the "
                f"extrapolated deflection is outside the closed form's tolerance.",
                file=sys.stderr,
            )
            exit_code = 1

    print()
    for solver in ("sestra", "abaqus"):
        ratio = plate_compare.assert_stiffener_present(extrapolated[(solver, False)], extrapolated[(solver, True)])
        print(
            f"{solver}: the stiffener makes the strip {1.0 / ratio:.4f}x stiffer -- ratio {ratio:.9f} "
            f"against EI_plate / sum EI = {plate_hand_check.stiffness_ratio():.9f}, rel "
            f"{abs(ratio / plate_hand_check.stiffness_ratio() - 1.0):.3e}"
        )
    rotation = plate_hand_check.support_rotation()
    for solver in ("sestra", "abaqus"):
        measured = abs(extrapolated[(solver, False)].component(plate_model.ROTATION_PROBE, "r2"))
        print(
            f"{solver}: support rotation {measured:.12e} against q L^3 / (24 D) = {rotation:.12e}, "
            f"rel {abs(measured / rotation - 1.0):.3e}"
        )

    for stiffened in (False, True):
        variant = "stiffened" if stiffened else "bare"
        print(f"\n--- {variant} strip, extrapolated tables, rel_tol {rel_tol:.1e}")
        try:
            report = compare.compare(
                extrapolated[("sestra", stiffened)], extrapolated[("abaqus", stiffened)], rel_tol=rel_tol
            )
        except (compare.ProbeSetMismatch, compare.NoSignal) as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(report.format_table())
        if not report.ok:
            print(
                f"CROSS-SOLVER COMPARISON FAILED ({variant}, extrapolated): "
                f"{len(report.failures)} component(s) outside {rel_tol:.1e}.",
                file=sys.stderr,
            )
            exit_code = 1

        finest_tol = plate_compare.PLATE_MESH_REL_TOL
        print(f"\n--- {variant} strip, finest mesh only, rel_tol {finest_tol:.1e} (diagnostic)")
        finest = compare.compare(
            solves[("sestra", stiffened)][-1].table,
            solves[("abaqus", stiffened)][-1].table,
            rel_tol=finest_tol,
        )
        worst = finest.worst
        print(
            f"{len(finest.diffs)} components, {len(finest.failures)} failed; worst significant "
            + (f"{worst.probe}.{worst.component} rel {worst.rel_diff:.3e}" if worst else "(none)")
        )
        if not finest.ok:
            print(
                f"the single-mesh comparison at h = {solves[('sestra', stiffened)][-1].mesh_size} is "
                f"outside {finest_tol:.1e}, which is the two shell elements' discretisation "
                f"difference and not by itself a translation defect -- read the extrapolated table "
                f"above for the verdict.",
                file=sys.stderr,
            )

    if exit_code == 0:
        print(f"\nplate case passed: both variants agree at rel {rel_tol:.1e} after extrapolation.")
    return exit_code


def _plate_sestra_sequence(runner, work_dir, *, stiffened: bool, reuse: bool) -> list:
    """One Sestra refinement sequence, solving or re-reading the decks already in ``work_dir``."""
    import pathlib

    from . import plate_model

    sequence = []
    for size in plate_model.MESH_SIZES:
        if reuse:
            case_name = runner.default_case_name(size, stiffened)
            sin_path = pathlib.Path(work_dir) / case_name / f"{case_name}R1.SIN"
            if not sin_path.is_file():
                raise OSError(
                    f"--reuse was given and there is no Sestra result at {sin_path}. Run the case "
                    f"once without --reuse to produce it."
                )
            sequence.append(runner.sestra_displacements(sin_path, mesh_size=size, stiffened=stiffened))
        else:
            sequence.append(runner.run_and_sample(work_dir, mesh_size=size, stiffened=stiffened))
    return sequence


def _plate_abaqus_sequence(runner, work_dir, *, stiffened: bool, reuse: bool) -> list:
    """One Abaqus refinement sequence, solving or re-reading the sidecars already in ``work_dir``."""
    import pathlib

    from . import plate_model

    sequence = []
    for size in plate_model.MESH_SIZES:
        if reuse:
            run_dir = pathlib.Path(work_dir) / "abaqus_{0}_{1}".format(
                "stf" if stiffened else "bare", str(size).replace(".", "p")
            )
            if not (run_dir / runner.DISPLACEMENTS_NAME).is_file():
                raise OSError(
                    f"--reuse was given and there is no Abaqus sidecar at "
                    f"{run_dir / runner.DISPLACEMENTS_NAME}. Run the case once without --reuse."
                )
            sequence.append(runner.abaqus_displacements(run_dir, mesh_size=size, stiffened=stiffened))
        else:
            sequence.append(runner.run_and_sample(work_dir, mesh_size=size, stiffened=stiffened))
    return sequence


def _describe_model() -> str:
    props = model.section_properties()
    return (
        f"portal frame: height {model.HEIGHT} m, span {model.SPAN} m, section {model.SECTION} "
        f"(A={props['area']:.6e} m2, Iy=Iz={props['Iy']:.6e} m4, "
        f"shear area={props['shear_area']:.6e} m2)\n"
        f"material {model.MATERIAL_NAME}: E={props['E']:.4g} Pa, nu={props['nu']}\n"
        f"supports: all six dofs fixed at both bases; "
        f"load: {model.P_TOTAL:.0f} N total, {model.P_TOTAL / 2:.0f} N in +X at each top corner\n"
        f"mesh target {model.MESH_SIZE} m, probes at {len(model.PROBE_POINTS)} named points"
    )


def _format_table(table: DisplacementTable) -> str:
    head = (
        f"{'probe':<13} {'node':>6} {'u1 [m]':>14} {'u2 [m]':>14} {'u3 [m]':>14} "
        f"{'r1 [rad]':>13} {'r2 [rad]':>13} {'r3 [rad]':>13}"
    )
    lines = [head, "-" * len(head)]
    for probe in model.PROBE_POINTS:
        if probe.name not in table.displacements:
            continue
        values = table.displacements[probe.name]
        nid = table.node_ids.get(probe.name, 0)
        lines.append(f"{probe.name:<13} {nid:>6} " + " ".join(f"{v:>13.6e}" for v in values[:3]))
        lines[-1] += " " + " ".join(f"{v:>12.6e}" for v in values[3:])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
