"""The Sestra half of the plate case: adapy shell model -> Sesam deck -> ``.SIN`` -> a table.

Same locator, same success check and same sampler as :mod:`sestra_runner` -- this module adds
only what shells and a distributed load need. Read that module's gap list first; the three
findings below are what this case ran into, all measured on the deck this runner writes.

What reaches Sestra, measured on this model's own deck
=====================================================

**Shells arrive as ``FQUS``.** ``write_elements.eltype_2_sesam`` maps a quadrilateral shell to
Sesam element type ``24``, and every ``GELMNT1`` record in the deck carries
``2.40000000E+01``: 128 / 512 / 2048 of them at the three seeds. ``FTRS`` (25) is the
triangular form and none is written, because the model is meshed with ``use_quads=True`` --
which matters, since the closed form is for a plate and a triangulated mesh would be a
different discretisation of it on one side only. Sestra solves them with
``Execution completed successfully`` and its message log reports no singularity beyond the
usual coefficients.

**The stiffener shares the shells' nodes.** Checked in the adapy FEM object before the deck is
written (:func:`plate_model.assert_stiffener_shares_nodes`): 33 / 65 / 129 nodes on the
stiffener line, every one of them used by a shell *and* a beam element, and the mesh gains no
nodes at all for the bar -- 165 / 585 / 2193 with and without it. So the ``BEAS`` elements are
attached rather than merely present, and the measured stiffness ratio (0.3762 against the
parallel-spring 0.376156) says so downstream too.

**A pressure load reaches the deck as nothing.** This is the finding this case turned up, and
it is a gap in ``src`` reported rather than fixed here:

    ada.fem.formats.sesam.write.write_loads.load_str
      -> rep.omitted(STAGE, "Load", name, 'a "pressure" load is not written by the Sesam writer')
      -> return ""

There is no ``BEUSLO`` and no ``BELOAD`` anywhere in ``ada/fem/formats/sesam/write``, so
*every* distributed load is in the same position. The report line is logged at WARNING and the
deck is written and solved regardless -- which is the dangerous part: a Sestra run of a
pressure-loaded model completes successfully and returns an **unloaded** structure, and two
such runs would agree with each other perfectly. :func:`compare.assert_has_signal` is what
would catch it here, and only because it exists.

So this side is given the exact consistent nodal load instead
(:func:`plate_model.consistent_nodal_loads`), and the substitution is exact rather than
approximate: for a 4-node bilinear quad ``integral(N_i) dA = A / 4``, so ``q A / 4`` at each
node *is* the pressure's load vector, which is what Abaqus' own ``*Dsload`` assembles. The
evidence that nothing was lost in the swap is measured on both sides:

* three ``Load`` records (one per distinct tributary area) become **165 / 585 / 2193**
  ``BNLOAD`` records summing to ``-2000.000000`` N against ``q L b = -2000``;
* Sestra's own reaction total comes back ``(0, 0, 2000.0)`` N exactly on the bare strip and
  ``(0, 0, 1999.99996)`` on the stiffened one -- :func:`reaction_total`;
* and the Abaqus side, given the *pressure* rather than these forces, reacts the same 2000.0 in z
  (1999.999962 on the stiffened strip) with its in-plane components at 1e-13.

One correction to :mod:`sestra_runner`'s gap list, which was measured before this branch: gap
2 no longer holds. ``write_loads.load_force`` now loops ``for node in load.fem_set.members``
and writes a ``BNLOAD`` for every one of them, so a ``Load`` over a many-node set applies to
all of it. This case depends on that: three ``Load`` objects carry 2193 nodal forces at the
finest mesh.
"""

from __future__ import annotations

import collections
import pathlib
import shutil
from dataclasses import dataclass, field

import numpy as np

from ada.fem.formats.sesam.results.read_sin import read_sin_file
from ada.fem.formats.sesam.sesam_exe_locator import get_sestra_version

from . import plate_model
from .displacements import DisplacementTable, sample_fea_result
from .sestra_runner import SUCCESS_MARKER, SestraFailed, sestra_exe

