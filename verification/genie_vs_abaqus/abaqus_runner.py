"""The Abaqus half: the same adapy model -> a CAE script -> Abaqus/Standard -> displacements.

Derived from :func:`model.build_portal_frame` -- the *same* function the Sestra half calls, not
a reimplementation -- and written out by ``Part.to_abaqus_cae_script``, which now carries the
model's supports, loads and step as well as its geometry. So what this measures is a
translation, which is the whole premise of the package.

How the contract this module used to only state is satisfied
============================================================

It said the Abaqus half must produce a :class:`displacements.DisplacementTable` covering
**every** probe, six components in global axes, and offered two routes: in process through
:func:`sample_abaqus_result`, or out of process as JSON. Both are used, in that order:

* the emitted CAE script solves the job in the same run and writes
  ``<stem>.cae_displacements.json`` -- every node's position and its six components per step;
* :func:`read_displacements_sidecar` reads that into an object shaped like an adapy
  ``FEAResult``, and :func:`sample_abaqus_result` hands it to
  :func:`displacements.sample_fea_result`. So the Abaqus half grows **no node-matching code
  of its own**: the probes are resolved by the identical rule on both sides, in one function.

A real ``FEAResult`` read from the ODB through ``ada.fem.formats.abaqus.results.read_odb``
would have been the tidier-looking route, and it does not work here for a measured reason:
**Abaqus does not store the six displacement components in one field.** ``U`` has
``componentLabels ('U1', 'U2', 'U3')`` and the rotations are a separate ``UR`` with
``('UR1', 'UR2', 'UR3')`` (probed on Abaqus 2025). ``displacements._nodal_displacements``
requires all six from one field and correctly raises ``MissingDisplacementField`` on three --
so the join has to happen somewhere, and the emitted script does it at the ODB, once, next to
the measurement that explains why.

Which beam element, and why
===========================

:data:`DEFAULT_ELEMENT_TYPE` is ``B32``, the quadratic shear-flexible beam, and the choice is
measured rather than preferred. Every candidate was run through the whole comparison on this
exact frame; the sway is at ``TOP_L``, against Sestra's ``2.468646e-02`` m:

=====  ======  =============  ===========  ==========================  ====================
elem   seed    sway [m]       vs Sestra    cross-solver (54 comps)     hand-check bracket
=====  ======  =============  ===========  ==========================  ====================
B31    1.0     2.487099e-02   +7.42e-03    FAIL, 2 (girder ``r2``)     FAIL (+0.58%)
B31    0.5     2.477715e-02   +3.66e-03    not run                     FAIL
B31    0.25    2.475369e-02   +2.85e-03    not run                     FAIL
B32    1.0     2.474589e-02   +2.41e-03    **PASS, 0**, worst 4.9e-03  FAIL (+0.077%)
B33    1.0     2.459902e-02   -3.54e-03    FAIL, 3 (girder ``r2``)     PASS
=====  ======  =============  ===========  ==========================  ====================

``B32`` is the only one that closes the **cross-solver** comparison, which is what this package
exists to do, and it is the right formulation for the job besides: ``BEAS`` is shear-flexible,
so a shear-flexible Abaqus element is the like-for-like choice, and ``B32`` is already at
``B31``'s converged limit on this mesh (``B31`` falls as ``1/n^2`` towards 2.4746e-02), so it is
the best available Timoshenko answer rather than a lucky one.

Two residuals are left over, and both are *understood* rather than tolerated.

**1. The girder's ``r2``, and why the two shear-rigid runs fail on it.** The three components
``B33`` fails are ``GIRDER_QTR``, ``GIRDER_MID`` and ``GIRDER_3QTR``'s rotation about global Y --
the girder's own in-plane bending rotation -- and nothing else. That rotation is the *difference*
of two larger numbers: the joint rotation at the corner (2.90e-03 rad) minus the rotation the
girder's own curvature accumulates (4.32e-03 rad over half the span), leaving -3.4e-04 at the
quarter point. A 0.4% difference in the second term is therefore a 4% difference in the
remainder, and :data:`compare.ABS_FLOOR` (1e-06) is 340x below the value, so the relative
comparison stands. What differs in that second term is **shear**: ``B33`` has none and ``BEAS``
does, and the closed form says so -- Euler-Bernoulli puts that rotation at ``-theta/8``, which is
-3.62e-04, and ``B33`` reports -3.58e-04 while Sestra reports -3.44e-04. So the failing
components are the shear-rigid/shear-flexible difference showing up where cancellation amplifies
it elevenfold, and they are exactly what ``compare``'s own docstring predicts of ``B33`` ("read
the per-point table"). Give Abaqus a shear-flexible element and they pass: ``B32``'s worst
significant residual anywhere in the table is 4.9e-03, half the budget.

**2. The hand check, and the one number no element choice can fix: Abaqus' ``PIPE`` section is
thin-walled and adapy's is not.** Isolated on a cantilever under a *pure end moment* (``B33``, so
no shear; a moment, so no torsion), M = 1000 N m, L = 4 m, E = 210 GPa, the same ``OD200x10``:
``ur2 = 7.071638e-04`` rad, so ``I = M L / (E theta) = 2.693525e-05`` m4. adapy's exact annulus
``pi/4 (ro^4 - ri^4)`` is ``2.700984e-05`` -- Abaqus is **0.276% lower**, and within 2.2e-4 of the
thin-walled midline value ``pi rm^3 t = 2.692935e-05``. The two *areas* are identical, because
``2 pi rm t`` and ``pi (ro^2 - ri^2)`` are algebraically the same number, so only bending moves.
(Pinned as a licensed test: ``test_the_abaqus_pipe_sections_second_moment_is_the_thin_walled_one``.)

Fold that one factor in and Abaqus reproduces both closed forms:

* Euler-Bernoulli 2.452209e-02 x (I_ada / I_aba) = 2.459000e-02, against ``B33``'s 2.459902e-02;
* Timoshenko      2.467747e-02 x (I_ada / I_aba) = 2.474578e-02, against ``B32``'s 2.474589e-02
  -- agreement to 4e-06 relative.

That second line is the strongest single statement available about this translation: **Abaqus'
shear-flexible answer is the Timoshenko closed form for the section Abaqus itself integrates, to
six figures.** The translation is exact; the 0.28% is a section definition.

But :func:`hand_check.admissible_bracket` is computed from **adapy's** ``I`` and is about 1.0%
wide -- 0.63% between the two formulations plus 0.2% of slack at each end, budgeted (see
:data:`hand_check.HAND_CHECK_REL_TOL`) for member axial flexibility at the 1e-04 level and for
discretisation. Nothing in it was budgeted for a 0.276% *section* difference between the closed
form's ``I`` and the solver's, so the bracket cannot hold a Timoshenko-family Abaqus element on a
tube however correct the translation: the Timoshenko end of the band is Timoshenko + 0.2% and
Abaqus lands at Timoshenko + 0.276%. ``B32`` misses it by 0.077%. That is a finding about the
budget rather than about the writer, and it is reported as one: no tolerance in this package has
been touched, ``REL_TOL`` and ``HAND_CHECK_REL_TOL`` are as their authors set them, and the way
to close it would be to widen the bracket by the *measured* section difference -- deliberately,
in :mod:`hand_check`, with this number in the comment -- rather than by feel.

What the emitted script checks before this module reads anything
===============================================================

The ranked list this module used to carry said the full 10 kN must arrive, and that Sestra's own
load summary read ``tx = 1.0000e+04``. That is no longer something to remember to check by hand:
the emitted script sums ``CF`` and ``RF`` over every node in the ODB and **fails the build**
unless the concentrated-force total is the resultant adapy computed from its own ``Load``
records. Measured on this frame: ``concentrated_force_sum [10000.0, 0.0, 0.0]``,
``reaction_force_sum [-10000.0, 0.0, 0.0]``. Joint merging, the section, the support type and
probe seeding are likewise checked in the kernel or refused by the writer rather than left to be
inferred from the answer -- see the guards listed in :mod:`ada.cadit.cae.writer`.

Running it
==========

From the repository root::

    python -m verification.genie_vs_abaqus.abaqus_runner --work-dir D:/temp/genie_vs_abaqus
    python -m verification.genie_vs_abaqus.run_comparison \\
        --work-dir D:/temp/genie_vs_abaqus --abaqus-json D:/temp/genie_vs_abaqus/abaqus_b32.json

There are four CAE tokens on the site server, so the run is bounded by a timeout and the process
is killed rather than left holding one -- which ``tests.core.cadit.cae.abaqus_runner`` already
does, and is reused here rather than reimplemented. That module is also where the measurement
lives that a relative ``PYTHONPATH`` inherited by the child kills ``job.submit()`` with a bare
``Abaqus Error`` and no traceback.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

from . import model
from .displacements import COMPONENTS, DisplacementTable, sample_fea_result

#: The beam element the comparison is closed with. ``B32``, the quadratic shear-flexible beam:
#: measured, it is the only one of the three that closes the cross-solver comparison at every one
#: of its 54 components, and it is the like-for-like formulation against Sestra's shear-flexible
#: ``BEAS`` besides. The module docstring carries the whole table and both leftover residuals.
DEFAULT_ELEMENT_TYPE = "B32"

#: What the emitted CAE script names its displacement sidecar, off the script's own stem.
DISPLACEMENTS_SUFFIX = ".cae_displacements.json"

#: Seconds. A 20-element linear static solve takes a couple; this is a stuck-run guard, and it
#: matters because a hung run holds one of the four site CAE tokens.
RUN_TIMEOUT = 900.0


class AbaqusNotInstalled(RuntimeError):
    """No Abaqus command script was found on this machine, or no CAE licence was free."""


class AbaqusFailed(RuntimeError):
    """Abaqus ran, and the build, the solve or one of the emitted script's guards failed."""


