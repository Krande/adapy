"""The Sestra half: adapy model -> Sesam FEM -> ``Sestra.exe`` -> ``.SIN`` -> displacements.

Sestra is located through adapy's own locator
(:func:`ada.fem.formats.sesam.sesam_exe_locator.get_sestra_default_exe_path`, which reads
DNV's ``ApplicationVersions.xml`` and honours ``ADA_SESTRA_EXE``), never a hardcoded path,
so this runs on any machine with a registered install. Measured on this one: Sestra
V11.3-00, build 11.3.0.105.

What adapy can and cannot write into a Sesam deck
=================================================

Measured on the deck this runner produces, not inferred from the source.

**Supported, and verified against Sestra's own load summary:**

* *Fully fixed supports.* ``ada.fem.Bc`` with dofs ``[1..6]`` becomes a ``BNBCD`` record
  with FIX code 1 on all six. Sestra's ``.MLG`` reported "with boundary condition: 12" for
  the frame's two fixed bases -- the 12 DOFs it should be.
* *Nodal point forces.* ``ada.fem.Load`` of type ``force`` becomes ``BNLOAD``. Sestra's
  ``.LIS`` load sum for the frame reads ``tx = 1.0000e+04``, ``ry = 6.0000e+04``, which is
  the applied 10 kN and its 6 m lever arm exactly, and the reaction sum cancels it to
  4.0e-07 N. The load arrives intact.
* *Gravity / acceleration.* ``LoadGravity`` becomes ``BGRAV``.
* *Linear static and eigenvalue steps.* ``write_steps.write_sestra_inp`` emits a
  ``sestra.inp`` for ``StepImplicitStatic`` and ``StepEigen``.

**Gaps, each of which constrains what this comparison can be:**

1. *No prescribed (non-zero) support displacement.* ``write_bcs`` defines ``PRESCRIBED = 2``
   and its own comment says it is "never written: ada's Bc magnitudes are not carried into
   BNDISPL yet". Any named DOF is written as fully fixed regardless of magnitude, so a
   settlement or imposed-displacement load case cannot be expressed. That is why the model
   uses fixed bases and nodal forces only.
2. *A force load applies to one node, silently.* ``write_loads.load_force`` reads
   ``load.fem_set.members[0].id`` and ignores every other member of the set. A ``Load`` over
   a two-node set therefore writes half the intended total load with no warning. This is a
   trap, not a limitation -- the model works around it by declaring one ``Load`` per node
   (see :func:`model.build_portal_frame`) -- but it is worth reporting as a defect in
   ``src``: the writer should either loop the members or raise.
3. *No distributed element load.* Only ``force`` and ``gravity``/``acc`` reach the writer.
   ``load_str`` logs "Unsupported Load type" and falls off the end returning ``None``, which
   ``loads_str`` then tries to concatenate: measured, a pressure load raises
   ``TypeError: can only concatenate str (not "NoneType") to str``. So it does fail rather
   than write a silently wrong deck, but it fails as a type error several frames away from
   the cause instead of as the ``UnsupportedLoadType`` that ``ada.fem.exceptions`` already
   defines. Either way a beam UDL (``BELOAD``/``BEUSLO``) cannot be written, so "uniform load
   on the girder" is not available to this comparison.
4. *Only the first step.* Multi-step decks log an error and write step 1.
5. *Only one part.* ``to_fem`` raises ``DoesNotSupportMultiPart`` unless the caller has
   already merged.

None of 1, 3, 4 or 5 blocks a portal frame with fixed bases and point loads, which is why
that is the model.
"""

from __future__ import annotations

import pathlib
import shutil

from ada.fem.formats.sesam.results.read_sin import read_sin_file
from ada.fem.formats.sesam.sesam_exe_locator import (
    get_sestra_default_exe_path,
    get_sestra_version,
)

from . import model
from .displacements import DisplacementTable, sample_fea_result

#: The line the Sestra message log writes on a clean run. Its absence is treated as a
#: failure even when a ``.SIN`` exists, because a partial ``.SIN`` from an aborted
#: factorisation is exactly the artefact that would otherwise be read as a result.
SUCCESS_MARKER = "Execution completed successfully"


class SestraNotInstalled(RuntimeError):
    """adapy's locator found no Sestra install."""