#: The result card Sestra writes support reactions into, and the three columns that are forces.
REACTION_FIELD = "REACTION-FORCE"


@dataclass
class PlateSolve:
    """One solver, one variant, one mesh: the table plus what the run itself reported.

    The provenance travels with the table because a convergence study is only readable if each
    row says what it was a solve *of*. ``node_count`` and ``element_counts`` in particular are
    how the two solvers' meshes are shown to be the same grid -- measured, they are, at every
    density -- without that ever being assumed by the comparison, which still matches probes by
    position.
    """

    table: DisplacementTable
    mesh_size: float
    stiffened: bool
    #: The solver's own summed support reaction, ``(fx, fy, fz)`` newtons.
    reaction_total: tuple[float, float, float]
    node_count: int
    element_counts: dict[str, int] = field(default_factory=dict)
    source: str = ""

    @property
    def label(self) -> str:
        return f"{'stiffened' if self.stiffened else 'bare'} h={self.mesh_size}"


def model_name(stiffened: bool) -> str:
    """``"plate_strip"`` / ``"plate_strip_stiffened"`` -- what goes in the table's ``model``.

    Distinct per variant on purpose: the two variants must never be compared against each
    other by :func:`compare.compare`, and a table that names which one it is makes a mix-up
    visible in the printed report rather than only in the numbers.
    """
    return "plate_strip_stiffened" if stiffened else "plate_strip"