class _Nodes:
    """``identifiers`` + ``coords``: the two attributes :func:`sample_fea_result` reads."""

    def __init__(self, identifiers, coords):
        self.identifiers = np.asarray(identifiers, dtype=np.int64)
        self.coords = np.asarray(coords, dtype=float)


class _Mesh:
    def __init__(self, nodes: _Nodes):
        self.nodes = nodes


class _Field:
    """One nodal field: component names, and a ``(n, 1 + ncomp)`` array labelled in column 0."""

    def __init__(self, components, values, step: int):
        self.components = tuple(components)
        self.values = np.asarray(values, dtype=float)
        self.step = step


class CaeDisplacementResult:
    """An ODB's nodal displacements, shaped like the part of an adapy ``FEAResult`` that matters.

    Deliberately a small adapter rather than a real ``FEAResult``:
    :func:`displacements.sample_fea_result`'s own docstring says it takes "anything with
    ``.mesh.nodes`` (``identifiers`` + ``coords``) and a nodal displacement field", so this is
    the documented seam and not a shortcut around one. Building a real ``FEAResult`` would
    additionally need element blocks and connectivity, none of which a displacement comparison
    reads.
    """

    def __init__(self, mesh: _Mesh, fields: dict[str, list[_Field]], **provenance):
        self.mesh = mesh
        self._fields = fields
        self.results_file_path = provenance.get("results_file_path", "")
        self.solver_version = provenance.get("solver_version", "")
        self.element_type = provenance.get("element_type", "")
        self.instance = provenance.get("instance", "")
        self.step_names = tuple(provenance.get("step_names", ()))
        self.job = provenance.get("job", "")

    def get_results_grouped_by_field_value(self) -> dict[str, list[_Field]]:
        return dict(self._fields)