class SestraFailed(RuntimeError):
    """Sestra ran but did not report success, or wrote no result file."""


def sestra_exe() -> str:
    """Sestra's path from adapy's locator. Raises :class:`SestraNotInstalled` if absent."""
    exe = get_sestra_default_exe_path()
    if exe is None or not pathlib.Path(exe).is_file():
        raise SestraNotInstalled(
            "adapy's locator found no Sestra executable. It reads DNV's "
            "%APPDATA%/DNVGL/ApplicationVersionManager/ApplicationVersions.xml and honours "
            "the ADA_SESTRA_EXE environment variable -- set that to override."
        )
    return exe


def run_sestra(work_dir: str | pathlib.Path, *, case_name: str = "portal", clean: bool = True) -> pathlib.Path:
    """Write the Sesam deck for :func:`model.build_portal_frame`, solve it, return the SIN.

    Delegates the solve to ``Assembly.to_fem(..., "sesam", execute=True)``, which finds the
    executable through the same locator and runs it with the deck directory as the working
    directory. The success of that call is then *verified* rather than trusted: adapy's
    ``LocalExecute`` shells out through a batch file and does not surface a non-zero solver
    exit, so this checks for the ``.SIN`` and for :data:`SUCCESS_MARKER` in ``SESTRA.MLG``.
    """
    exe = sestra_exe()
    version = get_sestra_version(exe)

    work_dir = pathlib.Path(work_dir)
    deck_dir = work_dir / case_name
    if clean and deck_dir.exists():
        shutil.rmtree(deck_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    assembly = model.build_portal_frame()
    part = assembly.get_by_name("PortalFrame")
    # The probes are the comparison. Confirm the mesh actually has a node at each of them
    # before spending a solve on it.
    model.assert_probes_are_seeded(part.fem)

    assembly.to_fem(case_name, "sesam", scratch_dir=work_dir, overwrite=True, execute=True)

    sin_path = deck_dir / f"{case_name}R1.SIN"
    mlg_path = deck_dir / "SESTRA.MLG"
    if not sin_path.is_file():
        raise SestraFailed(
            f"Sestra ({version}, {exe}) produced no result file at {sin_path}. "
            f"Message log: {mlg_path if mlg_path.is_file() else '(none written)'}"
        )
    if mlg_path.is_file():
        mlg = mlg_path.read_text(encoding="utf-8", errors="replace")
        if SUCCESS_MARKER not in mlg:
            raise SestraFailed(
                f"Sestra ({version}) wrote {sin_path.name} but its message log does not contain "
                f"'{SUCCESS_MARKER}'. A partial .SIN from an aborted run must not be read as a "
                f"result. Log tail:\n{mlg[-2000:]}"
            )
    return sin_path


def sestra_displacements(sin_path: str | pathlib.Path, *, step: int | None = None) -> DisplacementTable:
    """Read a Sestra ``.SIN`` and sample it at :data:`model.PROBE_POINTS`.

    Uses ``read_sin_file`` -- the pure-Python Norsam binary reader, no Prepost shell-out.
    Measured field inventory for this frame's result file: ``RVNODDIS`` (six components
    ``U1..U6``, 21 rows), ``REACTION-FORCE``, ``FORCES``, plus the derived
    ``sesam.*`` views. The sampler resolves components by name, so it uses whichever of
    those is present.

    One thing worth knowing about the numbers that come back: a ``.SIN`` stores results as
    **single-precision** floats. The 24.7 mm sway therefore carries about seven significant
    digits, an absolute resolution near 2.9e-9 m -- some 340x finer than
    :data:`compare.ABS_FLOOR`, so it never limits the comparison, but it does mean the last
    couple of printed digits are storage noise rather than solver output.
    """
    sin_path = pathlib.Path(sin_path)
    result = read_sin_file(sin_path, step=step)
    return sample_fea_result(
        result,
        model.PROBE_POINTS,
        solver="sestra",
        solver_version=get_sestra_version(sestra_exe()),
        model_name="portal_frame",
        load_case=model.LOAD_CASE,
        step=step,
    )


def run_and_sample(work_dir: str | pathlib.Path, *, case_name: str = "portal") -> DisplacementTable:
    """:func:`run_sestra` then :func:`sestra_displacements`. The whole Sestra half."""
    return sestra_displacements(run_sestra(work_dir, case_name=case_name))
