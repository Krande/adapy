"""CLI for the cross-solver comparison. Run from the repository root.

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
        description="Portal frame: Sestra vs Abaqus vs closed form.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
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