def read_displacements_sidecar(path: str | pathlib.Path, *, instance: str | None = None) -> CaeDisplacementResult:
    """Read ``<stem>.cae_displacements.json``, written by the emitted CAE script.

    One instance only, and refused rather than guessed at when there are several: node labels in
    an ODB are numbered per instance, so two instances offer two node 7s and a table keyed by a
    single integer id could not say which one it sampled.
    """
    path = pathlib.Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = payload.get("schema", "")
    if not schema.startswith("ada.cae_displacements/"):
        raise AbaqusFailed(
            "{0} is not a CAE displacement sidecar (schema {1!r}). The emitted script writes one next "
            "to itself when it is asked to submit the job.".format(path, schema)
        )
    instances = payload.get("instances") or []
    names = [entry["name"] for entry in instances]
    if instance is None:
        if len(instances) != 1:
            raise AbaqusFailed(
                "{0} carries {1} part instance(s) ({2}), and node labels in an ODB are numbered per "
                "instance -- so which node 7 a table meant would be unanswerable. Name one with "
                "instance=, or compare one part at a time.".format(path, len(instances), names)
            )
        entry = instances[0]
    else:
        matches = [candidate for candidate in instances if candidate["name"] == instance]
        if not matches:
            raise AbaqusFailed("{0} has no instance {1!r}; it carries {2}".format(path, instance, names))
        entry = matches[0]

    nodes = entry["nodes"]
    mesh = _Mesh(_Nodes([row[0] for row in nodes], [row[1:4] for row in nodes]))

    components = tuple(payload["components"])
    if len(components) != 6:
        raise AbaqusFailed(
            "{0} states {1} component(s) ({2}) and a DisplacementTable holds six. Abaqus splits them "
            "across the ODB's 'U' and 'UR' fields and the emitted script joins them, so a sidecar with "
            "three has lost the rotations.".format(path, len(components), list(components))
        )
    fields: dict[str, list[_Field]] = {"U": []}
    step_names = []
    for index, step in enumerate(entry["steps"]):
        step_names.append(step["name"])
        fields["U"].append(_Field(components, step["displacements"], index))

    return CaeDisplacementResult(
        mesh,
        fields,
        results_file_path=str(path),
        solver_version=payload.get("solver_version", ""),
        element_type=payload.get("element_type", ""),
        instance=entry["name"],
        step_names=step_names,
        job=payload.get("job", ""),
    )


