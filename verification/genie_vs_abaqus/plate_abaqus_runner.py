"""The Abaqus half of the plate case: the same model -> a CAE script -> ``S4R`` -> a table.

Built by ``Part.to_abaqus_cae_script`` from :func:`plate_model.build_strip`, the same function
the Sestra half calls. What the writer carries for this model, and what it does not, is measured
rather than assumed -- and the one thing it does not carry is the reason this module emits a
driver at all.

What the writer translates, verified in the emitted script and its sidecar
========================================================================

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
* the ``StaticStep``, and a ``FieldOutputRequest`` already naming ``U``, ``UR`` and ``RF``.

The gap, reproduced: a support on a plate edge is refused
=========================================================

``ada.cadit.cae.analysis._resolve_region`` resolves a ``FemSet``'s node positions against the
**geometric vertices** of the emitted model -- the plate's corners and the members' ends -- because
that is what survives a re-mesh. The interior nodes of a plate edge are not vertices, so this
model's three supports cannot be written. Reproduced on demand by
:func:`reproduce_edge_support_refusal`, which needs no Abaqus and no licence -- verbatim, on the
bare strip at a 0.125 seed::

    ada.cadit.cae.writer.CaeWriteError: boundary condition 'CYL' (on Strip's FEM) acts at
    (0.125, 0.0, 0.0) through the set 'CYL', and the emitted geometry has no vertex there -- the
    nearest vertex is at (0.0, 0.0, 0.0) in part instance 'Strip-1', 0.125 length units away.
    CAE carries a support or a load on a geometric vertex, which is what survives re-meshing;
    there is no vertex partway along a member [...]

That is a refusal by name with the measurement in it, which is the behaviour this project asks
for -- not a silent drop -- and it is a **capability gap, not a defect**: nothing is written
wrongly. It is reported as such, and not fixed here, because ``src/ada/cadit/cae`` belongs to
another workstream.

So the three supports are emitted by :func:`support_driver`, appended to the writer's own script
in the manner ``tests/core/cadit/cae/test_cae_licensed_acceptance.py`` already uses for the
kernel probes. Two things keep that from becoming a second, divergent model:

1. the driver is **generated from the model's own** :class:`ada.fem.Bc` **records**, through
   ``ada.cadit.cae.analysis.BC_KEYWORDS`` -- the same six-slot DOF order and the same keyword
   spellings the writer itself would have used. "Simply supported" therefore means one thing,
   written once, in :data:`plate_model.EDGE_SUPPORTS`;
2. the regions are **geometry** edges found by ``getByBoundingBox``, not mesh nodes, so they are
   mesh-independent across the three densities in the same way the writer's own regions are.
   Measured, the boxes find 1 edge each on the bare strip and 2 on each supported edge of the
   stiffened one (the bar splits them) -- printed as ``PROBE edge_counts`` and asserted.

The driver then submits the job, and writes the displacement sidecar in the writer's own
``ada.cae_displacements/1`` schema so :func:`abaqus_runner.read_displacements_sidecar` reads it
unchanged -- including the join across ``U`` and ``UR``, which Abaqus stores as two separate
fields (measured on Abaqus 2025: ``U`` has ``componentLabels ('U1', 'U2', 'U3')``).

The load: the writer's own pressure, and why it is the same vector as Sestra's
=============================================================================

This side is given the ``pressure`` load, which the writer turns into an assembly ``Surface`` over
the plate's faces and a ``Pressure`` on it (``*Dsload``) -- ``side1Faces``, so a positive magnitude
pushes against the plate's declared ``+z`` normal, i.e. in ``-z``, which is the sign the Sestra
side's negative nodal forces have.

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

from ada.cadit.cae.analysis import BC_KEYWORDS

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

#: The job the appended driver submits, and the stem of the sidecars it leaves.
JOB_NAME = "plate_job"

#: What the driver names its displacement sidecar -- the writer's own schema, read by
#: :func:`abaqus_runner.read_displacements_sidecar`.
DISPLACEMENTS_NAME = "plate.cae_displacements.json"

#: And its own small provenance sidecar: the reaction total, the element counts, the node count
#: and the stringer names. Separate from the displacements so a reader can see what the run was
#: without parsing 2193 rows.
CHECKS_NAME = "plate.cae_checks.json"

#: Half-width of the bounding boxes the driver finds the supported edges with, in metres.
#:
#: 1e-07 -- a round-off tolerance and not a search radius, for the reason
#: :data:`displacements.MATCH_TOL` gives: the coarsest feature it must not reach across is the
#: 0.03125 m finest element, 300000x larger.
EDGE_BOX_TOL = 1.0e-07


@dataclass(frozen=True)
class PlateRunChecks:
    """What the driver measured about the model it solved, read back from :data:`CHECKS_NAME`."""

    reaction_total: tuple[float, float, float]
    node_count: int
    element_counts: dict[str, int]
    stringers: tuple[str, ...]
    edge_counts: tuple[int, ...]
    solved: bool


def support_driver() -> str:
    """The CAE code that puts :data:`plate_model.EDGE_SUPPORTS` on the plate's edges, and solves.

    Generated from the model's own ``Bc`` records and ``analysis.BC_KEYWORDS``, so the DOF order
    and the keyword spellings are the writer's rather than a second opinion about them -- see the
    module docstring. Everything else in the deck is the writer's.

    The returned text is Abaqus-kernel Python (2.7): ``.format`` rather than f-strings, no
    annotations, and ``print`` going to ``abaqus.rpy`` rather than stdout.
    """
    statements = []
    for set_name, dofs, why in plate_model.EDGE_SUPPORTS:
        keywords = ", ".join("{0}=0.0".format(BC_KEYWORDS[dof - 1]) for dof in sorted(dofs))
        statements.append("    # {0}: {1}".format(set_name, why))
        statements.append(
            "    _m.DisplacementBC(name={0!r}, createStepName='Initial', "
            "region=_a.sets[{1!r}], {2})".format(set_name, _cae_set_name(set_name), keywords)
        )
    return _DRIVER_TEMPLATE.format(
        length=repr(float(plate_model.STRIP_LENGTH)),
        width=repr(float(plate_model.STRIP_WIDTH)),
        tol=repr(float(EDGE_BOX_TOL)),
        job=repr(JOB_NAME),
        displacements=repr(DISPLACEMENTS_NAME),
        checks=repr(CHECKS_NAME),
        part=repr(plate_model.PART_NAME),
        instance=repr("{0}-1".format(plate_model.PART_NAME)),
        step=repr(plate_model.STEP_NAME),
        shell_code=repr(DEFAULT_SHELL_ELEMENT),
        bcs="\n".join(statements),
        x0=_cae_set_name("SS_X0"),
        x1=_cae_set_name("SS_X1"),
        cyl=_cae_set_name("CYL"),
    )


def _cae_set_name(set_name: str) -> str:
    """The assembly-level set the driver builds for one support, lower-cased and suffixed.

    Distinct from the adapy ``FemSet`` name so that nothing in the emitted script can collide
    with a name the writer's own ``_guard_no_name_collisions`` already reserved.
    """
    return "{0}_edges".format(set_name.lower())


#: The driver, with ``{...}`` slots filled by :func:`support_driver`. Doubled braces are literal.
_DRIVER_TEMPLATE = '''

# --------------------------------------------------------------------------------------------
# Appended by verification/genie_vs_abaqus/plate_abaqus_runner.py.
#
# The three supports below are the ones ada.cadit.cae.analysis refuses: it resolves a support to
# a geometric VERTEX, and the interior nodes of a plate edge are not vertices. The refusal is
# reproduced verbatim in that module's docstring. The DOF keywords here come from
# analysis.BC_KEYWORDS and the DOF lists from plate_model.EDGE_SUPPORTS, so this is the same
# statement of "simply supported" the Sestra deck is written from -- not a second one.
#
# The regions are GEOMETRY edges, not mesh nodes, so they mean the same thing at all three mesh
# densities.
# --------------------------------------------------------------------------------------------
import odbAccess

_JOB = {job}
_TOL = {tol}
_L = {length}
_B = {width}
_STEP = {step}
_m = mdb.models[MODEL_NAME]
_a = _m.rootAssembly
_p = _m.parts[{part}]
_inst = _a.instances[{instance}]


def _edges_in_box(xmin, xmax, ymin, ymax):
    """Every geometry edge wholly inside the box, or a failure naming the box.

    getByBoundingBox rather than findAt: a supported edge of the STIFFENED strip is two edges,
    because the bar splits the body along its axis, and findAt at one point would have located
    one of them and silently left the other unsupported -- half a simple support.
    """
    got = _inst.edges.getByBoundingBox(xMin=xmin, xMax=xmax, yMin=ymin, yMax=ymax,
                                       zMin=-_TOL, zMax=_TOL)
    if len(got) == 0:
        _fail('no geometry edge lies inside the box x[{{0}}, {{1}}] y[{{2}}, {{3}}]; the emitted '
              'part has {{4}} edge(s). Without it a support would be created on nothing and the '
              'solve would fail as a singular system rather than as a missing '
              'support.'.format(xmin, xmax, ymin, ymax, len(_inst.edges)))
    return got


_e_x0 = _edges_in_box(-_TOL, _TOL, -_TOL, _B + _TOL)
_e_x1 = _edges_in_box(_L - _TOL, _L + _TOL, -_TOL, _B + _TOL)
_e_y0 = _edges_in_box(-_TOL, _L + _TOL, -_TOL, _TOL)
_e_y1 = _edges_in_box(-_TOL, _L + _TOL, _B - _TOL, _B + _TOL)
_EDGE_COUNTS = [len(_e_x0), len(_e_x1), len(_e_y0), len(_e_y1)]
print('PROBE edge_counts {{0}}'.format(_EDGE_COUNTS))
_a.Set(name='{x0}', edges=_e_x0)
_a.Set(name='{x1}', edges=_e_x1)
_a.Set(name='{cyl}', edges=_e_y0 + _e_y1)


def _apply_supports():
{bcs}


_apply_supports()

_counts = {{}}
for _el in _p.elements:
    _key = str(_el.type)
    _counts[_key] = _counts.get(_key, 0) + 1
print('PROBE element_counts {{0!r}}'.format(_counts))
print('PROBE node_count {{0}}'.format(len(_p.nodes)))
print('PROBE stringers {{0!r}}'.format(sorted(_p.stringers.keys())))

_job = mdb.Job(name=_JOB, model=MODEL_NAME)
_job.submit(consistencyChecking=OFF)
_job.waitForCompletion()
# NOT job.status: measured on Abaqus 2025 it reads None after waitForCompletion under
# `abaqus cae noGUI=`, so a check against COMPLETED fails every clean run. The .sta says so.
_STA = os.path.join(os.path.dirname(_result_path()), _JOB + '.sta')
_solved = False
if os.path.isfile(_STA):
    _handle = open(_STA, 'r')
    _solved = 'THE ANALYSIS HAS COMPLETED SUCCESSFULLY' in _handle.read()
    _handle.close()
print('PROBE solved {{0}}'.format(_solved))
if not _solved:
    _fail('the plate job did not complete successfully; {{0}} does not carry Abaqus\\' own '
          'completion line, so there is no result to sample.'.format(_JOB + '.sta'))

_odb = odbAccess.openOdb(_JOB + '.odb')
_payload = {{'schema': 'ada.cae_displacements/1', 'job': _JOB, 'odb': _JOB + '.odb',
            'model': MODEL_NAME, 'element_type': {shell_code},
            'components': ['U1', 'U2', 'U3', 'UR1', 'UR2', 'UR3'],
            'solver_version': str(_odb.jobData.version), 'instances': []}}
for _iname in sorted(_odb.rootAssembly.instances.keys()):
    _oi = _odb.rootAssembly.instances[_iname]
    _nodes = []
    for _nd in _oi.nodes:
        _c = _nd.coordinates
        _nodes.append([int(_nd.label), float(_c[0]), float(_c[1]), float(_c[2])])
    _nodes.sort()
    _steps = []
    for _sname in sorted(_odb.steps.keys()):
        _st = _odb.steps[_sname]
        _fr = _st.frames[-1]
        _rows = {{}}
        # Abaqus stores the six components in TWO fields -- U is ('U1','U2','U3') and the
        # rotations are a separate UR -- so they are joined here, once, as the writer's own
        # sidecar does. A reader expecting six in one place finds three.
        for _fname, _offset in (('U', 0), ('UR', 3)):
            if _fname not in _fr.fieldOutputs.keys():
                _fail('step {{0!r}} carries no {{1!r}} field, so its components cannot be '
                      'reported.'.format(_sname, _fname))
            _sub = _fr.fieldOutputs[_fname].getSubset(region=_oi)
            for _v in _sub.values:
                _lab = int(_v.nodeLabel)
                _row = _rows.get(_lab)
                if _row is None:
                    _row = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                    _rows[_lab] = _row
                for _axis in range(3):
                    _row[_offset + _axis] = float(_v.data[_axis])
        _table = []
        for _lab in sorted(_rows.keys()):
            _table.append([_lab] + _rows[_lab])
        _steps.append({{'name': _sname, 'frame': len(_st.frames) - 1,
                       'time': float(_fr.frameValue), 'displacements': _table}})
    _payload['instances'].append({{'name': _iname, 'nodes': _nodes, 'steps': _steps}})

# The solver's own reaction bookkeeping. adapy computed the applied pressure, so comparing the
# applied load against itself would prove nothing; this is what says the whole of it arrived.
_rf = [0.0, 0.0, 0.0]
_frame = _odb.steps[_STEP].frames[-1]
for _v in _frame.fieldOutputs['RF'].values:
    for _axis in range(3):
        _rf[_axis] = _rf[_axis] + float(_v.data[_axis])
print('PROBE reaction_total {{0!r}}'.format(_rf))
_odb.close()

_handle = open(os.path.join(os.path.dirname(_result_path()), {displacements}), 'w')
json.dump(_payload, _handle)
_handle.close()
_handle = open(os.path.join(os.path.dirname(_result_path()), {checks}), 'w')
json.dump({{'schema': 'ada.plate_checks/1', 'reaction_total': _rf, 'node_count': len(_p.nodes),
           'element_counts': _counts, 'stringers': sorted(_p.stringers.keys()),
           'edge_counts': _EDGE_COUNTS, 'solved': _solved}}, _handle)
_handle.close()
print('ADAPY-PLATE SOLVE OK')
sys.stdout.flush()
'''


def reproduce_edge_support_refusal(destination: str | pathlib.Path, *, mesh_size: float = 0.125) -> str:
    """Ask the CAE writer for a support on a plate edge and return what it says about it.

    Needs no Abaqus and no licence: the refusal happens at plan time, in
    ``ada.cadit.cae.analysis.plan_analysis``, before a file is written. So the gap this module's
    docstring describes is **measured on demand** rather than remembered, and the day the writer
    grows edge regions this function stops raising -- at which point the driver below can be
    deleted and the supports translated like everything else.

    Returns the ``CaeWriteError``'s message. Raises :class:`AssertionError` if the writer *accepts*
    the model, because that is the interesting outcome and it must not be reported as a pass.
    """
    from ada.cadit.cae.writer import CaeWriteError

    assembly = plate_model.build_strip(mesh_size, stiffened=False, route="abaqus", with_edge_supports=True)
    try:
        assembly.to_abaqus_cae_script(
            pathlib.Path(destination), mesh_size=mesh_size, shell_element_type=DEFAULT_SHELL_ELEMENT
        )
    except CaeWriteError as exc:
        return str(exc)
    raise AssertionError(
        "the CAE writer accepted a Bc on this strip's supported edge, which it refused when this "
        "module was written (see its docstring). That is good news and it means this package is now "
        "out of date: the three supports can be translated like everything else and "
        "plate_abaqus_runner.support_driver should go."
    )


def emit_and_run(
    work_dir: str | pathlib.Path,
    *,
    mesh_size: float,
    stiffened: bool,
    shell_element_type: str = DEFAULT_SHELL_ELEMENT,
) -> pathlib.Path:
    """Write the CAE script for one variant at one seed, run it, and return its run directory.

    ``submit=False`` on purpose: the writer's own ``solve()`` would submit the job *before* the
    appended driver could create a single support, and an unsupported plate is a singular system.
    So the driver submits, and it writes the writer's own sidecar schema on the way out.
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
    script = run_dir / "plate.py"
    # The assembly, not the part: the supports are on the part's FEM while the step carrying the
    # pressure is on the assembly's, so writing the part alone would refuse for want of the step.
    assembly.to_abaqus_cae_script(
        script,
        mesh_size=mesh_size,
        shell_element_type=shell_element_type,
        submit=False,
    )
    with script.open("a", encoding="utf-8") as handle:
        handle.write(support_driver())

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
    if "ADAPY-PLATE SOLVE OK" not in run.printed:
        raise AbaqusFailed(
            "the build reported success but the appended solve driver did not finish: no "
            "'ADAPY-PLATE SOLVE OK' in the replay output for the {0} strip at seed {1}. The "
            "writer's own build guards passed, so this is the supports, the job or the ODB "
            "read.\n{2}".format("stiffened" if stiffened else "bare", mesh_size, run.describe())
        )
    return run_dir


