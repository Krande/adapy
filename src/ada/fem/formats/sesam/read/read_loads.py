"""The Sesam load block on the way in. So far: BNDISPL.

The record is written by ``sesam/write/write_bcs`` and was not read, so a model round-tripped
through a Sesam deck came back with its settlement turned into a rigid clamp. This module is the
way back, and it is deliberately the *inverse* of the writer rather than an independent reading
of the format: every constant it needs
(:data:`~ada.fem.formats.sesam.write.write_bcs.DTYPE_DISPLACEMENT`, ...) is imported from the
module that writes it, so the two cannot drift apart silently.

**BNDISPL** (``LLC DTYPE COMPLX 0`` / ``NODENO NDOF D1 D2`` / ``D3..Dndof``) carries the
*value* of a prescribed displacement; BNBCD FIX code 2 carries *which* DOFs are prescribed.
Measured against Sestra V11.3-00 (``tests/fem/test_sesam_prescribed_displacement.py``), the two
cards are one statement:

* FIX code 2 and a BNDISPL record: the node moves by the value.
* FIX code 2, no BNDISPL: "WARNING ... No load is specified", no displacement result at all.
* a BNDISPL record on a DOF with FIX code 1: the value is ignored, the node does not move.

So the reader pairs them the same way -- a value is read onto a DOF only if BNBCD marked that
DOF prescribed, and each of the other two combinations is reported rather than guessed at.
A prescribed displacement is *loading* in Sesam, so the record names a load case (``LLC``); an
ada ``Bc`` belongs to no load case, so the case name is recorded in the BC's metadata under
:data:`SESAM_LOAD_CASE` -- which is also the writer's own approximation, in reverse (it writes
every settlement into the first case).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.formats import conversion_report
from ada.fem.formats.utils import str_to_int

from ..write.write_bcs import DTYPE_DISPLACEMENT
from . import cards
from .read_sets import text_record

if TYPE_CHECKING:
    from ada.api.nodes import Node

#: The ``stage`` every finding of the Sesam reader is filed under -- the same string
#: ``read_elements`` uses.
STAGE = "sesam reader"

#: COMPLX 0: no phase shift, so no imaginary half follows the real values. It is the only value
#: the writer emits and the only one an ada object can hold: a ``Bc`` magnitude is a single real
#: number.
NO_PHASE = 0

#: ``Bc.metadata`` key holding the name of the load case a BNDISPL record was in. A ``Bc``
#: belongs to no load case in ada, and a Sesam deck keeps a settlement in one, so the name has
#: nowhere else to go; the writer puts every settlement in the *first* case, which is the same
#: gap seen from the other side (``write_loads.step_loads_str``).
SESAM_LOAD_CASE = "sesam_load_case"


def report():
    return conversion_report.current()


def load_case_names(bulk_str: str) -> dict[int, str]:
    """``{LLC: name}`` from the deck's TDLOAD records."""
    names = {}
    for m in cards.re_tdload.finditer(bulk_str):
        d = m.groupdict()
        name, _ = text_record(d, "name")
        if name:
            names[str_to_int(d["llc"])] = name
    return names


def case_name(names: dict[int, str], llc: int) -> str:
    """The load case's name, or ``LC<llc>`` when no TDLOAD named it -- which is also the name
    the writer gives the one case it invents (``write_loads.DEFAULT_CASE`` is ``"LC1"``), so a
    deck written without TDLOAD still round-trips its case name."""
    return names.get(llc) or f"LC{llc}"


# --- BNDISPL: the value of a prescribed displacement ----------------------------------------