def sample_abaqus_result(
    result,
    *,
    solver_version: str,
    step: int | None = None,
) -> DisplacementTable:
    """Sample an adapy ``FEAResult`` read from an Abaqus ODB at the model's probe points.

    Exists so the Abaqus half does not grow its own node-matching: this is the same
    :func:`displacements.sample_fea_result` the Sestra half uses, so both sides resolve
    probes by the identical rule.
    """
    return sample_fea_result(
        result,
        model.PROBE_POINTS,
        solver="abaqus",
        solver_version=solver_version,
        model_name="portal_frame",
        load_case=model.LOAD_CASE,
        step=step,
    )


def abaqus_displacements(
    sidecar: str | pathlib.Path,
    *,
    step: int | None = None,
    solver_version: str | None = None,
) -> DisplacementTable:
    """Read a CAE displacement sidecar and sample it at :data:`model.PROBE_POINTS`.

    The Abaqus counterpart of :func:`sestra_runner.sestra_displacements`, and symmetric with it
    on purpose: both end in :func:`displacements.sample_fea_result`. The element code travels in
    ``solver_version`` because it decides the physics of the answer, so a report that does not
    name it is missing the one thing that explains a residual.
    """
    result = read_displacements_sidecar(sidecar)
    version = solver_version or result.solver_version or "unknown"
    if result.element_type:
        version = "{0} {1}".format(version, result.element_type)
    return sample_abaqus_result(result, solver_version=version, step=step)


