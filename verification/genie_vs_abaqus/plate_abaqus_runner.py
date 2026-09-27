"""The Abaqus half of the plate case: the same model -> a CAE script -> ``S4R`` -> a table.

Written by ``Part.to_abaqus_cae_script`` from :func:`plate_model.build_strip`, the same function
the Sestra half calls, and **the whole model travels through the writer** -- the body, the section,
the stiffener, the three supports, the pressure, the step, the job and the displacement sidecar.
Nothing is appended to the emitted script. What the writer carries is measured in that script and
in its own sidecars rather than assumed.

What the writer translates, verified in the emitted script and its sidecars
==========================================================================

* the plate as the **ACIS body adapy's own SAT writer produces** (``strip_Strip.sat`` beside the
  script), imported with ``openAcis`` / ``PartFromGeometryFile``. One face for the bare strip,
  **two** for the stiffened one -- the body arrives already split along the bar's axis -- and the
  emitted script asserts each face's area and normal against adapy's own;
* a ``HomogeneousShellSection(thickness=0.01, material='S355')`` at ``offsetType=MIDDLE_SURFACE``
  on every face, which is what puts the plate's reference surface where adapy's mesh puts its
  nodes -- and therefore what makes the bar's centroid coincide with it;
* the bar as a ``Part.Stringer`` on the edge the body already carries, with
  ``n1=(0.0, 1.0, 0.0)`` so its 45 mm dimension stands out of the plate. Measured on this very
  model: ``{'S4R': 128, 'B31': 32}`` at the coarsest seed and **165 nodes either way** -- the 32
  beam elements add none, they reuse the shells'; 512 + 64 on 585 nodes and 2048 + 128 on 2193 at
  the two finer seeds, matching the Sestra deck's ``FQUS`` and ``BEAS`` counts element for element.
  An *ordinary* shared edge gives ``{'S4R': 128}`` and no beam elements at all;
* ``S4R`` on the faces, ``B31`` on the stringer, ``seedPart(size=mesh_size)``;
* the three supports of :data:`plate_model.EDGE_SUPPORTS` as assembly-level ``Set``s of geometry
  **edges**, one ``DisplacementBC`` each in the ``Initial`` step, with every one of the six dofs
  written out -- ``0.0`` where the record fixes it and ``UNSET`` where it does not. See below;
* the ``StaticStep``, a ``FieldOutputRequest`` already naming ``U``, ``UR`` and ``RF``, and --
  with ``submit=True`` -- the job, the writer's own equilibrium guard, and the
  ``plate.cae_displacements.json`` this module reads.

The gap that used to be here, and the measurement it leaves behind
=================================================================

Until adapy **PR #405** (``feat/abaqus-cae-region-supports``) ``ada.cadit.cae.analysis`` resolved a
``Bc``'s ``FemSet`` against the **geometric vertices** of the emitted model only -- the plate's
corners and the members' ends. The interior nodes of a plate edge are not vertices, so all three of
this model's supports were refused by name, and this module appended a driver of its own that built
the regions with ``getByBoundingBox`` and submitted the job itself. That driver and the function
that reproduced the refusal are **both gone**: the writer classifies each record's nodes against
its own geometry and builds a vertex, an edge or a face region
(``ada.cadit.cae.analysis.classify_region``), and all three of these come out ``'edge'``.

The answer did not move by a digit. The hand-written driver's bare strip at a 0.125 seed gave
``u3(MID) = -0.17306548357009888``; the writer's own regions give ``-0.17306548357009888``, and so
did a third, independent hand-built CAE probe (``-0.173065483570099``). That is what says the
writer's regions are the *same* regions, and it is why every Abaqus number in
:mod:`plate_hand_check`'s tables is unchanged across the switch.

The measurement that made the driver locate edges by box rather than by ``findAt`` is still the
reason the writer does, and it is still the thing most worth stating about this model: on the
**stiffened** strip the bar splits each supported end, so ``SS_X0`` is **two** collinear edges of
0.25 m, and ``findAt`` at a point on that boundary returns **one** of them -- half a simple
support, which moves the answer by about 10% and leaves every number in the report plausible.
Measured in the emitted script for this very model, read back off the kernel and reported as
``region_edges`` (edges, and their total length):

=========  ===================  ===================
region     bare strip           stiffened strip
=========  ===================  ===================
``SS_X0``  1 edge, 0.5          **2** edges, 0.5
``SS_X1``  1 edge, 0.5          **2** edges, 0.5
``CYL``    2 edges, 8.0         2 edges, 8.0
=========  ===================  ===================

Each of those is checked *in the kernel* against what adapy computed from the body it authored --
per box and in total, count and length -- so a region that came out short fails the build instead
of quietly solving a differently supported plate. :func:`read_checks` refuses a run whose three
supports did not all arrive as edge regions, for the same reason.

The load: the writer's own pressure, and why it is the same vector as Sestra's
=============================================================================

This side is given the ``pressure`` load, which the writer turns into an assembly ``Surface`` over
the plate's faces and a ``Pressure`` on it (``*Dsload``) -- ``side1Faces``, so a positive magnitude
pushes against the plate's declared ``+z`` normal, i.e. in ``-z``, which is the sign the Sestra
side's negative nodal forces have. A pressure reaches **no** nodal field in the ODB, so the
writer's equilibrium guard carries it as adapy's own magnitude times adapy's own area for the
plate, along that plate's declared normal: ``APPLIED_PRESSURE (0.0, 0.0, -2000.0)`` against a
reaction total of ``2000.0`` in ``z``. That is the other half of PR #405, and without it every
solved plate model read as out of equilibrium by the whole of its own load.

The Sestra side cannot be given it: adapy's Sesam writer has no distributed-load record at all and
reports ``[OMITTED] a "pressure" load is not written`` (see :mod:`plate_sestra_runner`). It is
given the exact consistent nodal load instead, and *exact* is the operative word: for a 4-node
bilinear quadrilateral ``integral(N_i) dA = A / 4``, so ``q A / 4`` at each of an element's four
nodes is the vector Abaqus assembles from the pressure, not a lumping of it. Both solvers mesh
this strip with 4-node quads on the identical structured grid, and the evidence that the swap
changed nothing is that each solver's *own* reaction total comes back at ``q L b``:

    Abaqus (pressure)   2000.0 in z, in-plane components at 1e-13     bare, every seed
    Sestra (nodal)      2000.0 in z, in-plane components exactly 0    bare, every seed

and on the stiffened strip 1999.999962 (Abaqus) against 1999.999954 (Sestra), 1.9e-08 and 2.3e-08
of it; and that the two answers converge to the same extrapolant -- 1.720e-05 apart on the bare
strip and 1.271e-05 on the stiffened one -- and each to the closed form.

Running it
==========

From the repository root::

    python -m verification.genie_vs_abaqus.run_comparison --case plate --work-dir D:/temp/plate

There are four CAE tokens on the site server and this case needs **six** solves, so they are run
strictly one at a time, each bounded by :data:`RUN_TIMEOUT`; the launcher search, the timeout and
the sanitised child environment come from ``tests.core.cadit.cae.abaqus_runner`` rather than being
reimplemented -- that module is where the measurement lives that a relative ``PYTHONPATH``
inherited by the child kills ``job.submit()`` with a bare ``Abaqus Error`` and no traceback.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass

from . import plate_model
from .abaqus_runner import AbaqusFailed, AbaqusNotInstalled, read_displacements_sidecar
from .displacements import sample_fea_result
from .plate_sestra_runner import PlateSolve, model_name

#: The shell element the comparison is closed with. ``S4R`` is the only shell code whose answer
#: against a closed form has been measured on this writer's own output, and the only one
#: ``ada.cadit.cae.analysis.CARRIED_SHELL_ELEMENT_TYPES`` admits alongside ``S4``/``S8R``. It is
#: also the like-for-like choice against Sestra's ``FQUS``: both are 4-node bilinear shells, which
#: is what makes a convergence comparison between them a statement about the translation rather
#: than about two element theories.
DEFAULT_SHELL_ELEMENT = "S4R"

#: Seconds. The finest mesh is 2048 ``S4R`` and solves in a few; this is a stuck-run guard, and it
#: matters because a hung run holds one of the four site CAE tokens.
RUN_TIMEOUT = 1800.0

#: The job the writer submits, and the stem of the solver's own files.
JOB_NAME = "plate_job"

#: The emitted script's name, without its suffix. The writer names both sidecars below off it,
#: which is why it is a constant here rather than spelled out three times.
SCRIPT_STEM = "plate"

#: The writer's displacement sidecar, read by :func:`abaqus_runner.read_displacements_sidecar`.
DISPLACEMENTS_NAME = "{0}.cae_displacements.json".format(SCRIPT_STEM)

#: The writer's build-result sidecar: every guard's verdict, the mesh it built, the regions it
#: resolved and the equilibrium it checked. :func:`read_checks` reads this run's provenance out of
#: it rather than out of a sidecar of this package's own -- there is nothing left for this module
#: to measure that the writer does not already measure in the kernel.
BUILD_RESULT_NAME = "{0}.cae_build_result.json".format(SCRIPT_STEM)


@dataclass(frozen=True)
class PlateRunChecks:
    """What the writer measured about the model it solved, read back from :data:`BUILD_RESULT_NAME`."""

    reaction_total: tuple[float, float, float]
    node_count: int
    element_counts: dict[str, int]
    #: Per support set, what kind of geometry its region resolved to. All three are ``'edge'``.
    region_kinds: dict[str, str]
    #: Per support set, ``(edges, total length)`` as CAE itself measured them.
    region_edges: dict[str, tuple[int, float]]
    solved: bool


def emit_and_run(
    work_dir: str | pathlib.Path,
    *,
    mesh_size: float,
    stiffened: bool,
    shell_element_type: str = DEFAULT_SHELL_ELEMENT,
) -> pathlib.Path:
    """Write the CAE script for one variant at one seed, run it, and return its run directory.

    ``submit=True``: the job, the equilibrium check and the displacement sidecar are the writer's
    own, because the supports are too. Nothing is appended to the script -- see the module
    docstring for the driver this used to need and the PR that removed the need for it.
    """
    try:
        from tests.core.cadit.cae.abaqus_runner import abaqus_command, run_cae_script
    except ImportError as exc:  # pragma: no cover - depends on how this is invoked
        raise AbaqusNotInstalled(
            "could not import tests.core.cadit.cae.abaqus_runner, which owns the Abaqus launcher "
            "search and the sanitised child environment this needs: {0}. Run this from the "
            "repository root.".format(exc)
        ) from exc

    if abaqus_command() is None:
        raise AbaqusNotInstalled(
            "no Abaqus install found. tests.core.cadit.cae.abaqus_runner looks for "
            "C:/SIMULIA/Commands/abqXXXX.bat and honours ADA_ABAQUS_CMD."
        )

    work_dir = pathlib.Path(work_dir)
    run_dir = work_dir / "abaqus_{0}".format(
        "{0}_{1}".format("stf" if stiffened else "bare", str(mesh_size).replace(".", "p"))
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    assembly = plate_model.build_strip(mesh_size, stiffened=stiffened, route="abaqus")
    script = run_dir / "{0}.py".format(SCRIPT_STEM)
    # The assembly, not the part: the supports are on the part's FEM while the step carrying the
    # pressure is on the assembly's, so writing the part alone would refuse for want of the step.
    assembly.to_abaqus_cae_script(
        script,
        mesh_size=mesh_size,
        shell_element_type=shell_element_type,
        job_name=JOB_NAME,
        submit=True,
    )

    run = run_cae_script(script, run_dir, timeout=RUN_TIMEOUT)
    if run.licence_denied:
        raise AbaqusNotInstalled("Abaqus is installed but no CAE licence was available:\n" + run.stdout)
    if run.failed:
        raise AbaqusFailed(
            "the emitted CAE script did not run cleanly for the {0} strip at seed {1}, so there is "
            "no result to compare. Signals: {2}\n{3}".format(
                "stiffened" if stiffened else "bare", mesh_size, run.failure_signals, run.describe()
            )
        )
    if not (run_dir / DISPLACEMENTS_NAME).is_file():
        raise AbaqusFailed(
            "the build reported success and left no displacement sidecar at {0} for the {1} strip at "
            "seed {2}. The writer writes one only when the job it submitted finished, so read {3} "
            "for what it did instead.".format(
                run_dir / DISPLACEMENTS_NAME,
                "stiffened" if stiffened else "bare",
                mesh_size,
                run_dir / BUILD_RESULT_NAME,
            )
        )
    return run_dir


def read_checks(run_dir: str | pathlib.Path) -> PlateRunChecks:
    """Read this run's provenance out of the writer's own :data:`BUILD_RESULT_NAME`.

    Refused rather than defaulted at every step: the reaction total is the check that the whole
    pressure arrived, and the region kinds are the check that the three supports ran along their
    edges rather than sitting on the four corners a vertex region would have caught. A missing
    file, a failed build, or a support that resolved to the wrong kind of geometry would each
    otherwise become a silently skipped check.
    """
    run_dir = pathlib.Path(run_dir)
    path = run_dir / BUILD_RESULT_NAME
    if not path.is_file():
        raise AbaqusFailed(
            "no {0} in {1}. The emitted script writes it whenever CAE ran it at all, so its absence "
            "means the script never started -- and the reaction total, which is what says the whole "
            "pressure arrived, cannot be checked.".format(BUILD_RESULT_NAME, run_dir)
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = str(payload.get("schema", ""))
    if not schema.startswith("ada.cae_build_result/"):
        raise AbaqusFailed("{0} is not a CAE build-result sidecar (schema {1!r})".format(path, schema))
    if not payload.get("ok"):
        raise AbaqusFailed(
            "the emitted script's own guards failed for {0}, so whatever it solved is not the model "
            "adapy described: {1}".format(run_dir, payload.get("errors") or "(no errors recorded)")
        )

    equilibrium = payload.get("equilibrium") or {}
    if "reaction_force_sum" not in equilibrium:
        raise AbaqusFailed(
            "{0} carries no reaction total, which means the script was not asked to submit the job "
            "(it holds {1}). That total is what says the whole pressure arrived, so it is not "
            "optional here.".format(path, sorted(equilibrium) or "nothing")
        )
    total = [float(v) for v in equilibrium["reaction_force_sum"]]

    mesh = payload.get("mesh") or {}
    if len(mesh) != 1:
        raise AbaqusFailed(
            "{0} reports {1} meshed part(s) ({2}) and this model is one part: a node count summed "
            "over several would not be the strip's.".format(path, len(mesh), sorted(mesh))
        )
    meshed = mesh[sorted(mesh)[0]]

    kinds = {str(k): str(v) for k, v in sorted((payload.get("analysis") or {}).get("region_kinds", {}).items())}
    edges = {str(k): (int(v[0]), float(v[1])) for k, v in sorted((payload.get("region_edges") or {}).items())}
    wrong = [
        "{0!r} is {1}".format(name, kinds.get(name, "absent"))
        for name, _dofs, _why in plate_model.EDGE_SUPPORTS
        if kinds.get(name) != "edge"
    ]
    if wrong:
        raise AbaqusFailed(
            "the three supports did not all reach CAE as regions along their edges -- {0}. A support "
            "on the two corner vertices of a supported end holds two nodes instead of the whole "
            "edge, which solves and comes out about 10% too soft; the regions read back as "
            "{1}.".format(", ".join(wrong), kinds or "(none)")
        )

    return PlateRunChecks(
        reaction_total=(total[0], total[1], total[2]),
        node_count=int(meshed["nodes"]),
        element_counts={str(k): int(v) for k, v in sorted(meshed["elements_by_type"].items())},
        region_kinds=kinds,
        region_edges=edges,
        solved=bool(payload.get("displacements")),
    )


def abaqus_displacements(
    run_dir: str | pathlib.Path,
    *,
    mesh_size: float,
    stiffened: bool,
    step: int | None = None,
) -> PlateSolve:
    """Read one run directory's sidecars and sample the probes. The Abaqus counterpart of
    :func:`plate_sestra_runner.sestra_displacements`, and symmetric with it: both end in
    :func:`displacements.sample_fea_result`.

    The shell code travels in ``solver_version`` because it decides the physics of the answer, so
    a report that does not name it is missing the one thing that explains a residual.
    """
    run_dir = pathlib.Path(run_dir)
    checks = read_checks(run_dir)
    result = read_displacements_sidecar(run_dir / DISPLACEMENTS_NAME)
    version = result.solver_version or "unknown"
    if result.element_type:
        version = "{0} {1}".format(version, result.element_type)
    table = sample_fea_result(
        result,
        plate_model.PROBE_POINTS,
        solver="abaqus",
        solver_version=version,
        model_name=model_name(stiffened),
        load_case=plate_model.LOAD_CASE,
        step=step,
    )
    return PlateSolve(
        table=table,
        mesh_size=mesh_size,
        stiffened=stiffened,
        reaction_total=checks.reaction_total,
        node_count=checks.node_count,
        element_counts=checks.element_counts,
        source=str(run_dir / DISPLACEMENTS_NAME),
    )


def run_and_sample(work_dir: str | pathlib.Path, *, mesh_size: float, stiffened: bool) -> PlateSolve:
    """:func:`emit_and_run` then :func:`abaqus_displacements`. One variant at one seed."""
    run_dir = emit_and_run(work_dir, mesh_size=mesh_size, stiffened=stiffened)
    return abaqus_displacements(run_dir, mesh_size=mesh_size, stiffened=stiffened)


def run_sequence(
    work_dir: str | pathlib.Path,
    *,
    stiffened: bool,
    mesh_sizes: tuple[float, ...] = plate_model.MESH_SIZES,
) -> list[PlateSolve]:
    """One variant at every seed, coarse to fine. Strictly sequential: four CAE tokens exist."""
    return [run_and_sample(work_dir, mesh_size=size, stiffened=stiffened) for size in mesh_sizes]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plate_abaqus_runner",
        description="Solve the plate strip in Abaqus at every mesh density and write the tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--work-dir", default="temp/plate_verification", help="where to emit and run")
    parser.add_argument("--stiffened", action="store_true", help="the variant with the flat bar along the centreline")
    parser.add_argument(
        "--mesh-size",
        type=float,
        default=None,
        help="one seed only (default: every one of plate_model.MESH_SIZES)",
    )
    parser.add_argument("--json-out-dir", help="also write each table as JSON here")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Solve the Abaqus side and write its tables. ``0`` written, ``2`` could not be run."""
    args = build_parser().parse_args(argv)
    sizes = (args.mesh_size,) if args.mesh_size else plate_model.MESH_SIZES
    try:
        solves = [run_and_sample(args.work_dir, mesh_size=size, stiffened=args.stiffened) for size in sizes]
    except (AbaqusNotInstalled, AbaqusFailed, OSError, ValueError) as exc:
        print("ERROR: the Abaqus side could not be run: {0}".format(exc), file=sys.stderr)
        return 2

    for solve in solves:
        peak = solve.table.component(plate_model.DEFLECTION_PROBE, "u3")
        print(
            "{0:<22} nodes {1:>5}  {2}  reaction {3}  u3({4}) {5:.12e}".format(
                solve.label,
                solve.node_count,
                solve.element_counts,
                tuple(round(v, 6) for v in solve.reaction_total),
                plate_model.DEFLECTION_PROBE,
                peak,
            )
        )
        if args.json_out_dir:
            out = pathlib.Path(args.json_out_dir) / "abaqus_{0}.json".format(
                "{0}_{1}".format("stf" if solve.stiffened else "bare", str(solve.mesh_size).replace(".", "p"))
            )
            print("  wrote {0}".format(solve.table.to_json(out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