def prescribed_displacements(bulk_str: str) -> tuple[dict[int, dict[int, float]], dict[int, str]]:
    """``({node id: {dof: value}}, {node id: load case name})`` from the BNDISPL records.

    Every DOF the record lists is returned, zeros included: which of them is *prescribed* is
    BNBCD's statement, not this one, and a genuinely prescribed zero is a value like any other.
    :func:`prescribed_magnitudes` makes that pairing.

    DTYPE 3 (acceleration) and COMPLX 1 (a phase shift) are refused by name rather than read as
    a displacement: both would put a number that is not a displacement into ``Bc.magnitudes``,
    where every other format writer would then emit it as one.
    """
    names = load_case_names(bulk_str)
    rep = report()
    by_node: dict[int, dict[int, dict[int, float]]] = {}
    for m in cards.re_bndispl.finditer(bulk_str):
        d = m.groupdict()
        llc = str_to_int(d["llc"])
        nodeno = str_to_int(d["nodeno"])
        subject = f"node {nodeno} in load case {case_name(names, llc)}"
        dtype = str_to_int(d["dtype"])
        if dtype != DTYPE_DISPLACEMENT:
            rep.omitted(
                STAGE,
                "BNDISPL",
                subject,
                f"DTYPE {dtype} is not a displacement (Sestra: 1 = displacement, 3 = acceleration), and a "
                "Bc magnitude is a displacement; reading it as one would prescribe a movement nobody asked for",
                dtype=dtype,
            )
            continue
        complx = str_to_int(d["complx"])
        if complx != NO_PHASE:
            rep.omitted(
                STAGE,
                "BNDISPL",
                subject,
                "a complex prescribed displacement (COMPLX 1) carries a phase shift, which a real Bc "
                "magnitude has no room for; its imaginary half would be read as further real dofs",
                complx=complx,
            )
            continue
        ndof = str_to_int(d["ndof"])
        values = [float(x) for x in d["content"].split()[:ndof]]
        if len(values) < ndof:
            rep.omitted(
                STAGE,
                "BNDISPL",
                subject,
                f"the record declares NDOF {ndof} but carries {len(values)} values",
                ndof=ndof,
                n_values=len(values),
            )
            continue
        by_node.setdefault(nodeno, {})[llc] = {dof: value for dof, value in enumerate(values, start=1)}

    prescribed: dict[int, dict[int, float]] = {}
    cases: dict[int, str] = {}
    for nodeno in sorted(by_node):
        per_case = by_node[nodeno]
        llc = min(per_case)
        if len(per_case) > 1:
            # A Bc belongs to no load case, so a node prescribed differently in two of them
            # cannot be held as two BCs either. The first case is the choice, said out loud --
            # the mirror of the writer, which puts every settlement in the first case.
            rep.approximated(
                STAGE,
                "BNDISPL",
                f"node {nodeno}",
                "a displacement is prescribed in several load cases; a Bc belongs to none, so only the "
                "first case's values are read",
                load_cases=[case_name(names, x) for x in sorted(per_case)],
            )
        prescribed[nodeno] = per_case[llc]
        cases[nodeno] = case_name(names, llc)
    return prescribed, cases


def prescribed_magnitudes(
    records: list[tuple[Node, tuple[int, ...], tuple[int, ...]]], bulk_str: str
) -> list[tuple[Node, tuple[int, ...], tuple[float | None, ...], str | None]]:
    """``[(node, dofs, magnitudes, load case)]`` -- the BNBCD records with their BNDISPL values.

    ``records`` is what :func:`~.read_constraints.grab_bc` produces: the node, the DOFs its
    BNBCD record constrains, and the subset of those carrying FIX code 2 (prescribed).
    ``magnitudes`` lines up with ``dofs``, ``None`` on a DOF that is merely fixed.

    The two halves disagreeing is reported rather than resolved silently, in both directions:

    * FIX code 2 with no value for that DOF -- Sestra's "No load is specified", which leaves a
      static run with no displacement result at all. Read as a prescribed **zero**, i.e. the
      support the deck actually behaves as, and reported as a note. (Written back out it
      becomes FIX code 1: ``write_bcs.prescribed_displacements`` keeps only nonzero
      magnitudes, a prescribed zero *being* a fixed support.)
    * a nonzero BNDISPL value on a DOF without code 2 -- measured to be ignored by Sestra, so
      it is ignored here too, and named.
    """
    prescribed, cases = prescribed_displacements(bulk_str)
    rep = report()
    out = []
    for node, dofs, settled in records:
        values = prescribed.get(node.id, {})
        missing = [dof for dof in settled if dof not in values]
        if missing:
            rep.note(
                STAGE,
                "BNDISPL",
                f"node {node.id}",
                "BNBCD prescribes these dofs (FIX code 2) and no BNDISPL record gives them a value, which "
                'Sestra solves as "No load is specified"; they are read as a prescribed zero',
                dofs=missing,
            )
        ignored = sorted(dof for dof, value in values.items() if dof not in settled and value != 0.0)
        if ignored:
            rep.omitted(
                STAGE,
                "BNDISPL",
                f"node {node.id}",
                "a displacement is prescribed on dofs BNBCD does not give FIX code 2, and Sestra ignores "
                "the value there (measured); it is not read",
                dofs=ignored,
                values=[values[dof] for dof in ignored],
            )
        magnitudes = tuple(values.get(dof, 0.0) if dof in settled else None for dof in dofs)
        out.append((node, dofs, magnitudes, cases.get(node.id) if settled else None))
    return out