def run_sestra(
    work_dir: str | pathlib.Path,
    *,
    mesh_size: float,
    stiffened: bool,
    case_name: str | None = None,
    clean: bool = True,
) -> pathlib.Path:
    """Write the Sesam deck for one variant at one seed, solve it, return the ``.SIN``.

    Verified rather than trusted, for the reason :func:`sestra_runner.run_sestra` gives:
    adapy's ``LocalExecute`` shells out through a batch file and does not surface a non-zero
    solver exit, so the ``.SIN`` and the ``SESTRA.MLG`` success marker are both checked.
    """
    exe = sestra_exe()
    version = get_sestra_version(exe)

    work_dir = pathlib.Path(work_dir)
    case_name = case_name or default_case_name(mesh_size, stiffened)
    deck_dir = work_dir / case_name
    if clean and deck_dir.exists():
        shutil.rmtree(deck_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # "nodal": the Sesam writer has no distributed-load record at all -- see the module
    # docstring. build_strip's own guards check the mesh, the probes and the stiffener's
    # connectivity before a solve is spent on it.
    assembly = plate_model.build_strip(mesh_size, stiffened=stiffened, route="sestra")
    assembly.to_fem(case_name, "sesam", scratch_dir=work_dir, overwrite=True, execute=True)

    sin_path = deck_dir / f"{case_name}R1.SIN"
    mlg_path = deck_dir / "SESTRA.MLG"
    if not sin_path.is_file():
        raise SestraFailed(
            f"Sestra ({version}, {exe}) produced no result file at {sin_path} for the "
            f"{'stiffened' if stiffened else 'bare'} strip at seed {mesh_size}. Message log: "
            f"{mlg_path if mlg_path.is_file() else '(none written)'}"
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


def default_case_name(mesh_size: float, stiffened: bool) -> str:
    """A deck name that encodes the variant and the seed, so six runs can share a work dir."""
    return "strip_{0}_{1}".format("stf" if stiffened else "bare", str(mesh_size).replace(".", "p"))


def sestra_displacements(
    sin_path: str | pathlib.Path,
    *,
    mesh_size: float,
    stiffened: bool,
    step: int | None = None,
) -> PlateSolve:
    """Read a ``.SIN``, sample it at :data:`plate_model.PROBE_POINTS`, and read its reactions.

    The same ``read_sin_file`` and the same :func:`displacements.sample_fea_result` the frame
    uses, so probe correspondence is solved in one place for both cases and both solvers.

    Worth knowing about the numbers: a ``.SIN`` stores single-precision floats, so the 0.1733 m
    deflection carries about seven significant digits -- an absolute resolution near 1.0e-08 m.
    That is 5x *below* the 4.9e-05 relative residual the finest mesh still has, so it does not
    limit the convergence study; it does set a floor on the Richardson extrapolant, whose
    third-of-a-difference arithmetic lands on the closed form to 7.6e-09 relative -- i.e. right
    at the storage resolution, which is as close as this side can be read.
    """
    sin_path = pathlib.Path(sin_path)
    result = read_sin_file(sin_path, step=step)
    table = sample_fea_result(
        result,
        plate_model.PROBE_POINTS,
        solver="sestra",
        solver_version=get_sestra_version(sestra_exe()),
        model_name=model_name(stiffened),
        load_case=plate_model.LOAD_CASE,
        step=step,
    )
    counts: dict[str, int] = collections.Counter()
    # ``mesh.elements`` is a sequence of per-type blocks whose ``identifiers`` is a numpy array,
    # so no ``or ()`` fallback here: ``array or ()`` raises "truth value of an array ... is
    # ambiguous" rather than defaulting, which is how this line first announced itself.
    for block in getattr(result.mesh, "elements", ()):
        identifiers = getattr(block, "identifiers", None)
        if identifiers is None:
            continue
        # ``ElementBlock.elem_info.type``, not ``block.type``: a block has no ``type`` of its own,
        # and reading one through getattr's default reported every element as "unknown" -- which
        # looked like a report and was a placeholder.
        info = getattr(block, "elem_info", None)
        name = str(getattr(info, "type", "unknown"))
        counts[name] += int(np.asarray(identifiers).size)
    return PlateSolve(
        table=table,
        mesh_size=mesh_size,
        stiffened=stiffened,
        reaction_total=reaction_total(result),
        node_count=int(np.asarray(result.mesh.nodes.identifiers).size),
        element_counts=dict(sorted(counts.items())),
        source=str(sin_path),
    )


def reaction_total(result) -> tuple[float, float, float]:
    """The summed support reaction from a Sestra result, ``(fx, fy, fz)`` newtons.

    Checked against ``-q L b`` by :func:`plate_compare.assert_reaction_total`. It is the one
    scalar that says the *whole* load arrived: a load vector short by one node's tributary area
    changes the mid-span deflection by well under the discretisation residual and would pass
    every displacement comparison here, but it moves this number by that node's share exactly.

    ``RVNODDIS`` is not enough for that and neither is the applied load -- adapy computed the
    applied load itself, so comparing it against itself proves nothing. This is the solver's
    own bookkeeping.
    """
    grouped = result.get_results_grouped_by_field_value()
    if REACTION_FIELD not in grouped:
        raise SestraFailed(
            f"the result carries no {REACTION_FIELD!r} field, so the load Sestra actually reacted "
            f"cannot be read; it holds {sorted(grouped)}. Without it a deck that lost part of its "
            f"load would still produce a full displacement table."
        )
    values = np.asarray(grouped[REACTION_FIELD][0].values, dtype=float)
    # Column 0 is the node label; the next three are X/Y/Z-FORCE.
    total = values[:, 1:4].sum(axis=0)
    return (float(total[0]), float(total[1]), float(total[2]))


def run_and_sample(work_dir: str | pathlib.Path, *, mesh_size: float, stiffened: bool) -> PlateSolve:
    """:func:`run_sestra` then :func:`sestra_displacements`. One variant at one seed."""
    sin_path = run_sestra(work_dir, mesh_size=mesh_size, stiffened=stiffened)
    return sestra_displacements(sin_path, mesh_size=mesh_size, stiffened=stiffened)


def run_sequence(
    work_dir: str | pathlib.Path,
    *,
    stiffened: bool,
    mesh_sizes: tuple[float, ...] = plate_model.MESH_SIZES,
) -> list[PlateSolve]:
    """One variant at every seed, coarse to fine -- the sequence the extrapolation consumes."""
    return [run_and_sample(work_dir, mesh_size=size, stiffened=stiffened) for size in mesh_sizes]