def read_checks(run_dir: str | pathlib.Path) -> PlateRunChecks:
    """Read :data:`CHECKS_NAME` from a run directory.

    Refused rather than defaulted when absent: the reaction total is the check that the whole
    pressure arrived, and a missing file would otherwise become a silently skipped check.
    """
    path = pathlib.Path(run_dir) / CHECKS_NAME
    if not path.is_file():
        raise AbaqusFailed(
            "no {0} in {1}. The appended driver writes it whenever it solved, so its absence means "
            "the solve did not reach the end -- and the reaction total, which is what says the "
            "whole pressure arrived, cannot be checked.".format(CHECKS_NAME, run_dir)
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "ada.plate_checks/1":
        raise AbaqusFailed("{0} is not a plate-checks sidecar (schema {1!r})".format(path, payload.get("schema")))
    total = [float(v) for v in payload["reaction_total"]]
    return PlateRunChecks(
        reaction_total=(total[0], total[1], total[2]),
        node_count=int(payload["node_count"]),
        element_counts={str(k): int(v) for k, v in sorted(payload["element_counts"].items())},
        stringers=tuple(payload["stringers"]),
        edge_counts=tuple(int(v) for v in payload["edge_counts"]),
        solved=bool(payload["solved"]),
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
    parser.add_argument(
        "--reproduce-refusal",
        action="store_true",
        help="build the model WITH its Bc records, hand it to the CAE writer, and print the refusal "
        "this module's driver exists because of. Needs no Abaqus and no licence",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Solve the Abaqus side and write its tables. ``0`` written, ``2`` could not be run."""
    args = build_parser().parse_args(argv)
    if args.reproduce_refusal:
        target = pathlib.Path(args.work_dir) / "refusal" / "plate.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        print("ada.cadit.cae.writer.CaeWriteError:")
        print(reproduce_edge_support_refusal(target))
        return 0
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
