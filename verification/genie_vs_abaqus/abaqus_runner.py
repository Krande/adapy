"""The plug point for the Abaqus half. Nothing here runs Abaqus yet -- deliberately.

The CAE writer that would carry this model's supports and loads into an Abaqus CAE model
does not exist at the time of writing, and is being built separately. So rather than a stub
that pretends, this module states the contract precisely and offers the two ways to satisfy
it.

The contract
============

The Abaqus half must produce a :class:`displacements.DisplacementTable` with:

* ``solver = "abaqus"`` and a real ``solver_version``;
* an entry for **every** name in :data:`model.PROBE_POINTS` -- not a subset;
  :func:`compare.assert_same_probes` rejects a partial table, and that is the point;
* six components per probe in the order :data:`displacements.COMPONENTS`, i.e. three
  translations then three rotations, **global axes, metres and radians**. Abaqus reports
  nodal ``U`` in the global system by default; if a local transform is applied at a node,
  convert before filling the table;
* the resolved node id per probe, so a reader can confirm the two solvers used genuinely
  different nodes.

Two ways to satisfy it
======================

**In-process**, once the writer exists: build the model with
:func:`model.build_portal_frame` -- the *same* function, not a reimplementation, that is the
whole premise -- derive the CAE geometry from its concept beams, mesh, solve, read the ODB
into an adapy ``FEAResult``, and hand that to
:func:`displacements.sample_fea_result`. That function is solver-agnostic and already
handles the coordinate matching, so the Abaqus side needs no node-matching code of its own.
See :func:`sample_abaqus_result`.

**Out-of-process**, which also works today: write the table as JSON from wherever the ODB is
readable (Abaqus's own Python 2.7 kernel interpreter, for instance) and pass the file to
:func:`load_abaqus_table` or to ``run_comparison.py --abaqus-json``. The schema is whatever
:meth:`displacements.DisplacementTable.to_json` writes; :func:`write_template_json` emits a
filled-in skeleton to work from.

What the Abaqus side must get right for the comparison to close
===============================================================

Ranked by how badly each one breaks the comparison, worst first:

1. **Seed the mesh so every probe point is a node.** Member ends come free from the
   topology; the mid-span and quarter-point probes need an even number of elements along
   each member. Target size :data:`model.MESH_SIZE` on the 6 m columns and 8 m girder gives
   6 and 8. Call :func:`model.assert_probes_are_seeded` on the meshed FEM, or
   :func:`displacements.sample_fea_result` will raise :class:`displacements.ProbeNotFound` --
   which is the correct outcome, but cheaper to catch before the solve.
2. **Merge the three members at the two top joints.** Two coincident unmerged nodes at a
   corner detach the girder entirely, leaving two cantilevers each carrying ``P/2``: the
   closed form for that is ``P h^3 / (6 E I)`` = 6.347e-02 m, **2.6x** the frame's 2.469e-02 m.
   The sampler reports it as :class:`displacements.AmbiguousProbe` rather than picking one, so
   it cannot pass silently.
3. **The full 10 kN must arrive**, as :data:`model.P_TOTAL` / 2 at each top corner in global
   +X. Check the total applied force in the ``.dat`` or ``.sta`` before trusting anything:
   Sestra's own load summary reported ``tx = 1.0000e+04`` and that is the number to match.
4. **All six DOFs fixed at both bases.** ``ENCASTRE``, not ``PINNED``: releasing just the
   in-plane rotation at both bases takes the sway from 2.469e-02 m to 1.061e-01 m -- **4.3x**,
   measured by running exactly that variant through Sestra, and matching an independent
   pinned-base slope-deflection derivation to 0.3%. It is the single easiest
   support-translation error to make, and the comparison catches it by a factor of four.
   (Releasing all three rotations instead makes the model singular -- Sestra reports "the
   matrix is singular to machine precision", because the columns then have no torsional
   restraint at their bases at all.)
5. **Section and material as the model declares them** -- a 200 mm outer diameter, 10 mm
   wall tube, E = 210 GPa, nu = 0.3. Abaqus's ``PIPE`` section computes its own properties
   from ``(r, t)`` and will differ from adapy's integrated ``I`` in the fifth significant
   digit; that is expected and budgeted (see :mod:`compare`). What is not expected is a
   ``PIPE`` given a radius where a diameter belongs -- a factor of 16 in ``I``.
6. **Beam element type.** ``B31`` (linear Timoshenko) is the like-for-like choice against
   Sestra's ``BEAS``, and 6-8 elements per member puts it inside the 1% budget. ``B33``
   (cubic Euler-Bernoulli) will land ~0.6% *below* Sestra because it has no shear
   flexibility; still inside tolerance, but read the per-point table rather than only the
   verdict. Because the section is a tube, the ``n1`` orientation vector cannot affect this
   model's answer -- which is the one translation risk this first frame deliberately takes
   off the table.
"""

from __future__ import annotations

import pathlib

from . import model
from .displacements import DisplacementTable, sample_fea_result


class AbaqusHalfNotImplemented(NotImplementedError):
    """Raised by :func:`run_and_sample` until the CAE writer carries loads and supports."""


def run_and_sample(work_dir: str | pathlib.Path, *, case_name: str = "portal") -> DisplacementTable:
    """Not implemented. Kept as the named plug point so the CLI has one thing to call.

    When the CAE writer can carry this model's supports and loads, replace the body with:
    build via :func:`model.build_portal_frame`, write the CAE, mesh, solve, read the ODB to
    an adapy ``FEAResult``, and return :func:`sample_abaqus_result` of it. Do not
    reimplement the model here.
    """
    raise AbaqusHalfNotImplemented(
        "the Abaqus half of this comparison is not wired up yet: the CAE writer does not yet "
        "carry boundary conditions and loads, so there is no Abaqus deck to solve. Until it "
        "does, produce a DisplacementTable JSON by any means (see this module's docstring) "
        "and pass it with --abaqus-json. abaqus_runner.write_template_json emits a skeleton."
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