def emit_and_run(
    work_dir: str | pathlib.Path,
    *,
    case_name: str = "portal",
    element_type: str = DEFAULT_ELEMENT_TYPE,
    mesh_size: float | None = None,
) -> pathlib.Path:
    """Write the CAE script for the portal frame, run it, and return its displacement sidecar.

    ``model.build_portal_frame()`` and no local model: a comparison between two hand-built
    structures would prove nothing about a translation.
    """
    # Imported here so that reading a sidecar from an earlier run does not need the test package
    # on the path. That package owns the launcher search, the timeout and the sanitised child
    # environment -- a relative PYTHONPATH inherited by the child kills job.submit() with a bare
    # "Abaqus Error" and no traceback, measured -- and duplicating any of it here would be a
    # second thing to keep in step.
    try:
        from tests.core.cadit.cae.abaqus_runner import abaqus_command, run_cae_script
    except ImportError as exc:  # pragma: no cover - depends on how this is invoked
        raise AbaqusNotInstalled(
            "could not import tests.core.cadit.cae.abaqus_runner, which owns the Abaqus launcher "
            "search and the sanitised child environment this needs: {0}. Run this from the "
            "repository root, e.g. 'python -m verification.genie_vs_abaqus.abaqus_runner'.".format(exc)
        ) from exc

    if abaqus_command() is None:
        raise AbaqusNotInstalled(
            "no Abaqus install found. tests.core.cadit.cae.abaqus_runner looks for "
            "C:/SIMULIA/Commands/abqXXXX.bat and honours ADA_ABAQUS_CMD -- set that to point at the "
            "command script."
        )

    work_dir = pathlib.Path(work_dir)
    run_dir = work_dir / "{0}_abaqus_{1}".format(case_name, element_type.lower())
    run_dir.mkdir(parents=True, exist_ok=True)

    assembly = model.build_portal_frame()
    part = assembly.get_by_name("PortalFrame")
    # The probes are the comparison. adapy's own mesh is checked here for the same reason the
    # Sestra runner checks it; the Abaqus mesh is checked by the sampler, which raises
    # ProbeNotFound rather than settling for the nearest node.
    model.assert_probes_are_seeded(part.fem)

    script = run_dir / "{0}.py".format(case_name)
    # The assembly, not the part: the two Bc records are on the part's FEM while the step
    # carrying the loads is on the assembly's, so writing the part alone would emit the
    # supports and refuse for want of the step -- see ada.cadit.cae.analysis.analysis_fems.
    assembly.to_abaqus_cae_script(
        script,
        mesh_size=model.MESH_SIZE if mesh_size is None else mesh_size,
        element_type=element_type,
        job_name=case_name,
        submit=True,
    )

    run = run_cae_script(script, run_dir, timeout=RUN_TIMEOUT)
    if run.licence_denied:
        raise AbaqusNotInstalled("Abaqus is installed but no CAE licence was available:\n" + run.stdout)
    if run.failed:
        raise AbaqusFailed(
            "the emitted CAE script did not run cleanly, so there is no result to compare. "
            "Signals: {0}\n{1}".format(run.failure_signals, run.describe())
        )

    sidecar = run_dir / "{0}{1}".format(script.stem, DISPLACEMENTS_SUFFIX)
    if not sidecar.is_file():
        raise AbaqusFailed(
            "the run reported success and left no displacement sidecar at {0}. The script writes one "
            "only when it was asked to submit the job; read {1} for what it did.".format(
                sidecar, run_dir / "{0}.cae_build_result.json".format(script.stem)
            )
        )
    return sidecar


def run_and_sample(
    work_dir: str | pathlib.Path,
    *,
    case_name: str = "portal",
    element_type: str = DEFAULT_ELEMENT_TYPE,
    mesh_size: float | None = None,
) -> DisplacementTable:
    """:func:`emit_and_run` then :func:`abaqus_displacements`. The whole Abaqus half."""
    return abaqus_displacements(
        emit_and_run(work_dir, case_name=case_name, element_type=element_type, mesh_size=mesh_size)
    )


def load_abaqus_table(path: str | pathlib.Path) -> DisplacementTable:
    """Read a JSON :class:`displacements.DisplacementTable` produced out of process.

    Checks the probe set here rather than leaving it to the comparator, so a hand-written or
    script-generated file fails at the point it is read with a message naming what is missing.
    """
    table = DisplacementTable.from_json(path)
    expected = {p.name for p in model.PROBE_POINTS}
    missing = sorted(expected - set(table.displacements))
    extra = sorted(set(table.displacements) - expected)
    if missing or extra:
        raise ValueError(
            f"{path}: the table does not cover model.PROBE_POINTS. Missing: {missing or '(none)'}. "
            f"Unexpected: {extra or '(none)'}. Every probe must be present -- a partial table is "
            f"rejected by design, see compare.assert_same_probes."
        )
    for name, values in table.displacements.items():
        if len(values) != 6:
            raise ValueError(f"{path}: probe {name} has {len(values)} components, expected 6 (u1..u3, r1..r3)")
    return table


def write_template_json(path: str | pathlib.Path) -> pathlib.Path:
    """Write a zero-filled :class:`displacements.DisplacementTable` to work from.

    Note that the template as written will *not* pass: :func:`compare.assert_has_signal`
    rejects an all-zero table. That is intentional -- a skeleton that passed until filled in
    would be indistinguishable from a solve that lost its loads.
    """
    table = DisplacementTable(
        solver="abaqus",
        solver_version="FILL ME IN",
        model="portal_frame",
        load_case=model.LOAD_CASE,
        displacements={p.name: (0.0,) * 6 for p in model.PROBE_POINTS},
        node_ids={p.name: 0 for p in model.PROBE_POINTS},
        node_coords={p.name: p.xyz for p in model.PROBE_POINTS},
        source="FILL ME IN: path to the .odb",
    )
    return table.to_json(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abaqus_runner",
        description="Solve the portal frame in Abaqus and write the DisplacementTable run_comparison reads.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--work-dir", default="temp/genie_vs_abaqus", help="where to emit the script and run Abaqus")
    parser.add_argument("--case-name", default="portal", help="script / job name (default: portal)")
    parser.add_argument(
        "--element-type",
        default=DEFAULT_ELEMENT_TYPE,
        help="B31, B32 or B33 (default: {0}; this module's docstring says what each answers)".format(
            DEFAULT_ELEMENT_TYPE
        ),
    )
    parser.add_argument(
        "--mesh-size",
        type=float,
        default=None,
        help="element seed, metres (default: model.MESH_SIZE, which seeds every probe as a node)",
    )
    parser.add_argument("--sidecar", help="read this existing <stem>.cae_displacements.json instead of re-solving")
    parser.add_argument("--json-out", help="where to write the table (default: <work-dir>/abaqus_<elem>.json)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the Abaqus half's DisplacementTable. ``0`` it was written, ``2`` it could not be."""
    args = build_parser().parse_args(argv)
    try:
        if args.sidecar:
            sidecar = pathlib.Path(args.sidecar)
        else:
            sidecar = emit_and_run(
                args.work_dir,
                case_name=args.case_name,
                element_type=args.element_type,
                mesh_size=args.mesh_size,
            )
        table = abaqus_displacements(sidecar)
    except (AbaqusNotInstalled, AbaqusFailed, OSError, ValueError) as exc:
        print("ERROR: the Abaqus side could not be run: {0}".format(exc), file=sys.stderr)
        return 2

    out = args.json_out or str(pathlib.Path(args.work_dir) / "abaqus_{0}.json".format(args.element_type.lower()))
    written = table.to_json(out)
    print("Abaqus {0} -- {1}".format(table.solver_version, table.source))
    header = "{0:<13} {1:>6} ".format("probe", "node") + " ".join("{0:>14}".format(c) for c in COMPONENTS)
    print(header)
    print("-" * len(header))
    for probe in model.PROBE_POINTS:
        values = table.displacements[probe.name]
        print(
            "{0:<13} {1:>6} ".format(probe.name, table.node_ids.get(probe.name, 0))
            + " ".join("{0:>14.6e}".format(v) for v in values)
        )
    print("\nwrote {0}".format(written))
    print(
        "now: python -m verification.genie_vs_abaqus.run_comparison --work-dir {0} "
        "--abaqus-json {1}".format(args.work_dir, written)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
